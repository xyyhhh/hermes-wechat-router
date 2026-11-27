"""Check the actual batching/context contract, without pretending to test model judgment."""
from dataclasses import replace
import json
import time

import pytest

from conftest import message
from wechat_router.jev import build_request


def state_of(batch):
    request = build_request(json.loads(batch["snapshot"]))
    return request, json.loads(request["input"]) if "input" in request else request["state"]


@pytest.mark.parametrize("provider", ["typesafe", "google"])
@pytest.mark.parametrize("mode", ["bool", "choice"])
def test_cross_batch_reply_has_same_chat_history_without_other_topics_being_merged(store, cfg, provider, mode):
    cfg = replace(cfg, provider=provider, decision_mode=mode, max_batch_messages=2)
    now = time.time()
    events = [message(cfg, "invite", text="@本人 周五下午三点开评审会，可以吗？"),
              message(cfg, "chitchat", text="另外，这个表情包真好笑。"),
              message(cfg, "confirm", text="我能参加", sender_id="owner", is_self=True, reply_to="invite")]
    for event in events:
        store.accept(event, now=now - 2)
    # Identical conversation IDs from another source must not become this batch's context.
    store.accept(message(cfg, "foreign", source="other-listener", text="别的账号的安排"), now=now - 1)
    store.accept(message(cfg, "other-chat", chat="group-b", text="另一个群的安排"), now=now - 1)
    store.materialize(cfg, now=now)
    first = store.claim(cfg, now=now)
    request, state = state_of(first)
    assert [event["text"] for event in state["messages"]] == [events[0]["text"], events[1]["text"]]
    assert set(request["questions"]) == {"needs_followup"}
    store.finish(first, "observed")
    # Finish unrelated batches without inference, then claim the cross-boundary reply.
    store.materialize(cfg, now=now)
    while True:
        batch = store.claim(cfg, now=now)
        assert batch is not None
        if batch["source"] == "listener" and batch["conversation_id"] == "group-a":
            break
        store.finish(batch, "ignored")
    _, state = state_of(batch)
    assert [event["text"] for event in state["messages"]] == ["我能参加"]
    assert state["messages"][0]["reply_to"] == "h0"
    assert [event["text"] for event in state["history"]] == [events[0]["text"], events[1]["text"]]
    assert "existing_threads" not in state


def test_receive_order_and_truncated_history_are_visible_without_invented_reply_text(store, cfg):
    cfg = replace(cfg, max_batch_messages=1, max_context_chars=5)
    now = time.time()
    store.accept(message(cfg, "received-first", text="上文比较长，因此只保留结尾",
                         occurred_at="2026-11-23T13:00:00+08:00"), now=now - 2)
    store.accept(message(cfg, "received-second", text="好的", reply_to="not-in-input",
                         occurred_at="2026-11-23T12:00:00+08:00"), now=now - 1)
    store.materialize(cfg, now=now)
    first = store.claim(cfg, now=now)
    assert state_of(first)[1]["messages"][0]["text"] == "上文比较长，因此只保留结尾"
    store.finish(first, "ignored")
    store.materialize(cfg, now=now)
    _, state = state_of(store.claim(cfg, now=now))
    assert state["messages"][0]["reply_to"] == "unavailable"
    assert state["history"][0]["truncated"] is True
    assert len(state["history"][0]["text"]) == 5
    assert "not-in-input" not in json.dumps(state)


def test_prompt_update_applies_to_new_batch_but_keeps_retry_snapshot(store, cfg):
    now = time.time()
    old = replace(cfg, jev_instructions="原先保存的自定义规则", decision_question="原问题")
    store.accept(message(old, "old"), now=now - 2)
    store.materialize(old, now=now)
    batch = store.claim(old, now=now)
    original_request, _ = state_of(batch)
    store.fail(batch, old, "synthetic_retry")
    retry = store.claim(cfg, now=now + 1)
    assert state_of(retry)[0] == original_request
    store.finish(retry, "observed")
    store.accept(message(cfg, "new"), now=now + 1)
    store.materialize(cfg, now=now + 2)
    request, state = state_of(store.claim(cfg, now=now + 2))
    assert state["routing_policy"] == cfg.jev_instructions
    assert request["questions"]["needs_followup"]["instructions"] == cfg.decision_question
