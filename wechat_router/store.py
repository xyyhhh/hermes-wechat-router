"""Durable message inbox and fenced batch leases. Never writes Hermes databases."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from .events import digest, encode


class EventConflict(ValueError):
    pass


class LostLease(RuntimeError):
    pass


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        with self.connection() as c:
            version = c.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("router state is from a newer schema")
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript("""
                CREATE TABLE IF NOT EXISTS messages (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL, event_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL, received_at REAL NOT NULL,
                    fingerprint TEXT NOT NULL, payload TEXT NOT NULL, batch_id TEXT,
                    UNIQUE(source, event_id)
                );
                CREATE INDEX IF NOT EXISTS pending_messages
                    ON messages(source, conversation_id, batch_id, seq);
                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, conversation_id TEXT NOT NULL,
                    created_at REAL NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
                    attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL DEFAULT 0,
                    lease TEXT, lease_until REAL, snapshot TEXT, decision TEXT, plan TEXT,
                    outcome TEXT, error TEXT
                );
                CREATE INDEX IF NOT EXISTS due_batches ON batches(status, next_at, created_at);
                CREATE TABLE IF NOT EXISTS threads (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, conversation_id TEXT NOT NULL,
                    category TEXT NOT NULL, phase TEXT NOT NULL, excerpt TEXT NOT NULL,
                    updated_at REAL NOT NULL, task_id TEXT, board TEXT, profile TEXT
                );
                CREATE TABLE IF NOT EXISTS calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL,
                    created_at REAL NOT NULL, model TEXT, input_tokens INTEGER,
                    output_tokens INTEGER
                );
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                PRAGMA user_version=1;
            """)
        os.chmod(self.path, 0o600)

    @contextmanager
    def connection(self, write=False):
        c = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=5000")
        c.execute("PRAGMA synchronous=FULL")
        try:
            if write:
                c.execute("BEGIN IMMEDIATE")
            yield c
            if write:
                c.commit()
        except BaseException:
            if write:
                c.rollback()
            raise
        finally:
            c.close()

    def accept(self, event, now=None):
        now = time.time() if now is None else now
        fingerprint = digest(event)
        with self.connection(write=True) as c:
            old = c.execute("SELECT fingerprint FROM messages WHERE source=? AND event_id=?",
                            (event["source"], event["event_id"])).fetchone()
            if old:
                if old[0] != fingerprint:
                    raise EventConflict("event id already has different content")
                return False
            c.execute("INSERT INTO messages(source,event_id,conversation_id,received_at,fingerprint,payload) "
                      "VALUES(?,?,?,?,?,?)", (event["source"], event["event_id"], event["conversation_id"],
                                              now, fingerprint, encode(event)))
            return True

    def event_status(self, source, event_id):
        with self.connection() as c:
            r = c.execute("SELECT m.event_id,m.batch_id,b.status,b.outcome,b.error,b.plan,b.decision "
                          "FROM messages m LEFT JOIN batches b ON b.id=m.batch_id "
                          "WHERE m.source=? AND m.event_id=?", (source, event_id)).fetchone()
            if r is None:
                return None
            out = dict(r)
            plan = json.loads(out.pop("plan") or "[]")
            out["decision"] = json.loads(out["decision"]) if out["decision"] else None
            out["status"] = out["status"] or "buffered"
            out["tasks"] = [dict(thread_id=x["thread_id"], task_id=x["task_id"], board=x["board"])
                            for x in plan if x.get("task_id")]
            return out

    def materialize(self, cfg, now=None):
        now = time.time() if now is None else now
        made = []
        with self.connection(write=True) as c:
            chats = c.execute("SELECT source,conversation_id,MIN(received_at) first,MAX(received_at) last,"
                              "COUNT(*) n FROM messages WHERE batch_id IS NULL GROUP BY source,conversation_id "
                              "ORDER BY first LIMIT 100").fetchall()
            for chat in chats:
                active = c.execute("SELECT 1 FROM batches WHERE source=? AND conversation_id=? "
                                   "AND status IN ('queued','processing') LIMIT 1", tuple(chat)[:2]).fetchone()
                if active:
                    continue
                if (now < min(chat["last"] + cfg.quiet_seconds, chat["first"] + cfg.max_wait_seconds)
                        and chat["n"] < cfg.max_batch_messages):
                    continue
                rows = c.execute("SELECT seq,payload FROM messages WHERE source=? AND conversation_id=? "
                                 "AND batch_id IS NULL ORDER BY seq LIMIT ?",
                                 (chat["source"], chat["conversation_id"], cfg.max_batch_messages)).fetchall()
                chosen, chars = [], 0
                for row in rows:
                    size = len(json.loads(row["payload"])["text"])
                    if chosen and chars + size > cfg.max_batch_chars:
                        break
                    chars += size
                    chosen.append(row["seq"])
                batch_id = "b_" + uuid.uuid4().hex
                c.execute("INSERT INTO batches(id,source,conversation_id,created_at) VALUES(?,?,?,?)",
                          (batch_id, chat["source"], chat["conversation_id"], now))
                c.executemany("UPDATE messages SET batch_id=? WHERE seq=?", [(batch_id, x) for x in chosen])
                made.append(batch_id)
        return made

    def claim(self, cfg, now=None):
        now = time.time() if now is None else now
        with self.connection(write=True) as c:
            row = c.execute("SELECT * FROM batches WHERE (status='queued' AND next_at<=?) "
                            "OR (status='processing' AND lease_until<=?) ORDER BY created_at LIMIT 1",
                            (now, now)).fetchone()
            if not row:
                return None
            batch = dict(row)
            if batch["attempts"] >= cfg.max_attempts:
                c.execute("UPDATE batches SET status='failed',error='worker_recovery_limit',lease=NULL,"
                          "lease_until=NULL WHERE id=?", (batch["id"],))
                return None
            token = uuid.uuid4().hex
            c.execute("UPDATE batches SET status='processing',lease=?,lease_until=?,attempts=attempts+1 "
                      "WHERE id=?", (token, now + cfg.lease_seconds, batch["id"]))
            if not batch["snapshot"]:
                rows = c.execute("SELECT seq,payload FROM messages WHERE batch_id=? ORDER BY seq", (batch["id"],)).fetchall()
                current = [json.loads(x["payload"]) for x in rows]
                history = c.execute("SELECT payload FROM messages WHERE source=? AND conversation_id=? "
                                    "AND seq<? AND received_at>=? ORDER BY seq DESC LIMIT ?",
                                    (batch["source"], batch["conversation_id"], rows[0]["seq"],
                                     now - cfg.context_hours * 3600, cfg.max_context_messages)).fetchall()
                context, count = [], 0
                for x in history:
                    message = json.loads(x[0])
                    remaining = cfg.max_context_chars - count
                    if remaining <= 0:
                        break
                    if len(message["text"]) > remaining:
                        message["text"] = message["text"][-remaining:]
                        message["truncated"] = True
                    context.append(message)
                    count += len(message["text"])
                candidates = [dict(x) for x in c.execute(
                    "SELECT * FROM threads WHERE source=? AND conversation_id=? AND updated_at>=? "
                    "ORDER BY updated_at DESC LIMIT ?", (batch["source"], batch["conversation_id"],
                                                          now - cfg.candidate_days * 86400, cfg.max_candidates))]
                snapshot = {"messages": current, "context": list(reversed(context)), "candidates": candidates,
                            "mode": cfg.mode, "routes": cfg.routes, "triage_profile": cfg.triage_profile,
                            "board": cfg.board, "min_confidence": cfg.min_confidence,
                            "owner_name": cfg.owner_name, "owner_ids": list(cfg.owner_ids),
                            "interests": list(cfg.interests), "timezone": cfg.timezone, "model": cfg.model,
                            "provider": cfg.provider, "provider_base_url": cfg.provider_base_url,
                            "provider_endpoint": cfg.provider_endpoint,
                            "jev_instructions": cfg.jev_instructions}
                batch["snapshot"] = encode(snapshot)
                c.execute("UPDATE batches SET snapshot=? WHERE id=?", (batch["snapshot"], batch["id"]))
            batch.update(lease=token, status="processing", attempts=batch["attempts"] + 1)
            return batch

    @staticmethod
    def guard(c, batch):
        row = c.execute("SELECT lease,status,lease_until FROM batches WHERE id=?", (batch["id"],)).fetchone()
        if not row or row["lease"] != batch["lease"] or row["status"] != "processing" or row["lease_until"] <= time.time():
            raise LostLease("batch lease lost")

    def renew(self, batch, seconds):
        with self.connection(write=True) as c:
            self.guard(c, batch)
            c.execute("UPDATE batches SET lease_until=? WHERE id=?", (time.time() + seconds, batch["id"]))

    def save(self, batch, **changes):
        if not set(changes) <= {"decision", "plan", "outcome"}:
            raise ValueError("invalid batch update")
        with self.connection(write=True) as c:
            self.guard(c, batch)
            for key, value in changes.items():
                c.execute(f"UPDATE batches SET {key}=? WHERE id=?", (encode(value), batch["id"]))
                batch[key] = encode(value)

    def record_call(self, batch, response):
        usage = response.get("usage", {})
        def count(key):
            value = usage.get(key) if isinstance(usage, dict) else None
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
        with self.connection(write=True) as c:
            self.guard(c, batch)
            model = response.get("model")
            c.execute("INSERT INTO calls(batch_id,created_at,model,input_tokens,output_tokens) VALUES(?,?,?,?,?)",
                      (batch["id"], time.time(), model[:100] if isinstance(model, str) else None,
                       count("input_tokens"), count("output_tokens")))

    def upsert_thread(self, batch, effect):
        with self.connection(write=True) as c:
            self.guard(c, batch)
            c.execute("INSERT INTO threads(id,source,conversation_id,category,phase,excerpt,updated_at,task_id,board,profile) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET category=excluded.category,"
                      "phase=excluded.phase,excerpt=excluded.excerpt,updated_at=excluded.updated_at,"
                      "task_id=CASE WHEN threads.board=excluded.board THEN COALESCE(excluded.task_id,threads.task_id) "
                      "ELSE excluded.task_id END,board=excluded.board,profile=excluded.profile",
                      (effect["thread_id"], batch["source"], batch["conversation_id"], effect["category"],
                       effect["phase"], effect["excerpt"], time.time(), effect.get("task_id"), effect["board"], effect["profile"]))

    def finish(self, batch, outcome):
        with self.connection(write=True) as c:
            self.guard(c, batch)
            c.execute("UPDATE batches SET status='done',outcome=?,lease=NULL,lease_until=NULL,error=NULL WHERE id=?",
                      (outcome, batch["id"]))

    def fail(self, batch, cfg, code):
        with self.connection(write=True) as c:
            self.guard(c, batch)
            status = "failed" if batch["attempts"] >= cfg.max_attempts else "queued"
            delay = cfg.retry_seconds * 2 ** min(batch["attempts"] - 1, 8)
            c.execute("UPDATE batches SET status=?,next_at=?,error=?,lease=NULL,lease_until=NULL WHERE id=?",
                      (status, time.time() + delay, code, batch["id"]))

    def retry(self, batch_id):
        with self.connection(write=True) as c:
            return c.execute("UPDATE batches SET status='queued',attempts=0,next_at=0,error=NULL "
                             "WHERE id=? AND status='failed'", (batch_id,)).rowcount == 1

    def paused(self):
        with self.connection() as c:
            row = c.execute("SELECT value FROM settings WHERE key='paused'").fetchone()
            return bool(row and row[0] == "true")

    def pause(self, paused):
        with self.connection(write=True) as c:
            c.execute("INSERT INTO settings(key,value) VALUES('paused',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      ("true" if paused else "false",))

    def status(self):
        with self.connection() as c:
            return {"paused": self.paused(),
                    "messages": c.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
                    "buffered": c.execute("SELECT COUNT(*) FROM messages WHERE batch_id IS NULL").fetchone()[0],
                    "batches": dict(c.execute("SELECT status,COUNT(*) FROM batches GROUP BY status").fetchall()),
                    "usage": dict(c.execute("SELECT COUNT(*) calls,COALESCE(SUM(input_tokens),0) input_tokens,"
                                            "COALESCE(SUM(output_tokens),0) output_tokens,"
                                            "COALESCE(SUM(CASE WHEN input_tokens IS NULL THEN 1 ELSE 0 END),0) calls_without_usage FROM calls").fetchone()),
                    "failures": [dict(x) for x in c.execute("SELECT id,error,attempts FROM batches WHERE status='failed' "
                                                          "ORDER BY created_at DESC LIMIT 20")]}
