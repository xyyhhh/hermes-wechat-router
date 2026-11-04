"""Hermes imports this file directly, outside the gateway plugin namespace."""

import importlib.util
from pathlib import Path
import sys

# Keep relative imports working without mutating sys.path or shadowing Hermes's tools.
_name = "_hermes_wechat_router_dashboard"
if _name not in sys.modules:
    _package = Path(__file__).resolve().parents[1] / "wechat_router"
    _spec = importlib.util.spec_from_file_location(
        _name, _package / "__init__.py", submodule_search_locations=[str(_package)]
    )
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_name] = _module
    _spec.loader.exec_module(_module)

router = importlib.import_module(f"{_name}.dashboard").router
