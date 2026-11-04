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
