"""记忆作用域 key 从 SessionManager 一路透传到 chat() 的接线测试。"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from nonebot_plugin_hermes import mcp as _mcp
from nonebot_plugin_hermes.core.active_session import ActiveSessionManager
from nonebot_plugin_hermes.core.bot_registry import BotRegistry
from nonebot_plugin_hermes.core.hermes_client import ChatResult
from nonebot_plugin_hermes.core.inflight import InflightRegistry
from nonebot_plugin_hermes.core.message_buffer import MessageBuffer
from nonebot_plugin_hermes.core.storage.image_cache import ImageCache
from nonebot_plugin_hermes.core.storage.image_fetcher import ImageFetcher
from nonebot_plugin_hermes.core.storage.message_store import MessageStore


@dataclass
class _FakeTarget:
    id: str
    private: bool = False
    adapter: str = "ob11"


@pytest.fixture
def _runtime(tmp_path):
    store = MessageStore(db_path=tmp_path / "messages.db")
    cache = ImageCache(cache_dir=tmp_path / "imgs", quota_bytes=1024 * 1024)
    fetcher = ImageFetcher(store=store, cache=cache)
    _mcp.message_buffer = MessageBuffer(store=store, fetcher=fetcher)
    _mcp.active_sessions = ActiveSessionManager(default_ttl_sec=300)
    _mcp.bot_registry = BotRegistry()
    _mcp.inflight = InflightRegistry()
    yield
    _mcp.message_buffer = None
    _mcp.active_sessions = None
    _mcp.bot_registry = None
    _mcp.inflight = None
    store.close()


def _fake_bot():
    bot = MagicMock()
    bot.self_id = "999"
    return bot


@pytest.mark.asyncio
async def test_reactive_turn_passes_group_memory_key(monkeypatch, _runtime):
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as handler_mod

    monkeypatch.setattr(plugin_config, "hermes_honcho_enabled", True)

    captured: dict = {}

    async def capture_chat(**kwargs):
        captured.update(kwargs)
        return ChatResult(raw_text="ok", structured={"should_reply": False})

    monkeypatch.setattr(handler_mod.hermes_client, "chat", capture_chat)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    now = 7_000_000
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)
    await handler_mod._run_reactive_turn(
        bot=_fake_bot(),
        target=_FakeTarget(id="g1"),
        adapter_name="ob11",
        user_id="u1",
        group_id="g1",
        text="在吗",
        image_urls=[],
        is_explicit_trigger=True,
        now_ms=now,
    )

    assert captured["memory_key"] == "agent:main:nonebot-ob11:group:g1"


@pytest.mark.asyncio
async def test_passive_turn_passes_private_memory_key(monkeypatch, _runtime):
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as handler_mod

    monkeypatch.setattr(plugin_config, "hermes_honcho_enabled", True)

    captured: dict = {}

    async def capture_chat(**kwargs):
        captured.update(kwargs)
        return ChatResult(raw_text="ok")

    monkeypatch.setattr(handler_mod.hermes_client, "chat", capture_chat)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    await handler_mod._run_passive_turn(
        bot=_fake_bot(),
        target=_FakeTarget(id="u1", private=True),
        adapter_name="ob11",
        user_id="u1",
        group_id=None,
        text="你好",
        image_urls=[],
        is_private=True,
        now_ms=8_000_000,
    )

    assert captured["memory_key"] == "agent:main:nonebot-ob11:dm:u1"


@pytest.mark.asyncio
async def test_disabled_sends_no_memory_key(monkeypatch, _runtime):
    """默认关时 chat() 收到 memory_key=None,请求字节与旧版一致。"""
    from nonebot_plugin_hermes.handlers import message as handler_mod

    captured: dict = {}

    async def capture_chat(**kwargs):
        captured.update(kwargs)
        return ChatResult(raw_text="ok")

    monkeypatch.setattr(handler_mod.hermes_client, "chat", capture_chat)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    await handler_mod._run_passive_turn(
        bot=_fake_bot(),
        target=_FakeTarget(id="u1", private=True),
        adapter_name="ob11",
        user_id="u1",
        group_id=None,
        text="你好",
        image_urls=[],
        is_private=True,
        now_ms=9_000_000,
    )

    assert captured["memory_key"] is None
