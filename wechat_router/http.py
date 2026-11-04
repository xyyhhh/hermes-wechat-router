"""Authenticated HTTP ingress; acknowledgements follow the SQLite commit."""

import asyncio
from collections import deque
import hashlib
import hmac
import json
import time

from aiohttp import web

from . import __version__
from .events import InvalidEvent, normalize_event
from .store import EventConflict

PREFIX = "/wechat-router/v1"


def signature(secret, timestamp, method, path, body=b""):
    signed = f"v1\n{timestamp}\n{method.upper()}\n{path}\n".encode() + body
    return "sha256=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()


def make_app(store, cfg, secret):
    if not isinstance(secret, str) or len(secret.encode()) < 32:
        raise ValueError("WECHAT_ROUTER_SECRET must contain at least 32 bytes")
    arrivals = deque()

    @web.middleware
    async def authenticate(request, handler):
        if request.path == PREFIX + "/health" and request.method == "GET":
            return await handler(request)
        body = await request.read()
        stamp = request.headers.get("X-Wechat-Router-Timestamp", "")
        supplied = request.headers.get("X-Wechat-Router-Signature", "")
        if len(stamp) > 16 or not stamp.isdigit() or abs(time.time() - int(stamp)) > cfg.replay_seconds:
            return web.json_response({"error": "unauthorized"}, status=401)
        expected = signature(secret, stamp, request.method, request.raw_path, body)
        if not hmac.compare_digest(expected, supplied):
            return web.json_response({"error": "unauthorized"}, status=401)
        now = time.monotonic()
        while arrivals and arrivals[0] < now - 60:
            arrivals.popleft()
        if len(arrivals) >= 6000:
            return web.json_response({"error": "rate_limited"}, status=429, headers={"Retry-After": "60"})
        arrivals.append(now)
        try:
            return await handler(request)
        except (InvalidEvent, EventConflict) as exc:
            return web.json_response({"error": str(exc)}, status=409 if isinstance(exc, EventConflict) else 400)
        except web.HTTPException:
            raise
        except Exception:
            return web.json_response({"error": "inbox_unavailable"}, status=503)

    async def receive(request):
        if request.content_type != "application/json":
            raise InvalidEvent("Content-Type must be application/json")
        try:
            body = json.loads(await request.read())
        except (ValueError, UnicodeError):
            raise InvalidEvent("invalid JSON") from None
        event = normalize_event(body, cfg)
        fresh = await asyncio.to_thread(store.accept, event)
        result = await asyncio.to_thread(store.event_status, event["source"], event["event_id"])
        return web.json_response({**result, "duplicate": not fresh}, status=202 if fresh else 200)

    async def event_status(request):
        if not request.query.get("source") or not request.query.get("event_id"):
            raise InvalidEvent("source and event_id query parameters are required")
        result = await asyncio.to_thread(store.event_status, request.query["source"], request.query["event_id"])
        return web.json_response(result if result else {"error": "not_found"}, status=200 if result else 404)

    async def status(request):
        return web.json_response(await asyncio.to_thread(store.status))

    async def health(request):
        return web.json_response({"ok": True, "plugin": "wechat-router", "version": __version__})

    app = web.Application(client_max_size=cfg.max_body_bytes, middlewares=[authenticate])
    app.router.add_post(PREFIX + "/events", receive)
    app.router.add_get(PREFIX + "/events/status", event_status)
    app.router.add_get(PREFIX + "/status", status)
    app.router.add_get(PREFIX + "/health", health)
    return app
