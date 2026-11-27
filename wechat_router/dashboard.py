"""Web Dashboard API. Hermes supplies authentication and the current profile scope.

Only the explicit test-connection endpoint calls a model, with synthetic data.
Synchronous endpoints run in FastAPI's worker pool, retaining profile context.
"""

from dataclasses import asdict
from typing import Any
from zoneinfo import ZoneInfoNotFoundError

from fastapi import APIRouter, Body, HTTPException

from . import __version__
from .config import GOOGLE_MODEL, Settings, data_path
from .store import Store
from .jev import JevClient, JevError, build_request, preview_request, validate_response
from .network import model_http_options
from .audit import AuditLog, audit_path, audit_context

router = APIRouter()
EDITABLE = frozenset({
    "host", "port", "mode", "receive_only", "owner_name", "owner_ids", "owner_aliases", "sender_rules", "interests", "sources", "timezone",
    "quiet_seconds", "max_wait_seconds", "max_batch_messages", "max_context_chars",
    "min_confidence", "model", "board", "assistant_profile",
    "provider", "provider_base_url", "jev_instructions", "request_timeout",
    "decision_mode", "decision_question", "bool_threshold", "positive_criteria", "negative_criteria", "choice_criteria",
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
        raise HTTPException(422, "配置无效：检查 provider、判断模式、问题、选项、阈值和主助理名称；active 模式需要本人 ID。") from None


@router.get("/config")
def get_config():
    from gateway.platforms._shared import get_scoped_secret
    platform = _platform()
    defaults = asdict(Settings(model=GOOGLE_MODEL) if platform.get("extra", {}).get("provider") == "google" else Settings())
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
        "secrets": {k: bool(get_scoped_secret(k)) for k in ("WECHAT_ROUTER_SECRET", "TYPESAFE_API_KEY", "WECHAT_ROUTER_API_KEY", "GEMINI_API_KEY")},
        "defaults": {k: v for k, v in defaults.items() if k in {
            "jev_instructions", "decision_mode", "decision_question", "bool_threshold",
            "positive_criteria", "negative_criteria", "choice_criteria", "min_confidence"}},
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


@router.post("/request-preview")
def request_preview(body: dict[str, Any] = Body(...)):
    if set(body) != {"settings"} or not isinstance(body["settings"], dict) or set(body["settings"]) - EDITABLE:
        raise HTTPException(422, "Expected editable settings")
    settings = _validated({**_platform().get("extra", {}), **body["settings"]})
    return {"request": preview_request(settings), "endpoint": settings.provider_endpoint,
            "calls_model": False, "reads_messages": False}


@router.post("/test-connection")
async def test_connection(body: dict[str, Any] = Body(...)):
    if body:
        raise HTTPException(422, "Expected an empty object; test uses saved configuration")
    from aiohttp import ClientSession
    import asyncio
    from gateway.platforms._shared import get_scoped_secret
    settings = _validated(_platform().get("extra", {}))
    key = get_scoped_secret(settings.provider_key_name)
    if not key:
        raise HTTPException(409, "请先保存所选 provider 的模型密钥。")
    snapshot = asdict(settings)
    # No owner IDs, policy, interests or real messages from the user's config leave this endpoint.
    snapshot.update(input_format=2, owner_name="测试本人", owner_ids=["synthetic-owner"], owner_aliases=[],
                    sender_rules=[], interests=[], context=[], candidates=[], reply_context=[],
                    jev_instructions="只判断合成消息中是否出现时间安排。",
                    decision_question=Settings().decision_question, positive_criteria=Settings().positive_criteria,
                    negative_criteria=Settings().negative_criteria, choice_criteria=Settings().choice_criteria,
                    messages=[{"text": "@测试本人 已确认：2030年1月2日下午三点参加项目评审会。",
                               "sender_id": "synthetic-colleague", "mentions": ["synthetic-owner"],
                               "occurred_at": "2030-01-01T10:00:00+08:00", "chat_type": "group"}])
    request = build_request(snapshot)
    try:
        session_options, request_options = model_http_options(settings.provider_endpoint)
        async with ClientSession(**session_options) as session:
            client = JevClient(session, key, settings.request_timeout, provider=settings.provider,
                               endpoint=settings.provider_endpoint if settings.provider == "custom" else None,
                               request_options=request_options,
                               audit=await asyncio.to_thread(AuditLog, audit_path(_home())))
            with audit_context(origin="connection_test", decision_mode=settings.decision_mode):
                result = await client.evaluate(request)
            validated = validate_response(result, request)
    except JevError as exc:
        # Do not echo provider error bodies, authentication headers or arbitrary input.
        detail = exc.code
        if exc.code in ("jev_transport_error", "jev_timeout") and exc.__context__ is not None:
            detail += " (" + type(exc.__context__).__name__ + ")"
        raise HTTPException(502, detail) from None
    usage = validated["usage"]
    return {"ok": True, "provider": settings.provider, "model": settings.model,
            "answer": validated["answers"]["needs_followup"],
            "usage": {name: usage[name] for name in ("input_tokens", "output_tokens")
                      if isinstance(usage, dict) and type(usage.get(name)) is int and usage[name] >= 0},
            "synthetic_only": True, "requests": 1, "tasks_created": 0}


@router.get("/network")
def network_status():
    from urllib.parse import urlsplit
    from gateway.platforms.base import gateway_trust_env, resolve_proxy_url
    settings = _validated(_platform().get("extra", {}))
    proxy = resolve_proxy_url(target_hosts=urlsplit(settings.provider_endpoint).hostname)
    parts = urlsplit(proxy) if proxy else None
    return {"gateway_trust_env": gateway_trust_env(), "proxy": {
        "scheme": parts.scheme, "host": parts.hostname, "port": parts.port,
    } if parts else None}


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


@router.get("/audit")
def audit_status():
    try:
        return {"enabled": True, **AuditLog.summary(audit_path(_home()))}
    except Exception:
        raise HTTPException(503, "调用日志暂不可读，请检查服务器磁盘和文件权限。") from None


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
