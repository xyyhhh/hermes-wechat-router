import json
from dataclasses import replace

import pytest

from wechat_router.engine import Engine, make_plan
from wechat_router.jev import build_request, validate_response, JevError
from conftest import message, ready_batch, response


class FakeJev:
    def __init__(self, actions=None, links=None):
        self.calls = 0
        self.actions, self.links = actions, links

    async def evaluate(self, request):
        self.calls += 1
        return response(request, self.actions, self.links)


class FakeKanban:
    def __init__(self):
        self.cards, self.comments = {}, {}
        self.fail_after_create = False
        self.previous_status = "running"
        self.requested_boards = []

    def for_board(self, board):
        self.requested_boards.append(board)
        return self

    async def create(self, effect, body, key, parent=None):
        task = self.cards.setdefault(key, {"id": f"t_{len(self.cards)+1}", "parent": parent, "body": body,
                                           "profile": effect["profile"]})
        if self.fail_after_create:
            self.fail_after_create = False
            raise RuntimeError("simulated connection lost after board commit")
        return task["id"]

    async def comment_once(self, task, marker, body):
        self.comments.setdefault(marker, body)
        return {"task": {"id": task, "status": self.previous_status}}


async def test_pending_inference_does_not_move_messages_to_a_different_provider(store, cfg):
    batch = ready_batch(store, cfg)
    changed = replace(cfg, provider="custom", provider_base_url="https://another.example/v1")
    jev, kb = FakeJev(), FakeKanban()
    with pytest.raises(JevError, match="jev_provider_changed"):
        await Engine(store, changed, jev, kb).process(batch)
    assert jev.calls == 0 and not kb.cards


async def test_snapshot_preserves_user_prompt_across_a_retry(store, cfg):
    cfg = replace(cfg, jev_instructions="原先的判断规则")
    batch = ready_batch(store, cfg)
    newer = replace(cfg, jev_instructions="新的判断规则")
    class CaptureJev(FakeJev):
        async def evaluate(self, request):
            assert request["state"]["routing_policy"] == "原先的判断规则"
            return await super().evaluate(request)
    await Engine(store, newer, CaptureJev(), FakeKanban()).process(batch)


async def test_equivalent_full_endpoint_does_not_block_pending_inference(store, cfg):
    cfg = replace(cfg, provider="custom", provider_base_url="https://provider.example/v1")
    batch = ready_batch(store, cfg)
    changed = replace(cfg, provider_base_url="https://provider.example/v1/systemone")
    jev = FakeJev()
    await Engine(store, changed, jev, FakeKanban()).process(batch)
    assert jev.calls == 1


@pytest.mark.parametrize("mode", ["observe", "active"])
async def test_ignore_never_calls_agent_or_board(store, cfg, mode):
    cfg = replace(cfg, mode=mode)
    batch = ready_batch(store, cfg)
    jev, kb = FakeJev({"m0": "ignore"}), FakeKanban()
    await Engine(store, cfg, jev, kb).process(batch)
    assert jev.calls == 1 and not kb.cards


async def test_observe_records_decision_but_no_tasks(store, cfg):
    batch = ready_batch(store, cfg)
    jev, kb = FakeJev(), FakeKanban()
    await Engine(store, cfg, jev, kb).process(batch)
    assert not kb.cards
    assert store.status()["usage"]["input_tokens"] == 100
    assert store.event_status("listener", "1")["outcome"] == "observed"


async def test_one_call_hands_all_topics_to_one_main_assistant(store, cfg):
    cfg = replace(cfg, mode="active")
    batch = ready_batch(store, cfg, message(cfg, "1"), message(cfg, "2", text="好的"),
                        message(cfg, "3", text="另一个事项"))
    jev, kb = FakeJev(links={"m1": "m0"}), FakeKanban()
    await Engine(store, cfg, jev, kb).process(batch)
    assert jev.calls == 1
    assert len(kb.cards) == 1
    card = next(iter(kb.cards.values()))
    assert card["profile"] == "assistant"
    assert "另一个事项" in card["body"] and "好的" in card["body"]


async def test_retry_after_board_commit_does_not_repeat_inference_or_task(store, cfg):
    cfg = replace(cfg, mode="active")
    batch = ready_batch(store, cfg)
    jev, kb = FakeJev(), FakeKanban()
    kb.fail_after_create = True
    engine = Engine(store, cfg, jev, kb)
    with pytest.raises(RuntimeError):
        await engine.process(batch)
    with store.connection(write=True) as c:
        c.execute("UPDATE batches SET lease_until=0 WHERE id=?", (batch["id"],))
    recovered = store.claim(cfg)
    await engine.process(recovered)
    assert jev.calls == 1
    assert len(kb.cards) == 1


async def test_related_tasks_are_context_for_the_main_assistant(store, cfg):
    cfg = replace(cfg, mode="active")
    first = ready_batch(store, cfg)
    jev, kb = FakeJev(), FakeKanban()
    engine = Engine(store, cfg, jev, kb)
    await engine.process(first)
    second = ready_batch(store, cfg, message(cfg, "2", text="改到周六"))
    engine.jev = FakeJev(links={"m0": "t0"})
    await engine.process(second)
    assert len(kb.cards) == 2 and not kb.comments
    last = list(kb.cards.values())[-1]
    assert last["parent"] is None
    assert '"related_threads"' in last["body"] and "t_1" in last["body"]
    assert last["profile"] == "assistant"
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM threads").fetchone()[0] == 2


async def test_watch_is_available_to_later_batch(store, cfg):
    cfg = replace(cfg, decision_mode="choice")
    first = ready_batch(store, cfg)
    await Engine(store, cfg, FakeJev({"m0": "watch"}), FakeKanban()).process(first)
    second = ready_batch(store, cfg, message(cfg, "2", text="周五三点就这么定了"))
    assert len(json.loads(second["snapshot"])["candidates"]) == 1


def test_low_confidence_act_still_goes_to_main_assistant(store, cfg):
    cfg = replace(cfg, decision_mode="choice", assistant_profile="main", triage_profile="triage", routes={k: "specialist" for k in cfg.routes})
    batch = ready_batch(store, cfg)
    snap = json.loads(batch["snapshot"])
    req = build_request(snap)
    result = response(req)
    result["answers"]["needs_followup"]["confidence"] = .2
    plan = make_plan(batch, snap, validate_response(result, req))
    assert plan[0]["profile"] == "main" and plan[0]["action"] == "review"


@pytest.mark.parametrize("value", [True, float("nan"), -0.1, 1.1, "yes"])
def test_invalid_noul_is_not_a_decision(store, cfg, value):
    batch = ready_batch(store, cfg)
    req = build_request(json.loads(batch["snapshot"]))
    result = response(req)
    result["answers"]["needs_followup"]["noul"] = value
    with pytest.raises(JevError):
        validate_response(result, req)


async def test_failed_batch_does_not_disappear(store, cfg):
    cfg = replace(cfg, max_attempts=1)
    import time
    store.accept(message(cfg), now=time.time() - 2)
    class BrokenJev:
        async def evaluate(self, request):
            raise JevError("jev_http_429")
    await Engine(store, cfg, BrokenJev(), FakeKanban()).tick()
    assert store.status()["failures"][0]["error"] == "jev_http_429"


async def test_switch_to_observe_suppresses_pending_active_effects(store, cfg):
    active = replace(cfg, mode="active")
    batch = ready_batch(store, active)
    kb = FakeKanban()
    await Engine(store, cfg, FakeJev(), kb).process(batch)
    assert not kb.cards
    assert store.event_status("listener", "1")["outcome"] == "observed"


async def test_old_observe_batch_does_not_become_active_retroactively(store, cfg):
    batch = ready_batch(store, cfg)
    kb = FakeKanban()
    await Engine(store, replace(cfg, mode="active"), FakeJev(), kb).process(batch)
    assert not kb.cards


async def test_board_change_does_not_carry_foreign_task_ids(store, cfg):
    old = replace(cfg, mode="active")
    first = ready_batch(store, old)
    await Engine(store, old, FakeJev(), FakeKanban()).process(first)
    new = replace(cfg, board="another", decision_mode="choice")
    second = ready_batch(store, new, message(cfg, "2"))
    await Engine(store, new, FakeJev({"m0": "watch"}, {"m0": "t0"}), FakeKanban()).process(second)
    with store.connection() as c:
        row = c.execute("SELECT task_id,board FROM threads ORDER BY updated_at DESC LIMIT 1").fetchone()
    assert row["board"] == "another" and row["task_id"] is None


async def test_retry_pins_original_board_after_config_change(store, cfg):
    batch = ready_batch(store, replace(cfg, mode="active", board="original"))
    kb = FakeKanban()
    await Engine(store, replace(cfg, mode="active", board="new"), FakeJev(), kb).process(batch)
    assert kb.requested_boards == ["original"]
