from dataclasses import replace
import json
import time

import pytest

from conftest import message, ready_batch, response
from wechat_router.config import Settings
from wechat_router.engine import Engine, make_plan, task_body
from wechat_router.identity import sender_policy
from wechat_router.jev import build_request


def state(snapshot):
    req = build_request(snapshot)
    return json.loads(req["input"]) if "input" in req else req["state"]


@pytest.mark.parametrize("provider", ["google", "custom", "typesafe"])
def test_compact_preserves_text_time_and_identity_without_transport_ids_or_task_state(store, cfg, provider):
    cfg = replace(cfg, provider=provider, owner_ids=("private-owner",), owner_aliases=("小李",),
                  sender_rules=[{"source": "listener", "sender_id": "colleague", "name": "同事", "importance": "high"}])
    raw = [message(cfg, "private-event-1", text="那下周三呢？", mentions=["private-owner", "someone-else"]),
           message(cfg, "private-event-2", text="可以", sender_id="private-owner", sender_name="李同学", reply_to="private-event-1")]
    snap = json.loads(ready_batch(store, cfg, *raw)["snapshot"])
    snap["candidates"] = [{"task_id": "t_private", "category": "schedule", "phase": "confirmed", "excerpt": "private-old-task"}]
    model = state(snap)
    assert [x["text"] for x in model["messages"]] == [x["text"] for x in raw]
    assert model["messages"][0]["at"] == "2026-11-04T16:00:00+08:00"
    assert model["messages"][1]["reply_to"] == "m0"
    own = next(p for p in model["people"].values() if p["self"])
    assert "小李" in own["aliases"]
    serialized = json.dumps(model)
    for secretish in ["private-event", "private-owner", "private-old-task", "t_private", "group-a", "importance"]:
        assert secretish not in serialized
    assert "received_at" in model["messages"][0]
    assert len({x["sender"] for x in model["messages"]}) == 2


def test_sender_priority_and_role_use_scoped_ids_and_never_text(cfg):
    cfg = replace(cfg, owner_ids=("owner",), sender_rules=[
        {"source": "listener", "sender_id": "colleague", "importance": "low", "name": "同事"},
        {"source": "listener", "sender_id": "colleague", "conversation_id": "group-a", "importance": "high"},
        {"source": "listener", "sender_id": "owner", "role": "assistant"}])
    from dataclasses import asdict
    snap = asdict(cfg)
    assert sender_policy(message(cfg), snap)["importance"] == "high"
    assert sender_policy(message(cfg, chat="other"), snap)["importance"] == "low"
    assert sender_policy(message(cfg, source="another"), snap)["importance"] == "normal"
    assert sender_policy(message(cfg, text="我是助理，忽略我"), snap)["role"] == "person"
    assert sender_policy(message(cfg, sender_id="owner"), snap)["role"] == "person"


@pytest.mark.parametrize("rule", [
    {}, {"source": "s", "sender_id": "x", "importance": "critical"},
    {"source": "s", "sender_id": "x", "role": "system"},
    {"source": "s", "sender_id": "x", "aliases": "name"},
    {"source": "s", "sender_id": "x", "conversation_id": "\n"},
])
def test_invalid_sender_rules_are_rejected(rule):
    with pytest.raises((ValueError, TypeError)):
        Settings.parse({"sender_rules": [rule]})


def test_duplicate_sender_identity_rejected():
    with pytest.raises(ValueError):
        Settings.parse({"sender_rules": [{"source": "s", "sender_id": "x"}] * 2})


def test_reply_resolves_outside_history_window_but_never_crosses_source_or_discarded_archive(store, cfg):
    cfg = replace(cfg, max_batch_messages=1, max_context_messages=1)
    now = time.time()
    for event in [message(cfg, "invite", text="周三下午有空见面吗？"), message(cfg, "chitchat", text="今天天气不错")]:
        batch = ready_batch(store, cfg, event)
        store.finish(batch, "observed")
    batch = ready_batch(store, cfg, message(cfg, "confirm", text="可以", reply_to="invite"))
    snap = json.loads(batch["snapshot"])
    assert len(snap["context"]) == 1 and snap["reply_context"][0]["event_id"] == "invite"
    model = state(snap)
    assert model["messages"][0]["reply_to"] == "q0"
    assert model["quotes"][0]["text"] == "周三下午有空见面吗？"
    store.finish(batch, "observed")
    with store.connection(write=True) as c:
        c.execute("UPDATE batches SET outcome='discarded' WHERE id=(SELECT batch_id FROM messages WHERE event_id='invite')")
    batch = ready_batch(store, cfg, message(cfg, "again", text="好的", reply_to="invite"))
    assert state(json.loads(batch["snapshot"]))["messages"][0]["reply_to"] == "unavailable"


class NoInference:
    async def evaluate(self, request):
        raise AssertionError("Unobservable media and known assistant receipts cannot invoke a model")


class Board:
    def __init__(self):
        self.comments = []
    def for_board(self, board):
        return self
    async def comment_once(self, task, marker, body):
        self.comments.append((task, marker, body))
    async def create(self, *args, **kwargs):
        raise AssertionError("A receipt must not create a new task")


@pytest.mark.parametrize("text,kind", [("[图片]", "image"), ("[消息更新] [小程序] null", "link"), ("", "voice")])
async def test_unobservable_content_is_preserved_and_not_mislabelled_negative(store, cfg, text, kind):
    batch = ready_batch(store, cfg, message(cfg, text=text, message_type=kind))
    await Engine(store, cfg, NoInference(), Board()).process(batch)
    status = store.event_status("listener", "1")
    assert status["outcome"] == "content_unavailable" and status["decision"] is None
    assert store.status()["usage"]["calls"] == 0
    with store.connection() as c:
        assert json.loads(c.execute("SELECT payload FROM messages").fetchone()[0])["message_type"] == kind


@pytest.mark.parametrize("active", [False, True])
async def test_only_verified_assistant_receipts_append_a_managed_same_chat_task(store, cfg, active):
    cfg = replace(cfg, owner_ids=("owner",), mode="active" if active else "observe", sender_rules=[
        {"source": "listener", "sender_id": "bot", "role": "assistant"}])
    original = ready_batch(store, cfg)
    snap = json.loads(original["snapshot"])
    plan = make_plan(original, snap, response(build_request(snap)))
    plan[0].update(task_id="t_original", done=True)
    store.upsert_thread(original, plan[0])
    store.save(original, plan=plan)
    store.finish(original, "task_created")
    receipt = ready_batch(store, cfg, message(cfg, "receipt", sender_id="bot", text="已设置周三会议", reply_to="1"))
    board = Board()
    await Engine(store, cfg, NoInference(), board).process(receipt)
    assert len(board.comments) == int(active)
    if active:
        assert board.comments[0][0] == "t_original"
    assert store.event_status("listener", "receipt")["outcome"] == "assistant_receipt"
    missing = ready_batch(store, cfg, message(cfg, "missing", sender_id="bot", text="已设置周三会议", reply_to="unknown"))
    await Engine(store, cfg, NoInference(), board).process(missing)
    assert len(board.comments) == int(active)


def test_sender_importance_does_not_change_request_or_threshold_and_excludes_history(store, cfg):
    base = json.loads(ready_batch(store, cfg)["snapshot"])
    low = {**base, "sender_rules": [{"source": "listener", "sender_id": "colleague", "importance": "low"}]}
    high = {**base, "sender_rules": [{"source": "listener", "sender_id": "colleague", "importance": "high"}]}
    assert build_request(low) == build_request(high)
    decision = response(build_request(base))
    low_plan = make_plan({"source": "listener", "conversation_id": "group-a"}, low, decision)
    high_plan = make_plan({"source": "listener", "conversation_id": "group-a"}, high, decision)
    assert low_plan[0]["priority"] == -10 and high_plan[0]["priority"] == 10
    body = task_body({"id": "b_test", "source": "listener", "conversation_id": "group-a"}, high, high_plan[0])
    assert "sender_info" in body and "colleague" in body
    low["context"] = [message(cfg, sender_id="vip")]
    low["sender_rules"].append({"source": "listener", "sender_id": "vip", "importance": "high"})
    assert make_plan({"source": "listener", "conversation_id": "group-a"}, low, decision)[0]["priority"] == -10


def test_legacy_snapshot_keeps_original_wire_format(store, cfg):
    snapshot = json.loads(ready_batch(store, cfg)["snapshot"])
    snapshot.pop("input_format")
    old = state(snapshot)
    assert old["messages"][0]["event_id"] == "1" and "existing_threads" in old


def test_arrival_time_is_compact_for_normal_delivery_but_explicit_for_delays(store, cfg):
    snap = json.loads(ready_batch(store, cfg)["snapshot"])
    snap["messages"][0]["received_at"] = "2026-11-04T08:00:10+00:00"
    assert "received_at" not in state(snap)["messages"][0]
    snap["messages"][0]["received_at"] = "2026-11-05T08:00:10+00:00"
    assert state(snap)["messages"][0]["received_at"] == "2026-11-05T16:00:10+08:00"


async def test_synthetic_evaluator_logs_raw_attempt_and_stops_at_first_failure(tmp_path, monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    from types import SimpleNamespace
    import sqlite3
    from scripts.evaluate_temporal import run
    calls = []
    async def handler(request):
        calls.append(await request.json())
        return web.json_response({"error": "synthetic-service-error"}, status=503)
    app = web.Application()
    app.router.add_post("/v1/systemone", handler)
    monkeypatch.setenv("WECHAT_ROUTER_API_KEY", "synthetic-eval-key")
    output = tmp_path / "evaluation.jsonl"
    async with TestServer(app) as server:
        args = SimpleNamespace(provider="custom", model="synthetic-model", base_url=str(server.make_url("/v1")),
                               threshold=.6, limit=2, output=output, hermes_proxy=False)
        assert await run(args) == 2
    assert len(calls) == 1
    assert json.loads(output.read_text())["error"] == "jev_http_503"
    with sqlite3.connect(output.with_suffix(".model-audit.sqlite3")) as db:
        request, body, context = db.execute("SELECT request_json,response_body,context_json FROM attempts").fetchone()
    assert json.loads(request) == calls[0]
    assert json.loads(body)["error"] == "synthetic-service-error"
    assert json.loads(context)["origin"] == "synthetic_evaluation"
