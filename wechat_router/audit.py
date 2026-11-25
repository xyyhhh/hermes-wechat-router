"""Private, durable model request/response journal, separate from the inbox schema."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import uuid

_context = ContextVar("wechat_router_audit_context", default={})


@contextmanager
def audit_context(**values):
    token = _context.set(values)
    try:
        yield
    finally:
        _context.reset(token)


class AuditLog:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        os.close(os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o600))
        os.chmod(self.path, 0o600)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS attempts (
                id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
                provider TEXT NOT NULL, endpoint TEXT NOT NULL, model TEXT,
                context_json TEXT NOT NULL, request_json TEXT NOT NULL,
                http_status INTEGER, response_body BLOB, response_bytes INTEGER,
                truncated INTEGER NOT NULL DEFAULT 0, elapsed_ms INTEGER, error_code TEXT
            )""")
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA synchronous=FULL")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def begin(self, request, provider, endpoint, secret):
        attempt_id = "a_" + uuid.uuid4().hex
        def encode(value):
            text = json.dumps(value, ensure_ascii=False)
            return text.replace(secret, "[REDACTED]") if secret else text
        with self.connect() as db:
            db.execute("INSERT INTO attempts(id,started_at,provider,endpoint,model,context_json,request_json) "
                       "VALUES(?,?,?,?,?,?,?)", (attempt_id, datetime.now(timezone.utc).isoformat(), provider,
                       endpoint, request.get("model"), encode(_context.get()), encode(request)))
        return attempt_id

    def finish(self, attempt_id, *, status, body, response_bytes, truncated, elapsed_ms, error_code, secret):
        if secret:
            body = body.replace(secret.encode(), b"[REDACTED]")
        with self.connect() as db:
            db.execute("UPDATE attempts SET finished_at=?,http_status=?,response_body=?,response_bytes=?,"
                       "truncated=?,elapsed_ms=?,error_code=? WHERE id=?", (
                       datetime.now(timezone.utc).isoformat(), status, body, response_bytes, int(truncated),
                       elapsed_ms, error_code, attempt_id))

    @staticmethod
    def summary(path):
        path = Path(path)
        if not path.exists():
            return {"initialized": False, "attempts": 0}
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            row = db.execute("SELECT COUNT(*),COALESCE(SUM(finished_at IS NULL),0),"
                             "COALESCE(SUM(error_code IS NOT NULL),0),MIN(started_at),MAX(started_at) FROM attempts").fetchone()
            return dict(zip(("attempts", "pending", "errors", "first_started_at", "last_started_at"), row),
                        initialized=True, database_bytes=path.stat().st_size)
        finally:
            db.close()


def audit_path(home):
    return Path(home) / "plugin-data" / "wechat-router" / "model-audit.sqlite3"
