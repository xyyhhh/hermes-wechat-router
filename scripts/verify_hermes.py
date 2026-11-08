#!/usr/bin/env python3
"""Offline integration: actual Hermes loader, adapter lifecycle and Kanban CLI/DB.

Usage: python scripts/verify_hermes.py --hermes-source <official-checkout>
Requires the plugin's test dependencies and the selected Hermes checkout's import dependencies.
Uses temporary synthetic profiles and a localhost Jev fixture; never calls a hosted model.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hermes-source", required=True, type=Path)
    args = parser.parse_args()
    source = args.hermes_source.resolve()
    plugin = Path(__file__).resolve().parents[1]
    # Hermes owns the top-level `tools` package; don't let the plugin's deferred tools.py shadow it.
    sys.path[:0] = [str(source), str(plugin)]
    with tempfile.TemporaryDirectory(prefix="wechat-router-integration-") as directory:
        home = Path(directory)
        (home / "config.yaml").write_text("{}\n")
        profile = home / "profiles" / "inbox"
        profile.mkdir(parents=True)
        (profile / "config.yaml").write_text("{}\n")
        wrapper = home / "hermes-fixture-cli"
        wrapper.write_text(f"#!{sys.executable}\n" +
                          "import argparse,sys\n"
                          "from hermes_cli.kanban_parser import build_parser\n"
                          "from hermes_cli.kanban import kanban_command\n"
                          "p=argparse.ArgumentParser()\n"
                          "build_parser(p.add_subparsers(dest='command'))\n"
                          "raise SystemExit(kanban_command(p.parse_args()))\n")
        wrapper.chmod(0o700)
        env = {"HERMES_HOME": str(home), "PYTHONPATH": os.pathsep.join((str(source), str(plugin))),
               "WECHAT_ROUTER_SECRET": "synthetic-fixture-secret-" * 3,
               "TYPESAFE_API_KEY": "", "WECHAT_ROUTER_API_KEY": "synthetic-custom-fixture-key"}
        with patch.dict(os.environ, env):
            from hermes_cli.plugin_dev import doctor_plugin
            report = doctor_plugin(plugin)
            assert report.ok, report.format_text()
            assert report.registered_tools == ("wechat_router_status",)
            print("Hermes Plugin Doctor: passed")
            from hermes_cli.plugins import PluginContext, PluginManager
            from hermes_cli.plugins_manifest import PluginManifest
            from wechat_router.plugin import register
            manager = PluginManager(scope_key=str(home))
            manifest = PluginManifest(name="wechat-router", kind="platform", source="user", path=str(plugin))
            register(PluginContext(manifest, manager))
            try:
                verify_dashboard(plugin, home)
                asyncio.run(exercise(home, wrapper))
            finally:
                manager.unload()


def verify_dashboard(plugin, home):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from hermes_cli.config import load_config, save_config
    from hermes_cli.web_server_dashboard import _discover_dashboard_plugins, _mount_one_plugin_api
    from wechat_router.config import data_path

    save_config({"plugins": {"enabled": ["wechat-router"], "isolation": "in_process"},
                 "model": {"default": "fixture-preserve-model"},
                 "platforms": {"wechat_router": {"enabled": True, "extra": {"retry_seconds": 20}}}})
    entry = next(p for p in _discover_dashboard_plugins([(plugin.parent, "user")]) if p["name"] == "wechat-router")
    assert entry["tab"]["path"] == "/wechat-router" and not entry["tab"].get("hidden")
    for key in ("entry", "css", "_api_file"):
        assert (Path(entry["_dir"]) / entry[key]).is_file()
    app = FastAPI()
    assert _mount_one_plugin_api(app, entry, {"wechat-router"}, set())
    with TestClient(app) as client:
        prefix = "/api/plugins/wechat-router"
        response = client.get(prefix + "/config")
        assert response.status_code == 200, response.text
        assert response.json()["settings"]["mode"] == "observe"
        assert "synthetic-fixture-key" not in response.text
        assert client.get(prefix + "/status").json() == {"initialized": False}
        assert not data_path(home).exists()
        response = client.put(prefix + "/config", json={"enabled": True, "settings": {"owner_name": "fixture-owner"}})
        assert response.status_code == 200, response.text
        saved = load_config()
        assert saved["model"]["default"] == "fixture-preserve-model"
        assert saved["platforms"]["wechat_router"]["extra"]["retry_seconds"] == 20
        assert saved["platforms"]["wechat_router"]["extra"]["owner_name"] == "fixture-owner"
        custom = {"provider": "custom", "provider_base_url": "https://provider.example/v1",
                  "jev_instructions": "只关注本人的预约和截止事项。"}
        response = client.put(prefix + "/config", json={"enabled": False, "settings": custom})
        assert response.status_code == 200, response.text
        saved = client.get(prefix + "/config").json()
        assert saved["settings"]["provider_base_url"] == custom["provider_base_url"]
        assert saved["settings"]["jev_instructions"] == custom["jev_instructions"]
        assert "synthetic-custom-fixture-key" not in json.dumps(saved)
    print("Dashboard discovery, real API mount and profile-scoped config persistence: passed")


async def exercise(home, wrapper):
    from aiohttp import ClientSession, web
    from aiohttp.test_utils import TestServer
    from gateway.config import PlatformConfig
    from wechat_router.adapter import WechatRouterAdapter
    from wechat_router.http import PREFIX, signature
    from wechat_router.jev import JevClient
    from wechat_router.kanban import KanbanClient

    requests = []
    async def evaluate(request):
        assert request.headers["Authorization"] == "Bearer synthetic-custom-fixture-key"
        payload = await request.json()
        assert payload["state"]["routing_policy"] == "集成测试自定义判断规则"
        requests.append(payload)
        answers = {}
        for key in payload["questions"]:
            ref, field = key.split("_")
            value = {"action": "act", "category": "schedule", "phase": "proposed",
                     "link": "m0" if ref == "m1" else "new"}[field]
            answers[key] = {"type": "choice", "choice": value, "confidence": .95}
        return web.json_response({"answers": answers, "model": "jev-synthetic-fixture",
                                  "usage": {"input_tokens": 200, "output_tokens": 40}})
    app = web.Application()
    app.router.add_post("/v1/systemone", evaluate)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = PlatformConfig(enabled=True, extra={
        "port": port, "mode": "active", "owner_ids": ["fixture-owner"],
        "quiet_seconds": .1, "max_wait_seconds": .2, "poll_seconds": .01, "hermes_bin": str(wrapper),
    })
    async with TestServer(app) as server, ClientSession(trust_env=False) as session:
        config.extra.update(provider="custom", provider_base_url=str(server.make_url("/v1")),
                            jev_instructions="集成测试自定义判断规则")
        with patch.object(JevClient, "ENDPOINT", str(server.make_url("/v1/systemone"))):
            adapter = WechatRouterAdapter(config)
            try:
                assert await adapter.connect()
                base = f"http://127.0.0.1:{port}"
                for i, text in enumerate(("周五三点开项目会？", "我可以参加")):
                    event = {"source": "fixture-listener", "event_id": str(i), "conversation_id": "fixture-group",
                             "chat_type": "group", "sender_id": "fixture-colleague", "text": text,
                             "occurred_at": "2026-11-04T16:00:00+08:00"}
                    body = json.dumps(event, ensure_ascii=False).encode()
                    stamp = str(int(time.time()))
                    headers = {"Content-Type": "application/json", "X-Wechat-Router-Timestamp": stamp,
                               "X-Wechat-Router-Signature": signature(os.environ["WECHAT_ROUTER_SECRET"], stamp,
                                                                        "POST", PREFIX + "/events", body)}
                    async with session.post(base + PREFIX + "/events", data=body, headers=headers) as result:
                        assert result.status == 202
                path = PREFIX + "/events/status?source=fixture-listener&event_id=0"
                outcome = None
                for _ in range(200):
                    stamp = str(int(time.time()))
                    headers = {"X-Wechat-Router-Timestamp": stamp,
                               "X-Wechat-Router-Signature": signature(os.environ["WECHAT_ROUTER_SECRET"], stamp, "GET", path)}
                    async with session.get(base + path, headers=headers) as result:
                        outcome = await result.json()
                    if outcome["status"] in ("done", "failed"):
                        break
                    await asyncio.sleep(.05)
                assert outcome["status"] == "done", outcome
                assert outcome["outcome"] == "task_created", outcome
                assert len(requests) == 1 and len(requests[0]["state"]["messages"]) == 2
                assert len(outcome["tasks"]) == 1
                board = KanbanClient(adapter.settings, home)
                task = await board.show(outcome["tasks"][0]["task_id"])
                assert task["task"]["assignee"] == "inbox"
                assert task["task"]["status"] == "ready"
                assert "fixture-group" in task["task"]["body"]
                print("HTTP ingress -> batched Jev fixture -> real Hermes Kanban: passed")
            finally:
                await adapter.disconnect()
            assert adapter._worker is None and adapter._runner is None and adapter._session is None
            print("Gateway adapter shutdown: passed")


if __name__ == "__main__":
    main()
