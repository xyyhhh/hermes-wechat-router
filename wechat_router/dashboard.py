"""Web Dashboard API. Hermes supplies authentication and the current profile scope.

No model calls, network requests, gateway restarts or direct Kanban writes here.
Synchronous endpoints run in FastAPI's worker pool, retaining profile context.
"""

from dataclasses import asdict
from typing import Any
from zoneinfo import ZoneInfoNotFoundError

from fastapi import APIRouter, Body, HTTPException

from . import __version__
from .config import DEFAULT_JEV_INSTRUCTIONS, Settings, data_path
from .store import Store

router = APIRouter()
EDITABLE = frozenset({
    "host", "port", "mode", "owner_name", "owner_ids", "interests", "sources", "timezone",
    "quiet_seconds", "max_wait_seconds", "max_batch_messages", "max_context_chars",
    "min_confidence", "model", "board", "triage_profile", "routes",
    "provider", "provider_base_url", "jev_instructions", "request_timeout",
})


def _home():
    from hermes_constants import get_hermes_home
    return get_hermes_home()


def _platform():
    from hermes_cli.config import load_config_readonly
    config = load_config_readonly()
    value = (config.get("platforms") or {}).get("wechat_router") or {}
    if not isinstance(value, dict) or not isinstance(value.get("extra", {}), dict):
        raise HTTPException(409, "platforms.wechat_router must contain an extra object")
    return value


def _validated(extra):
    try:
        return Settings.parse(extra)
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        # Validation exceptions can include submitted values. Never echo arbitrary input.
        raise HTTPException(422, "配置无效：检查 provider 地址、提示词、时区、数值和分类路由；active 模式需要本人 ID。") from None


@router.get("/config")
def get_config():
    from gateway.platforms._shared import get_scoped_secret
    platform = _platform()
    defaults = asdict(Settings())
    # Only return known non-secret fields, even if the YAML contains unrelated keys.
    extra = {**defaults, **{k: v for k, v in platform.get("extra", {}).items() if k in defaults}}
    error = None
    try:
        _validated(platform.get("extra", {}))
    except HTTPException as exc:
        error = exc.detail
    home = _home()
    return {
        "version": __version__, "enabled": bool(platform.get("enabled", False)),
        "settings": {k: v for k, v in extra.items() if k in EDITABLE},
        "profile": home.name if home.parent.name == "profiles" else "default",
        "validation_error": error,
        "secrets": {k: bool(get_scoped_secret(k)) for k in ("WECHAT_ROUTER_SECRET", "TYPESAFE_API_KEY", "WECHAT_ROUTER_API_KEY")},
        "defaults": {"jev_instructions": DEFAULT_JEV_INSTRUCTIONS},
    }


@router.put("/config")
def put_config(body: dict[str, Any] = Body(...)):
    if set(body) != {"enabled", "settings"} or type(body["enabled"]) is not bool:
        raise HTTPException(422, "Expected enabled (boolean) and settings (object)")
    patch = body["settings"]
    if not isinstance(patch, dict) or set(patch) - EDITABLE:
        raise HTTPException(422, "Unknown or non-editable setting")
    current = _platform().get("extra", {})
    _validated({**current, **patch})
    from hermes_cli.config import save_config
    save_config({"platforms": {"wechat_router": {"enabled": body["enabled"], "extra": patch}}},
                merge_existing=True)
    return {"ok": True, "restart_required": True}


def _store():
    path = data_path(_home())
    if not path.is_file():
        raise HTTPException(409, "尚未初始化消息收件箱，请先配置并启动 Gateway。")
    return Store(path)


@router.get("/status")
def status():
    if not data_path(_home()).is_file():
        return {"initialized": False}
    return {"initialized": True, **_store().status()}


@router.post("/pause")
def pause(body: dict[str, Any] = Body(...)):
    if set(body) != {"paused"} or type(body["paused"]) is not bool:
        raise HTTPException(422, "Expected paused (boolean)")
    store = _store()
    store.pause(body["paused"])
    return {"paused": store.paused()}


@router.post("/retry")
def retry(body: dict[str, Any] = Body(...)):
    if set(body) != {"batch_id"} or not isinstance(body["batch_id"], str) or not 1 <= len(body["batch_id"]) <= 128:
        raise HTTPException(422, "Expected batch_id")
    if not _store().retry(body["batch_id"]):
        raise HTTPException(409, "该批次不存在或不处于失败状态。")
    return {"queued": True}
