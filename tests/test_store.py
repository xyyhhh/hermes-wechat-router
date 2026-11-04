from dataclasses import replace
import time

import pytest

from wechat_router.events import InvalidEvent
from wechat_router.store import EventConflict, LostLease, Store
from conftest import message, ready_batch


def test_duplicate_and_conflict_are_durable(store, cfg):
    m = message(cfg)
    assert store.accept(m)
    reopened = Store(store.path)
    assert not reopened.accept(m)
    with pytest.raises(EventConflict):
        reopened.accept({**m, "text": "不同内容"})
    assert reopened.accept({**m, "source": "another-listener"})


def test_chat_and_source_isolation(store, cfg):
    store.accept(message(cfg, chat="one"), now=0)
    store.accept(message(cfg, event_id="2", chat="two"), now=0)
    assert len(store.materialize(cfg, now=1)) == 2


def test_quiet_window_and_max_wait(store, cfg):
    cfg = replace(cfg, quiet_seconds=8, max_wait_seconds=60)
    store.accept(message(cfg), now=0)
    store.accept(message(cfg, "2"), now=7)
    assert not store.materialize(cfg, now=10)
    assert store.materialize(cfg, now=15)


def test_busy_chat_cannot_postpone_forever(store, cfg):
    cfg = replace(cfg, quiet_seconds=8, max_wait_seconds=60)
    for i in range(10):
        store.accept(message(cfg, str(i)), now=i * 6)
    assert store.materialize(cfg, now=60)


def test_size_limit_splits_backlog_without_losing_messages(store, cfg):
    cfg = replace(cfg, max_batch_messages=2)
    for i in range(5):
        store.accept(message(cfg, str(i)), now=0)
    assert len(store.materialize(cfg, now=1)) == 1
    assert store.status()["buffered"] == 3
    assert not store.materialize(cfg, now=2)  # serial per conversation, including queued retries


def test_crash_lease_recovery_fences_old_worker(store, cfg):
    batch = ready_batch(store, cfg)
    with store.connection(write=True) as c:
        c.execute("UPDATE batches SET lease_until=0 WHERE id=?", (batch["id"],))
    recovered = store.claim(cfg)
    assert recovered["id"] == batch["id"]
    assert recovered["lease"] != batch["lease"]
    with pytest.raises(LostLease):
        store.finish(batch, "ignored")
    store.finish(recovered, "ignored")


def test_pause_is_persistent(store):
    store.pause(True)
    assert Store(store.path).paused()
    store.pause(False)
    assert not store.paused()


def test_repeated_worker_crashes_eventually_stop(store, cfg):
    batch = ready_batch(store, replace(cfg, max_attempts=1))
    with store.connection(write=True) as c:
        c.execute("UPDATE batches SET lease_until=0")
    assert store.claim(replace(cfg, max_attempts=1)) is None
    assert store.event_status("listener", "1")["error"] == "worker_recovery_limit"


def test_nontext_message_is_kept_as_unread_content(cfg):
    event = message(cfg, message_type="image", text="")
    assert event["message_type"] == "image"
    assert "未提供文本" in event["text"]


def test_failure_retry_preserves_decision(store, cfg):
    batch = ready_batch(store, cfg)
    store.save(batch, decision={"answers": {}})
    store.fail(batch, replace(cfg, max_attempts=1), "example_error")
    assert store.event_status("listener", "1")["status"] == "failed"
    assert store.retry(batch["id"])
    assert store.claim(cfg)["decision"] == batch["decision"]


def test_snapshot_context_is_bounded_and_excludes_future_messages(store, cfg):
    cfg = replace(cfg, max_batch_messages=1, max_context_chars=10)
    for i in range(3):
        store.accept(message(cfg, str(i), text="长上下文" * 10), now=time.time() - 2)
    store.materialize(cfg)
    first = store.claim(cfg)
    store.finish(first, "ignored")
    store.materialize(cfg)
    second = store.claim(cfg)
    import json
    snap = json.loads(second["snapshot"])
    assert sum(len(x["text"]) for x in snap["context"]) == 10
    assert [x["event_id"] for x in snap["context"]] == ["0"]
    assert [x["event_id"] for x in snap["messages"]] == ["1"]


@pytest.mark.parametrize("change", [{"chat_type": "unknown"}, {"is_self": "false"},
                                  {"occurred_at": "2026-11-04T00:00:00"}, {"mentions": "owner"},
                                  {"text": ""}, {"event_id": "bad\nkey"}])
def test_event_validation(cfg, change):
    with pytest.raises(InvalidEvent):
        message(cfg, **change)
