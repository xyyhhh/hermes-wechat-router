"""The only runtime module coupled to Hermes' documented platform extension API."""

import asyncio
import logging

from gateway.config import Platform
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.platforms._shared import get_scoped_secret
from hermes_constants import get_hermes_home

from .config import Settings, data_path
from .engine import Engine
from .http import make_app
from .jev import JevClient
from .kanban import KanbanClient
from .store import Store

logger = logging.getLogger(__name__)


class WechatRouterAdapter(BasePlatformAdapter):
    def __init__(self, config):
        super().__init__(config, Platform("wechat_router"))
        self.settings = Settings.parse(config.extra)
        self.home = get_hermes_home()
        self._runner = None
        self._worker = None
        self._session = None

    async def connect(self, *, is_reconnect=False):
        from aiohttp import ClientSession, web
        if self._runner is not None:
            return True
        cfg = self.settings
        secret = get_scoped_secret("WECHAT_ROUTER_SECRET", "")
        api_key = get_scoped_secret(cfg.provider_key_name, "")
        if not api_key:
            raise ValueError(f"{cfg.provider_key_name} is not configured for this profile")
        store = await asyncio.to_thread(Store, data_path(self.home))
        app = make_app(store, cfg, secret)
        try:
            self._session = ClientSession(trust_env=False)
            kanban = KanbanClient(cfg, self.home)
            client = JevClient(self._session, api_key, cfg.request_timeout,
                               endpoint=cfg.provider_endpoint if cfg.provider == "custom" else None)
            engine = Engine(store, cfg, client, kanban)
            self._runner = web.AppRunner(app, access_log=None)
            await self._runner.setup()
            await web.TCPSite(self._runner, cfg.host, cfg.port).start()
            self._worker = asyncio.create_task(engine.run(), name="wechat-router-inbox")
            self._mark_connected()
            logger.info("wechat-router ingress started (mode=%s)", cfg.mode)
            return True
        except BaseException:
            await self.disconnect()
            raise

    async def disconnect(self):
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None
        if self._session is not None:
            await self._session.close()
            self._session = None
        self._mark_disconnected()

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        return SendResult(success=False, error="wechat-router is an ingress-only service")

    async def get_chat_info(self, chat_id):
        return {"name": "WeChat router ingress", "type": "dm"}
