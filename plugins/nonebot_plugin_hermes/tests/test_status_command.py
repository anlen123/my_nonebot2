"""/hermes-status 正文拼装。

这段代码读的全是运行时单例的内部状态,是最容易在重构后悄悄失配的地方——
SQLite 化之后 MessageBuffer 已经没有 per-group 内存桶,而 status 还在摸它。
"""

from __future__ import annotations

import pytest

from nonebot_plugin_hermes import mcp as _mcp
from nonebot_plugin_hermes.core.active_session import ActiveSessionManager
from nonebot_plugin_hermes.core.bot_registry import BotRegistry
from nonebot_plugin_hermes.core.message_buffer import BufferedMessage, MessageBuffer
from nonebot_plugin_hermes.core.storage.image_cache import ImageCache
from nonebot_plugin_hermes.core.storage.image_fetcher import ImageFetcher
from nonebot_plugin_hermes.core.storage.message_store import MessageStore
from nonebot_plugin_hermes.handlers.commands import build_status_lines

_NOW = 5_000_000


@pytest.fixture
def _runtime(tmp_path):
    store = MessageStore(db_path=tmp_path / "messages.db")
    cache = ImageCache(cache_dir=tmp_path / "imgs", quota_bytes=1024 * 1024)
    fetcher = ImageFetcher(store=store, cache=cache)
    _mcp.message_buffer = MessageBuffer(store=store, fetcher=fetcher)
    _mcp.active_sessions = ActiveSessionManager(default_ttl_sec=300)
    _mcp.bot_registry = BotRegistry()
    yield
    _mcp.message_buffer = None
    _mcp.active_sessions = None
    _mcp.bot_registry = None
    store.close()


def _line(ts: int, group: str | None, user: str) -> BufferedMessage:
    return BufferedMessage(
        ts=ts,
        adapter="ob11",
        group_id=group,
        user_id=user,
        nickname=user,
        content="hi",
        image_urls=[],
        is_bot=False,
    )


def test_status_lines_report_buffer_counts(_runtime):
    _mcp.message_buffer.append(_line(_NOW - 3, "g1", "u1"))
    _mcp.message_buffer.append(_line(_NOW - 2, "g1", "u2"))
    _mcp.message_buffer.append(_line(_NOW - 1, None, "u9"))

    body = "\n".join(build_status_lines(_NOW))

    assert "💬 MessageBuffer: 3 条 / 2 个 bucket" in body
    assert "  - ob11/g1: 2" in body
    assert "  - ob11/@private:u9: 1" in body


def test_status_lines_on_empty_runtime(_runtime):
    body = "\n".join(build_status_lines(_NOW))

    assert "💬 MessageBuffer: 0 条 / 0 个 bucket" in body
    assert "📊 ActiveSessions: 0 个活跃" in body
    assert "🤖 BotRegistry: 0 个路由" in body


def test_status_lines_without_runtime_singletons():
    """MCP 关掉时单例是 None,status 仍要能出正文而不是抛异常。"""
    _mcp.message_buffer = None
    _mcp.active_sessions = None
    _mcp.bot_registry = None

    body = "\n".join(build_status_lines(_NOW))

    assert "🔍 Hermes Plugin M1-mem 状态" in body


def test_status_lines_include_active_session(_runtime):
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=_NOW)

    body = "\n".join(build_status_lines(_NOW))

    assert "📊 ActiveSessions: 1 个活跃" in body
    assert "ob11/g1 by u1" in body
