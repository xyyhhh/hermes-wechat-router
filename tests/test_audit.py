import asyncio
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
import pytest

from wechat_router.audit import AuditLog, audit_context
from wechat_router.jev import JevClient, JevError


def rows(log):
    with sqlite3.connect(log.path) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute("SELECT * FROM attempts ORDER BY started_at")]


@pytest.mark.parametrize("status,body,error", [
    (200, b'{"answers":{"gate":{"type":"noul","noul":0.9}},"usage":{"total_input_tokens":123,"total_output_tokens":0}}', None),
    (429, b'{"error":"quota exceeded"}', "jev_http_429"),
    (200, b'not-json', "jev_invalid_json"),
    (200, b'{"answers":{"gate":{"type":"noul","noul":"wrong"}}}', "jev_invalid_noul"),
])
async def test_request_is_durable_before_http_and_raw_response_is_retained(tmp_path, status, body, error):
    log = AuditLog(tmp_path / "private" / "model-audit.sqlite3")
    request_body = {"model": "gemini-decision-1", "input": "synthetic", "questions": {"gate": {"type": "noul"}}}
    async def handler(request):
        saved = rows(log)
        assert len(saved) == 1 and saved[0]["finished_at"] is None
        assert json.loads(saved[0]["request_json"]) == request_body
        assert json.loads(saved[0]["context_json"])["batch_id"] == "b_fixture"
        return web.Response(status=status, body=body)
    app = web.Application(); app.router.add_post("/decisions", handler)
    async with TestServer(app) as server, ClientSession() as session:
        client = JevClient(session, "synthetic-secret-key", provider="google", audit=log,
                           endpoint=str(server.make_url("/decisions")))
        with audit_context(origin="inbox", batch_id="b_fixture", attempt=1):
            if error and error != "jev_invalid_noul":
                with pytest.raises(JevError, match=error):
                    await client.evaluate(request_body)
            else:
                await client.evaluate(request_body)
    saved = rows(log)[0]
    assert saved["response_body"] == body and saved["http_status"] == status
    assert saved["error_code"] == error and saved["finished_at"] and saved["elapsed_ms"] >= 0
    assert log.path.stat().st_mode & 0o777 == 0o600
    assert log.path.parent.stat().st_mode & 0o777 == 0o700


async def test_timeout_retries_remain_distinct_and_key_echo_is_redacted(tmp_path):
    log = AuditLog(tmp_path / "private" / "model-audit.sqlite3")
    count = 0
    async def handler(request):
        nonlocal count
        count += 1
        if count == 1:
            await asyncio.sleep(.1)
        return web.Response(status=401, text="invalid synthetic-secret-key")
    app = web.Application(); app.router.add_post("/decisions", handler)
    async with TestServer(app) as server, ClientSession() as session:
        client = JevClient(session, "synthetic-secret-key", timeout=.01, audit=log,
                           endpoint=str(server.make_url("/decisions")))
        with pytest.raises(JevError, match="jev_timeout"):
            await client.evaluate({"model": "test"})
        client.timeout = 1
        with pytest.raises(JevError, match="jev_http_401"):
            await client.evaluate({"model": "test"})
    saved = rows(log)
    assert len(saved) == 2 and saved[0]["id"] != saved[1]["id"]
    assert saved[0]["error_code"] == "jev_timeout"
    assert saved[1]["response_body"] == b"invalid [REDACTED]"
    assert "synthetic-secret-key" not in str(saved)


async def test_no_model_request_when_audit_cannot_start(tmp_path):
    class Broken:
        def begin(self, *args):
            raise OSError("synthetic disk failure")
    class Session:
        def post(self, *args, **kwargs):
            raise AssertionError("Do not call the provider without a durable request record")
    with pytest.raises(JevError, match="audit_unavailable"):
        await JevClient(Session(), "synthetic-key", audit=Broken()).evaluate({})


def test_jsonl_export_is_private_and_incomplete_attempt_survives(tmp_path):
    log = AuditLog(tmp_path / "private" / "model-audit.sqlite3")
    log.begin({"model": "test", "input": "synthetic"}, "google", "https://example.com", "key")
    assert AuditLog.summary(log.path)["pending"] == 1
    output = tmp_path / "export.jsonl"
    script = Path(__file__).resolve().parents[1] / "scripts/export_audit.py"
    subprocess.run([sys.executable, str(script), "--database", str(log.path), "--output", str(output)], check=True, capture_output=True)
    exported = json.loads(output.read_text())
    assert exported["request"]["input"] == "synthetic" and exported["finished_at"] is None
    assert output.stat().st_mode & 0o777 == 0o600


async def test_cancelled_call_is_logged_and_oversize_response_is_marked(tmp_path):
    log = AuditLog(tmp_path / "private" / "model-audit.sqlite3")
    started = asyncio.Event()
    release = asyncio.Event()
    async def blocked(request):
        started.set()
        await release.wait()
        return web.json_response({})
    async def huge(request):
        return web.Response(body=b"x" * (2 * 1024 * 1024 + 1))
    app = web.Application(); app.router.add_post("/blocked", blocked); app.router.add_post("/huge", huge)
    async with TestServer(app) as server, ClientSession() as session:
        client = JevClient(session, "synthetic-key", audit=log, endpoint=str(server.make_url("/blocked")))
        task = asyncio.create_task(client.evaluate({"model": "test"}))
        await started.wait(); task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        client.endpoint = str(server.make_url("/huge"))
        with pytest.raises(JevError, match="jev_response_too_large"):
            await client.evaluate({"model": "test"})
    saved = rows(log)
    assert saved[0]["error_code"] == "jev_cancelled" and saved[0]["finished_at"]
    assert saved[1]["truncated"] == 1 and len(saved[1]["response_body"]) == 2 * 1024 * 1024
