from dataclasses import replace
import json

import pytest

from conftest import message, ready_batch, response
from wechat_router.config import Settings
from wechat_router.engine import Engine, make_plan
from wechat_router.jev import JevError, build_request, validate_response
from wechat_router.store import LostLease


@pytest.mark.parametrize("mode,kind", [("bool", "noul"), ("choice", "choice")])
def test_exactly_one_event_question_for_a_multi_topic_batch(store, cfg, mode, kind):
    cfg = replace(cfg, decision_mode=mode, decision_question="整批是否值得主助理跟进？")
    batch = ready_batch(store, cfg, message(cfg, "1"), message(cfg, "2", text="另一个截止日期"))
    request = build_request(json.loads(batch["snapshot"]))
    assert set(request["questions"]) == {"needs_followup"}
    assert len(request["state"]["messages"]) == 2
    question = request["questions"]["needs_followup"]
    assert question["type"] == kind and question["instructions"] == cfg.decision_question
    assert question["criteria"] == ({"true": cfg.positive_criteria, "false": cfg.negative_criteria}
                                    if mode == "bool" else cfg.choice_criteria)


@pytest.mark.parametrize("probability,creates_plan", [(0, False), (.59, False), (.6, True), (1, True)])
def test_bool_threshold_controls_one_assistant_handoff(store, cfg, probability, creates_plan):
    cfg = replace(cfg, assistant_profile="main", routes={k: "calendar" for k in cfg.routes})
    batch = ready_batch(store, cfg)
    snapshot = json.loads(batch["snapshot"])
    request = build_request(snapshot)
    raw = {"answers": {"needs_followup": {"type": "noul", "noul": probability}}}
    plan = make_plan(batch, snapshot, validate_response(raw, request))
    assert bool(plan) == creates_plan
    if plan:
        assert len(plan) == 1 and plan[0]["profile"] == "main"


@pytest.mark.parametrize("bad", [
    {"decision_mode": "score"}, {"bool_threshold": True}, {"bool_threshold": 0},
    {"bool_threshold": 1}, {"bool_threshold": float("nan")}, {"decision_question": " "},
    {"positive_criteria": ""}, {"negative_criteria": 1}, {"choice_criteria": {}},
    {"choice_criteria": {"ignore": "a", "watch": "b", "act": ""}}, {"assistant_profile": "../bad"},
])
def test_bad_request_settings_cannot_be_saved(bad):
    with pytest.raises((ValueError, TypeError)):
        Settings.parse(bad)


@pytest.mark.parametrize("answer", [
    {"type": "choice", "choice": "calendar", "confidence": .99},
    {"type": "choice", "choice": "act", "confidence": float("nan")},
    {"type": "choice", "choice": "act", "confidence": True},
])
def test_choice_answer_cannot_select_an_agent(store, cfg, answer):
    batch = ready_batch(store, replace(cfg, decision_mode="choice"))
    request = build_request(json.loads(batch["snapshot"]))
    with pytest.raises(JevError):
        validate_response({"answers": {"needs_followup": answer}}, request)


class Board:
    def __init__(self):
        self.created = []
    def for_board(self, board):
        return self
    async def create(self, effect, body, key, parent=None):
        self.created.append((effect["profile"], parent))
        return "t_fixture"
    async def comment_once(self, *args):
        raise AssertionError("A retargeted legacy plan must not comment on the specialist task")


class NoInference:
    async def evaluate(self, request):
        raise AssertionError("A cached decision must not be inferred again")


@pytest.mark.parametrize("created", [False, True])
async def test_legacy_plan_only_retargets_not_yet_created_tasks(store, cfg, created):
    cfg = replace(cfg, mode="active", assistant_profile="main")
    batch = ready_batch(store, cfg)
    snapshot = json.loads(batch["snapshot"])
    snapshot.pop("decision_mode")
    snapshot.pop("assistant_profile")
    store.upgrade_snapshot(batch, snapshot)
    decision = {"answers": {"m0_action": {"choice": "act", "confidence": .95}}}
    plan = make_plan(batch, snapshot, decision)
    plan[0].update(profile="calendar", previous_task="t_specialist")
    if created:
        plan[0]["task_id"] = "t_existing"
    store.save(batch, decision=decision, plan=plan)
    board = Board()
    await Engine(store, cfg, NoInference(), board).process(batch)
    assert board.created == ([] if created else [("main", None)])
    saved = json.loads(batch["plan"])[0]
    assert saved["profile"] == ("calendar" if created else "main")


async def test_old_undecided_batch_migrates_and_freezes_new_request_fields(store, cfg):
    batch = ready_batch(store, cfg)
    snapshot = json.loads(batch["snapshot"])
    for field in ("decision_mode", "decision_question", "bool_threshold", "positive_criteria",
                  "negative_criteria", "choice_criteria", "assistant_profile"):
        snapshot.pop(field)
    store.upgrade_snapshot(batch, snapshot)
    new = replace(cfg, decision_mode="choice", decision_question="新版事件问题", assistant_profile="main")
    class Capture:
        async def evaluate(self, request):
            question = request["questions"]["needs_followup"]
            assert question["type"] == "choice" and question["instructions"] == new.decision_question
            return response(request)
    await Engine(store, new, Capture(), Board()).process(batch)
    upgraded = json.loads(batch["snapshot"])
    assert upgraded["decision_mode"] == "choice" and upgraded["assistant_profile"] == "main"


def test_snapshot_upgrade_requires_current_lease(store, cfg):
    batch = ready_batch(store, cfg)
    with store.connection(write=True) as connection:
        connection.execute("UPDATE batches SET lease_until=0 WHERE id=?", (batch["id"],))
    recovered = store.claim(cfg)
    with pytest.raises(LostLease):
        store.upgrade_snapshot(batch, {"invalid": "old worker"})
    assert json.loads(recovered["snapshot"])["decision_mode"] == "bool"
