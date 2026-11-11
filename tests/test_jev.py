import json
from dataclasses import replace

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
import pytest

from wechat_router.jev import JevClient, JevError, build_request, validate_response
from conftest import ready_batch, response


async def test_custom_provider_uses_configured_url_and_key_with_same_protocol(store, cfg):
    cfg = replace(cfg, jev_instructions="只关注本人确认的预约，忽略群内闲聊。")
    req = build_request(json.loads(ready_batch(store, cfg)["snapshot"]))
    async def handler(request):
        assert request.headers["Authorization"] == "Bearer synthetic-custom-key"
        value = await request.json()
        assert value["state"]["routing_policy"] == cfg.jev_instructions
        assert value["model"] == cfg.model
        assert set(value) == {"model", "state", "questions"}
        return web.json_response(response(value))
    app = web.Application()
    app.router.add_post("/custom/v1/systemone", handler)
    async with TestServer(app) as server, ClientSession(trust_env=False) as session:
        client = JevClient(session, "synthetic-custom-key", endpoint=str(server.make_url("/custom/v1/systemone")))
        result = await client.evaluate(req)
        assert validate_response(result, req)["answers"]["needs_followup"]["noul"] == .95


def test_old_batch_keeps_original_default_prompt(store, cfg):
    from wechat_router.config import DEFAULT_JEV_INSTRUCTIONS
    snapshot = json.loads(ready_batch(store, cfg)["snapshot"])
    del snapshot["jev_instructions"]
    assert build_request(snapshot)["state"]["routing_policy"] == DEFAULT_JEV_INSTRUCTIONS


async def test_real_http_protocol_with_local_fixture(store, cfg, monkeypatch):
    batch = ready_batch(store, cfg)
    req = build_request(json.loads(batch["snapshot"]))
    seen = []
    async def handler(request):
        assert request.headers["Authorization"] == "Bearer synthetic-test-key"
        value = await request.json()
        seen.append(value)
        return web.json_response(response(value))
    app = web.Application()
    app.router.add_post("/v1/systemone", handler)
    async with TestServer(app) as server, ClientSession(trust_env=False) as session:
        monkeypatch.setattr(JevClient, "ENDPOINT", str(server.make_url("/v1/systemone")))
        result = await JevClient(session, "synthetic-test-key").evaluate(req)
        assert validate_response(result, req)["answers"]["needs_followup"]["noul"] == .95
        assert seen == [req]


async def test_redirect_never_forwards_key(monkeypatch):
    seen = []
    async def redirect(request):
        return web.Response(status=302, headers={"Location": "/target"})
    async def target(request):
        seen.append(True)
        return web.json_response({})
    app = web.Application()
    app.router.add_post("/v1/systemone", redirect)
    app.router.add_get("/target", target)
    async with TestServer(app) as server, ClientSession(trust_env=False) as session:
        monkeypatch.setattr(JevClient, "ENDPOINT", str(server.make_url("/v1/systemone")))
        with pytest.raises(JevError, match="jev_http_302"):
            await JevClient(session, "synthetic-test-key").evaluate({})
        assert not seen
