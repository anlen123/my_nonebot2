"""图片内联从 turn runner 一路透传到 chat() 的接线测试。

钉死的性质是「平台 URL 不出现在出站 payload 里」——内联单测覆盖转换本身,
这里覆盖两条出站路径是否真的用上了转换结果。
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

from nonebot_plugin_hermes import mcp as _mcp
from nonebot_plugin_hermes.core import image_inline
from nonebot_plugin_hermes.core.active_session import ActiveSessionManager
from nonebot_plugin_hermes.core.bot_registry import BotRegistry
from nonebot_plugin_hermes.core.hermes_client import ChatResult
from nonebot_plugin_hermes.core.inflight import InflightRegistry
from nonebot_plugin_hermes.core.message_buffer import MessageBuffer
from nonebot_plugin_hermes.core.storage.image_cache import ImageCache
from nonebot_plugin_hermes.core.storage.image_fetcher import ImageFetcher
from nonebot_plugin_hermes.core.storage.message_store import MessageStore

_PLATFORM_URL = "https://media.example.invalid/download?fileid=abc&rkey=secret-token"


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


def _jpeg(w: int = 2400, h: int = 1600) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (30, 90, 160)).save(buf, format="JPEG")
    return buf.getvalue()


class _FakeClient:
    def __init__(self, mapping):
        self._m = mapping

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        resp = MagicMock()
        resp.content = self._m[url]
        resp.raise_for_status = MagicMock(return_value=None)
        return resp


def _fake_bot():
    bot = MagicMock()
    bot.self_id = "999"
    return bot


def _install(monkeypatch, handler_mod, captured: dict, structured: dict | None) -> None:
    monkeypatch.setattr(image_inline.httpx, "AsyncClient", lambda **kw: _FakeClient({_PLATFORM_URL: _jpeg()}))

    async def capture_chat(**kwargs):
        captured.update(kwargs)
        return ChatResult(raw_text="ok", structured=structured)

    monkeypatch.setattr(handler_mod.hermes_client, "chat", capture_chat)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))


def _image_part_urls(user_content) -> list[str]:
    assert isinstance(user_content, list), f"expected multimodal parts, got {type(user_content)}"
    return [p["image_url"]["url"] for p in user_content if p.get("type") == "image_url"]


def _assert_inlined(captured: dict) -> None:
    urls = _image_part_urls(captured["user_content_override"])
    assert len(urls) == 1
    assert urls[0].startswith("data:image/jpeg;base64,")
    # 平台 URL 带时效凭据,整条都不该出现在出站 payload 的任何角落。
    assert _PLATFORM_URL not in json.dumps(captured["user_content_override"])


@pytest.mark.asyncio
async def test_reactive_turn_sends_inlined_image_not_platform_url(monkeypatch, _runtime):
    from nonebot_plugin_hermes.handlers import message as handler_mod

    captured: dict = {}
    _install(monkeypatch, handler_mod, captured, {"should_reply": False})

    now = 7_000_000
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)
    await handler_mod._run_reactive_turn(
        bot=_fake_bot(),
        target=_FakeTarget(id="g1"),
        adapter_name="ob11",
        user_id="u1",
        group_id="g1",
        text="看看这个",
        image_urls=[_PLATFORM_URL],
        is_explicit_trigger=True,
        now_ms=now,
    )
    _assert_inlined(captured)


@pytest.mark.asyncio
async def test_passive_turn_sends_inlined_image_not_platform_url(monkeypatch, _runtime):
    from nonebot_plugin_hermes.handlers import message as handler_mod

    captured: dict = {}
    _install(monkeypatch, handler_mod, captured, None)

    await handler_mod._run_passive_turn(
        bot=_fake_bot(),
        target=_FakeTarget(id="u1", private=True),
        adapter_name="ob11",
        user_id="u1",
        group_id=None,
        text="这是什么",
        image_urls=[_PLATFORM_URL],
        is_private=True,
        now_ms=8_000_000,
    )
    _assert_inlined(captured)


@pytest.mark.asyncio
async def test_inline_disabled_falls_back_to_platform_url(monkeypatch, _runtime):
    """逃生口仍然可用:关掉开关就回到直发 URL 的旧行为。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as handler_mod

    monkeypatch.setattr(plugin_config, "hermes_image_inline_enabled", False)
    captured: dict = {}
    _install(monkeypatch, handler_mod, captured, None)

    await handler_mod._run_passive_turn(
        bot=_fake_bot(),
        target=_FakeTarget(id="u1", private=True),
        adapter_name="ob11",
        user_id="u1",
        group_id=None,
        text="这是什么",
        image_urls=[_PLATFORM_URL],
        is_private=True,
        now_ms=8_000_000,
    )
    assert _image_part_urls(captured["user_content_override"]) == [_PLATFORM_URL]
