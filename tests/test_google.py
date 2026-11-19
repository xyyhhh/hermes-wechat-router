"""Real HTTP fixtures for Google's native Decisions wire protocol."""
from dataclasses import replace
import json

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
import pytest

from conftest import ready_batch, response
from wechat_router.config import GOOGLE_ENDPOINT, GOOGLE_MODEL, Settings
from wechat_router.engine import Engine
from wechat_router.jev import JevClient, JevError, build_request, validate_response


def test_google_uses_fixed_endpoint_and_own_key():
    cfg = Settings.parse({"provider": "google", "provider_base_url": "https://old.example/v1"})
    assert cfg.provider_endpoint == GOOGLE_ENDPOINT and cfg.provider_key_name == "GEMINI_API_KEY"
    assert cfg.model == GOOGLE_MODEL
    assert Settings.parse({"provider": "google", "model": "future-id"}).model == "future-id"


@pytest.mark.parametrize("mode", ["bool", "choice"])
async def test_google_native_http_and_usage_reaches_main_assistant(store, cfg, mode):
    cfg = replace(cfg, provider="google", model=GOOGLE_MODEL, decision_mode=mode, mode="active")
    batch = ready_batch(store, cfg)
    request_body = build_request(json.loads(batch["snapshot"]))
    assert set(request_body) == {"model", "input", "questions"}
    state = json.loads(request_body["input"])
    assert state["messages"] and state["routing_policy"] == cfg.jev_instructions
    seen = []

    async def handler(request):
        assert request.headers["x-goog-api-key"] == "synthetic-google-key"
        assert "Authorization" not in request.headers and not request.query
        payload = await request.json()
        assert payload == request_body
        seen.append(payload)
        result = response(payload)
        result.pop("model", None)
        result["usage"] = {"total_input_tokens": 123, "total_output_tokens": 0}
        return web.json_response(result)

    class Board:
        created = []
        def for_board(self, board):
            return self
        async def create(self, effect, body, key, parent=None):
            self.created.append(effect)
            return "synthetic-main-task"

    app = web.Application()
    app.router.add_post("/v1beta/decisions", handler)
    async with TestServer(app) as server, ClientSession(trust_env=False) as session:
        client = JevClient(session, "synthetic-google-key", provider="google",
                           endpoint=str(server.make_url("/v1beta/decisions")))
        board = Board()
        await Engine(store, cfg, client, board).process(batch)
    assert len(seen) == 1 and len(board.created) == 1
    assert board.created[0]["profile"] == cfg.assistant_profile
    usage = store.status()["usage"]
    assert usage["calls"] == 1 and usage["input_tokens"] == 123 and usage["output_tokens"] == 0
    with store.connection() as connection:
        assert connection.execute("SELECT model FROM calls").fetchone()[0] == GOOGLE_MODEL


@pytest.mark.parametrize("status", [302, 401, 429, 503])
async def test_google_errors_do_not_follow_redirects_or_fallback(status):
    seen = []
    async def handler(request):
        seen.append(request.path)
        return web.Response(status=status, headers={"Location": "/target"}, text="private provider detail")
    async def target(request):
        raise AssertionError("Do not forward a Google key")
    app = web.Application()
    app.router.add_post("/v1beta/decisions", handler)
    app.router.add_get("/target", target)
    async with TestServer(app) as server, ClientSession(trust_env=False) as session:
        client = JevClient(session, "synthetic-google-key", provider="google",
                           endpoint=str(server.make_url("/v1beta/decisions")))
        with pytest.raises(JevError, match=f"^jev_http_{status}$"):
            await client.evaluate({"model": GOOGLE_MODEL})
    assert seen == ["/v1beta/decisions"]


async def test_google_missing_usage_is_unknown_not_zero(store, cfg):
    request_body = build_request(json.loads(ready_batch(store, replace(cfg, provider="google"))["snapshot"]))
    async def handler(request):
        return web.json_response({"answers": {"needs_followup": {"type": "noul", "noul": .8}}})
    app = web.Application()
    app.router.add_post("/v1beta/decisions", handler)
    async with TestServer(app) as server, ClientSession(trust_env=False) as session:
        client = JevClient(session, "synthetic-google-key", provider="google",
                           endpoint=str(server.make_url("/v1beta/decisions")))
        result = validate_response(await client.evaluate(request_body), request_body)
    assert result["usage"] == {}
