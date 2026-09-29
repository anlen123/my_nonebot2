"""接入点从路由表一路透传到 chat() 的接线测试。"""

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
async def test_reactive_turn_routes_configured_group(monkeypatch, _runtime):
    from nonebot_plugin_hermes.config import HermesEndpoint, plugin_config
    from nonebot_plugin_hermes.handlers import message as handler_mod

    monkeypatch.setattr(
        plugin_config,
        "hermes_group_endpoints",
        {"ob11:g1": HermesEndpoint(url="http://127.0.0.1:8642/p/team-a", key="sk-a-at-least-16-ch")},
    )

    captured: dict = {}

    async def capture_chat(**kwargs):
        captured.update(kwargs)
        return ChatResult(raw_text="ok", structured={"should_reply": False})

    monkeypatch.setattr(handler_mod.hermes_client, "chat", capture_chat)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    now = 11_000_000
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

    assert captured["target"].label == "ob11:g1"
    assert captured["target"].base_url == "http://127.0.0.1:8642/p/team-a"
    assert captured["target"].api_key == "sk-a-at-least-16-ch"


@pytest.mark.asyncio
async def test_passive_turn_unconfigured_uses_default(monkeypatch, _runtime):
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as handler_mod

    monkeypatch.setattr(plugin_config, "hermes_group_endpoints", {})

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
        now_ms=12_000_000,
    )

    assert captured["target"].label == "default"


@pytest.mark.asyncio
async def test_passive_turn_in_group_routes_configured_group(monkeypatch, _runtime):
    """passive 路径(active_session 关)在群里也要按群路由。"""
    from nonebot_plugin_hermes.config import HermesEndpoint, plugin_config
    from nonebot_plugin_hermes.handlers import message as handler_mod

    monkeypatch.setattr(
        plugin_config,
        "hermes_group_endpoints",
        {"ob11:g7": HermesEndpoint(url="http://127.0.0.1:8643", key="sk-b-at-least-16-ch")},
    )

    captured: dict = {}

    async def capture_chat(**kwargs):
        captured.update(kwargs)
        return ChatResult(raw_text="ok")

    monkeypatch.setattr(handler_mod.hermes_client, "chat", capture_chat)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    await handler_mod._run_passive_turn(
        bot=_fake_bot(),
        target=_FakeTarget(id="g7"),
        adapter_name="ob11",
        user_id="u1",
        group_id="g7",
        text="你好",
        image_urls=[],
        is_private=False,
        now_ms=12_500_000,
    )

    assert captured["target"].label == "ob11:g7"
    assert captured["target"].base_url == "http://127.0.0.1:8643"
