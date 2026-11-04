import json
import time
from dataclasses import replace

from aiohttp.test_utils import TestClient, TestServer
import pytest

from wechat_router.http import make_app, PREFIX, signature
from conftest import message

SECRET = "synthetic-test-secret-" * 3


def signed(path, body=b"", method="POST", stamp=None):
    timestamp = str(int(time.time())) if stamp is None else str(stamp)
    return {"Content-Type": "application/json", "X-Wechat-Router-Timestamp": timestamp,
            "X-Wechat-Router-Signature": signature(SECRET, timestamp, method, path, body)}


async def test_ingress_ack_duplicates_conflicts_and_status(store, cfg):
    async with TestClient(TestServer(make_app(store, cfg, SECRET))) as client:
        path = PREFIX + "/events"
        body = json.dumps(message(cfg), ensure_ascii=False).encode()
        res = await client.post(path, data=body, headers=signed(path, body))
        assert res.status == 202
        assert (await res.json())["status"] == "buffered"
        assert store.status()["messages"] == 1
        res = await client.post(path, data=body, headers=signed(path, body))
        assert res.status == 200 and (await res.json())["duplicate"]
        changed = json.dumps(message(cfg, text="另一个消息")).encode()
        res = await client.post(path, data=changed, headers=signed(path, changed))
        assert res.status == 409
        path = PREFIX + "/events/status?source=listener&event_id=1"
        res = await client.get(path, headers=signed(path, method="GET"))
        assert res.status == 200
        assert "text" not in await res.json()


async def test_tamper_replay_and_unsigned_requests_rejected(store, cfg):
    async with TestClient(TestServer(make_app(store, cfg, SECRET))) as client:
        path = PREFIX + "/events"
        body = json.dumps(message(cfg)).encode()
        assert (await client.post(path, data=body)).status == 401
        assert (await client.post(path, data=body + b" ", headers=signed(path, body))).status == 401
        assert (await client.post(path, data=body, headers=signed(path, body, stamp=0))).status == 401
        assert store.status()["messages"] == 0


async def test_database_failure_returns_503_not_false_ack(store, cfg, monkeypatch):
    def fail(*args):
        raise OSError("synthetic failure, not an accepted event")
    monkeypatch.setattr(store, "accept", fail)
    async with TestClient(TestServer(make_app(store, cfg, SECRET))) as client:
        path = PREFIX + "/events"
        body = json.dumps(message(cfg)).encode()
        result = await client.post(path, data=body, headers=signed(path, body))
        assert result.status == 503
        assert await result.json() == {"error": "inbox_unavailable"}


async def test_oversized_request_rejected(store, cfg):
    cfg = replace(cfg, max_body_bytes=100)
    async with TestClient(TestServer(make_app(store, cfg, SECRET))) as client:
        path = PREFIX + "/events"
        body = b"x" * 101
        assert (await client.post(path, data=body, headers=signed(path, body))).status == 413


async def test_health_exposes_no_queue_or_messages(store, cfg):
    async with TestClient(TestServer(make_app(store, cfg, SECRET))) as client:
        result = await client.get(PREFIX + "/health")
        assert result.status == 200
        assert set(await result.json()) == {"ok", "plugin", "version"}


def test_too_short_secret_prevents_start(store, cfg):
    with pytest.raises(ValueError):
        make_app(store, cfg, "short")
