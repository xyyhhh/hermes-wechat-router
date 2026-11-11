import copy
import importlib.util
from pathlib import Path
import sys
from types import ModuleType

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from wechat_router import dashboard
from wechat_router.config import data_path
from wechat_router.store import Store


@pytest.fixture
def ui(tmp_path, monkeypatch):
    config = {"model": {"default": "keep-me"}, "platforms": {"wechat_router": {
        "enabled": True, "extra": {"hermes_bin": "/existing/hermes", "retry_seconds": 20}
    }}}
    writes = []
    hermes_config = ModuleType("hermes_cli.config")
    hermes_config.load_config_readonly = lambda: copy.deepcopy(config)
    hermes_config.save_config = lambda value, **kw: writes.append((value, kw))
    shared = ModuleType("gateway.platforms._shared")
    shared.get_scoped_secret = lambda name: "synthetic-secret-do-not-return"
    monkeypatch.setitem(sys.modules, "hermes_cli.config", hermes_config)
    monkeypatch.setitem(sys.modules, "gateway.platforms._shared", shared)
    monkeypatch.setattr(dashboard, "_home", lambda: tmp_path)
    app = FastAPI()
    app.include_router(dashboard.router, prefix="/api/plugins/wechat-router")
    with TestClient(app) as client:
        yield client, config, writes, tmp_path


def test_fresh_dashboard_does_not_create_inbox_or_leak_secrets(ui):
    client, config, writes, home = ui
    config["platforms"]["wechat_router"]["extra"]["api_key"] = "synthetic-secret-do-not-return"
    response = client.get("/api/plugins/wechat-router/config")
    assert response.status_code == 200
    assert "synthetic-secret" not in response.text
    assert "hermes_bin" not in response.json()["settings"]
    assert response.json()["secrets"]["TYPESAFE_API_KEY"] is True
    assert client.get("/api/plugins/wechat-router/status").json() == {"initialized": False}
    assert not data_path(home).exists()
    assert writes == []


def test_save_only_patches_plugin_fields_and_preserves_advanced_settings(ui):
    client, config, writes, _ = ui
    original = copy.deepcopy(config)
    response = client.put("/api/plugins/wechat-router/config", json={
        "enabled": True, "settings": {"mode": "active", "owner_ids": ["me"]}
    })
    assert response.json() == {"ok": True, "restart_required": True}
    assert writes == [({"platforms": {"wechat_router": {
        "enabled": True, "extra": {"mode": "active", "owner_ids": ["me"]}
    }}}, {"merge_existing": True})]
    assert config == original


def test_provider_and_prompt_can_be_saved_without_model_calls(ui):
    client, _, writes, _ = ui
    patch = {"provider": "custom", "provider_base_url": "https://provider.example/v1",
             "model": "provider-jev", "jev_instructions": "本人被明确提及的事项才交接。"}
    response = client.put("/api/plugins/wechat-router/config", json={"enabled": False, "settings": patch})
    assert response.status_code == 200
    assert writes[0][0]["platforms"]["wechat_router"]["extra"] == patch
    config = client.get("/api/plugins/wechat-router/config").json()
    assert config["defaults"]["jev_instructions"]
    assert config["secrets"]["WECHAT_ROUTER_API_KEY"] is True


@pytest.mark.parametrize("mode,kind", [("bool", "noul"), ("choice", "choice")])
def test_request_preview_uses_draft_fields_without_saving_or_reading_messages(ui, mode, kind):
    client, _, writes, home = ui
    patch = {"decision_mode": mode, "decision_question": "自定义问题", "assistant_profile": "main"}
    response = client.post("/api/plugins/wechat-router/request-preview", json={"settings": patch})
    assert response.status_code == 200
    preview = response.json()
    question = preview["request"]["questions"]["needs_followup"]
    assert question["type"] == kind and question["instructions"] == "自定义问题"
    assert not preview["calls_model"] and not preview["reads_messages"]
    assert "synthetic-secret" not in response.text
    assert writes == [] and not data_path(home).exists()
    config = client.get("/api/plugins/wechat-router/config").json()
    assert "routes" not in config["settings"] and "triage_profile" not in config["settings"]


@pytest.mark.parametrize("patch", [
    {"mode": "active"}, {"timezone": "Not/A/Timezone"}, {"quiet_seconds": 100},
    {"port": True}, {"routes": {}}, {"hermes_bin": "arbitrary-command"},
    {"TYPESAFE_API_KEY": "synthetic-secret-do-not-return"}, {"owner_ids": "bad"},
])
def test_invalid_or_sensitive_patch_cannot_write(ui, patch):
    client, _, writes, _ = ui
    response = client.put("/api/plugins/wechat-router/config", json={"enabled": True, "settings": patch})
    assert response.status_code == 422
    assert "synthetic-secret" not in response.text
    assert writes == []


def test_pause_requires_real_inbox_and_boolean(ui):
    client, _, _, home = ui
    url = "/api/plugins/wechat-router/pause"
    assert client.post(url, json={"paused": True}).status_code == 409
    store = Store(data_path(home))
    assert client.post(url, json={"paused": "false"}).status_code == 422
    assert client.post(url, json={"paused": True}).json() == {"paused": True}
    assert store.paused()
    assert client.get("/api/plugins/wechat-router/status").json()["paused"] is True
    assert client.post(url, json={"paused": False}).json() == {"paused": False}


def test_retry_only_requeues_failed_batch(ui):
    client, _, _, home = ui
    store = Store(data_path(home))
    with store.connection(write=True) as connection:
        connection.execute("INSERT INTO batches(id,source,conversation_id,created_at,status,attempts,error) "
                           "VALUES('failed-one','fixture','group',1,'failed',3,'fixture_failure')")
        connection.execute("INSERT INTO batches(id,source,conversation_id,created_at,status) "
                           "VALUES('done-one','fixture','group',2,'done')")
    url = "/api/plugins/wechat-router/retry"
    assert client.post(url, json={"batch_id": "done-one"}).status_code == 409
    assert client.post(url, json={"batch_id": "missing"}).status_code == 409
    assert client.post(url, json={"batch_id": "failed-one"}).json() == {"queued": True}
    assert store.status()["batches"]["queued"] == 1
    assert store.status()["failures"] == []


def test_dashboard_entry_imports_without_adding_sys_path():
    path = Path(__file__).resolve().parents[1] / "dashboard" / "plugin_api.py"
    before = list(sys.path)
    spec = importlib.util.spec_from_file_location("dashboard_entry_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.router.routes
    assert sys.path == before
