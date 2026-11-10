import importlib
import json
import sys
import time
import types
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession

from wechat_router.config import Settings, data_path
from wechat_router.http import PREFIX, signature
from wechat_router.store import Store


def test_receive_only_flag_is_strictly_boolean():
    assert Settings.parse({'receive_only': True}).receive_only
    with pytest.raises(ValueError):
        Settings.parse({'receive_only': 'true'})


@pytest.mark.asyncio
async def test_receive_only_accepts_and_deduplicates_without_model_or_dispatch(monkeypatch, tmp_path, unused_tcp_port):
    # Only the host API is stubbed; HTTP, persistence, signatures and duplicates
    # use the real adapter and receiver implementation.
    class Base:
        def __init__(self, config, platform):
            self.config = config
        def _mark_connected(self):
            pass
        def _mark_disconnected(self):
            pass
    modules = {'gateway': {}, 'gateway.config': {'Platform': lambda value: value},
               'gateway.platforms': {}, 'gateway.platforms.base': {'BasePlatformAdapter': Base, 'SendResult': dict},
               'gateway.platforms._shared': {'get_scoped_secret': lambda key, default='': 'synthetic-receiver-secret-'*3 if key=='WECHAT_ROUTER_SECRET' else ''},
               'hermes_constants': {'get_hermes_home': lambda: tmp_path}}
    for name, values in modules.items():
        module = types.ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.delitem(sys.modules, 'wechat_router.adapter', raising=False)
    adapter_module = importlib.import_module('wechat_router.adapter')
    def prohibited(*args, **kwargs):
        raise AssertionError('receive_only must not instantiate inference or dispatch clients')
    for name in ('JevClient', 'KanbanClient', 'Engine'):
        monkeypatch.setattr(adapter_module, name, prohibited)
    adapter = adapter_module.WechatRouterAdapter(SimpleNamespace(extra={'receive_only': True, 'port': unused_tcp_port}))
    try:
        assert await adapter.connect()
        body = json.dumps({'source':'fixture-source','event_id':'fixture-event','conversation_id':'fixture-chat',
            'sender_id':'fixture-sender','occurred_at':'2026-01-01T00:00:00Z','text':'Synthetic receive-only fixture'}).encode()
        stamp = str(int(time.time()))
        headers = {'Content-Type':'application/json','X-Wechat-Router-Timestamp':stamp,
            'X-Wechat-Router-Signature': signature('synthetic-receiver-secret-'*3,stamp,'POST',PREFIX+'/events',body)}
        async with ClientSession() as client:
            async with client.post(f'http://127.0.0.1:{unused_tcp_port}'+PREFIX+'/events',data=body,headers=headers) as reply:
                assert reply.status==202
            async with client.post(f'http://127.0.0.1:{unused_tcp_port}'+PREFIX+'/events',data=body,headers=headers) as reply:
                assert reply.status==200
                assert (await reply.json())['duplicate'] is True
        store = Store(data_path(tmp_path))
        assert store.status()['messages']==1 and store.paused()
        assert store.status()['usage']['calls']==0
        assert adapter._worker is None and adapter._session is None
    finally:
        await adapter.disconnect()
        sys.modules.pop('wechat_router.adapter', None)
