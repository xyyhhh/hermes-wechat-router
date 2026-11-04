from dataclasses import replace
import time

import pytest

from wechat_router.config import Settings
from wechat_router.events import normalize_event
from wechat_router.store import Store


@pytest.fixture
def cfg():
    return replace(Settings(), quiet_seconds=0.01, max_wait_seconds=0.1, retry_seconds=0.01)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "router" / "router.sqlite3")


def message(cfg, event_id="1", chat="group-a", text="周五下午三点开会", **kwargs):
    return normalize_event({"source": "listener", "event_id": event_id, "conversation_id": chat,
                            "sender_id": "colleague", "chat_type": "group", "text": text,
                            "occurred_at": "2026-11-04T16:00:00+08:00", **kwargs}, cfg)


def ready_batch(store, cfg, *messages):
    for m in messages or (message(cfg),):
        store.accept(m, now=time.time() - 2)
    store.materialize(cfg)
    return store.claim(cfg)


def response(request, actions=None, links=None):
    out = {"answers": {}, "model": "jev-test-fixture", "usage": {"input_tokens": 100, "output_tokens": 20}}
    for key, q in request["questions"].items():
        i, kind = key.split("_")
        defaults = {"action": (actions or {}).get(i, "act"), "category": "schedule",
                    "link": (links or {}).get(i, "new"), "phase": "proposed"}
        out["answers"][key] = {"type": "choice", "choice": defaults[kind], "confidence": 0.95}
    return out
