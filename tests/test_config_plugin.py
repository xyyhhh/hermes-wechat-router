import importlib.util
from pathlib import Path

import pytest

from wechat_router.config import Settings


@pytest.mark.parametrize("raw", [{"mode": "surprise"}, {"owner_ids": "me"}, {"port": True},
                               {"request_timeout": float("nan")}, {"board": "../other"},
                               {"triage_profile": "--shell"}, {"unknown": 1}, {"routes": {}}])
def test_bad_config_rejected(raw):
    with pytest.raises((ValueError, TypeError)):
        Settings.parse(raw)


def test_gateway_injected_session_flags_are_not_router_configuration():
    cfg = Settings.parse({'receive_only': True, 'group_sessions_per_user': True, 'thread_sessions_per_user': False})
    assert cfg.receive_only
    with pytest.raises(ValueError):
        Settings.parse({'group_sessions_per_user_typo': True})


@pytest.mark.parametrize("url", ["", "https://", "ftp://example.com/v1", "http://example.com/v1",
                                "https://user:secret@example.com/v1", "https://example.com/v1?key=secret",
                                "https://example.com/v1#fragment", "https://example.com:bad/v1",
                                "https://example.com:0/v1", "https://example.com/\nv1"])
def test_provider_rejects_invalid_or_sensitive_url(url):
    with pytest.raises(ValueError):
        Settings.parse({"provider": "custom", "provider_base_url": url})


@pytest.mark.parametrize("url,expected", [
    ("https://example.com/v1/", "https://example.com/v1/systemone"),
    ("https://example.com/v1/systemone", "https://example.com/v1/systemone"),
    ("http://localhost:8000/v1", "http://localhost:8000/v1/systemone"),
    ("http://127.0.0.1:8000/v1", "http://127.0.0.1:8000/v1/systemone"),
])
def test_custom_provider_endpoint_and_separate_key(url, expected):
    cfg = Settings.parse({"provider": "custom", "provider_base_url": url})
    assert cfg.provider_endpoint == expected
    assert cfg.provider_key_name == "WECHAT_ROUTER_API_KEY"
    assert Settings().provider_key_name == "TYPESAFE_API_KEY"


@pytest.mark.parametrize("prompt", ["", " ", 123, "x" * 12001])
def test_prompt_must_be_nonempty_and_bounded(prompt):
    with pytest.raises(ValueError):
        Settings.parse({"jev_instructions": prompt})


def test_registration_imports_no_adapter_or_inference_client():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("router_test_plugin", root / "__init__.py",
                                                submodule_search_locations=[str(root)])
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        class Context:
            def register_platform(self, **kwargs):
                self.registration = kwargs
            def register_cli_command(self, **kwargs):
                pass
            def register_tool(self, **kwargs):
                pass
        ctx = Context()
        module.register(ctx)
        assert ctx.registration["name"] == "wechat_router"
        assert "router_test_plugin.wechat_router.adapter" not in sys.modules
    finally:
        for name in list(sys.modules):
            if name == spec.name or name.startswith(spec.name + "."):
                del sys.modules[name]
