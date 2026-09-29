"""reactive 续发轮窗口裁剪:select_followup_window 纯函数 + handler 接线。"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from nonebot_plugin_hermes import mcp as _mcp
from nonebot_plugin_hermes.core.active_session import ActiveSessionManager
from nonebot_plugin_hermes.core.bot_registry import BotRegistry
from nonebot_plugin_hermes.core.hermes_client import ChatResult
from nonebot_plugin_hermes.core.inflight import InflightRegistry
from nonebot_plugin_hermes.core.message_buffer import BufferedMessage, MessageBuffer
from nonebot_plugin_hermes.core.prompt_builder import select_followup_window
from nonebot_plugin_hermes.core.storage.image_cache import ImageCache
from nonebot_plugin_hermes.core.storage.image_fetcher import ImageFetcher
from nonebot_plugin_hermes.core.storage.message_store import MessageStore


def _msg(ts: int, content: str, *, is_bot: bool = False, msg_id: int | None = None) -> BufferedMessage:
    return BufferedMessage(
        ts=ts,
        adapter="ob11",
        group_id="g1",
        user_id="u9" if is_bot else "u1",
        nickname="assistant" if is_bot else "member",
        content=content,
        is_bot=is_bot,
        id=msg_id,
    )


def _window(n: int, bot_at: int | None = None) -> list[BufferedMessage]:
    """构造 n 条新→旧消息;bot_at 指定第几条(0=最新)是 bot 发言。"""
    return [_msg(ts=1000 - i, content=f"m{i}", is_bot=(i == bot_at), msg_id=100 - i) for i in range(n)]


def test_limit_ge_len_returns_all():
    recent = _window(4)
    assert select_followup_window(recent, 4) == recent
    assert select_followup_window(recent, 10) == recent


def test_limit_zero_or_negative_disables_trim():
    recent = _window(6)
    assert select_followup_window(recent, 0) == recent
    assert select_followup_window(recent, -1) == recent


def test_basic_trim_keeps_newest():
    recent = _window(6)
    assert select_followup_window(recent, 3) == recent[:3]


def test_bot_line_inside_tail_not_duplicated():
    recent = _window(6, bot_at=1)
    result = select_followup_window(recent, 3)
    assert result == recent[:3]


def test_bot_line_outside_tail_is_pinned_after():
    recent = _window(6, bot_at=4)
    result = select_followup_window(recent, 3)
    assert result == recent[:3] + [recent[4]]


def test_no_bot_line_just_tail():
    recent = _window(6)
    assert select_followup_window(recent, 2) == recent[:2]


def test_multiple_bot_lines_pins_only_newest():
    recent = _window(8, bot_at=5)
    recent[6] = _msg(ts=994, content="m6", is_bot=True, msg_id=94)
    result = select_followup_window(recent, 3)
    assert result == recent[:3] + [recent[5]]


def test_empty_input():
    assert select_followup_window([], 3) == []


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


def _seed_buffer(n: int, bot_at: int | None = None) -> None:
    """写 n 条消息进 buffer,ts 递增(i=0 最旧)。bot_at 按写入序号指定 bot 行。"""
    for i in range(n):
        _mcp.message_buffer.append(_msg(ts=2000 + i, content=f"s{i}", is_bot=(i == bot_at)))


async def _run_turn(monkeypatch, *, explicit: bool) -> list[BufferedMessage]:
    """跑一发 reactive turn,返回传给 build_reactive_user_content 的 recent_messages。"""
    from nonebot_plugin_hermes.handlers import message as handler_mod

    captured: dict = {}

    def capture_build(**kwargs):
        captured["recent"] = list(kwargs["recent_messages"])
        return "stub-user-content"

    async def fake_chat(**kwargs):
        return ChatResult(raw_text="ok", structured={"should_reply": False})

    monkeypatch.setattr(handler_mod, "build_reactive_user_content", capture_build)
    monkeypatch.setattr(handler_mod.hermes_client, "chat", fake_chat)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    now = 9_000_000
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)
    await handler_mod._run_reactive_turn(
        bot=_fake_bot(),
        target=_FakeTarget(id="g1"),
        adapter_name="ob11",
        user_id="u1",
        group_id="g1",
        text="继续聊",
        image_urls=[],
        is_explicit_trigger=explicit,
        now_ms=now,
    )
    return captured["recent"]


@pytest.mark.asyncio
async def test_explicit_turn_gets_full_window(monkeypatch, _runtime):
    from nonebot_plugin_hermes.config import plugin_config

    monkeypatch.setattr(plugin_config, "hermes_reactive_followup_window", 3)
    _seed_buffer(6)
    recent = await _run_turn(monkeypatch, explicit=True)
    assert len(recent) == 6


@pytest.mark.asyncio
async def test_followup_turn_gets_trimmed_window(monkeypatch, _runtime):
    from nonebot_plugin_hermes.config import plugin_config

    monkeypatch.setattr(plugin_config, "hermes_reactive_followup_window", 3)
    _seed_buffer(6)  # 无 bot 行
    recent = await _run_turn(monkeypatch, explicit=False)
    assert [m.content for m in recent] == ["s5", "s4", "s3"]


@pytest.mark.asyncio
async def test_followup_turn_pins_bot_line(monkeypatch, _runtime):
    from nonebot_plugin_hermes.config import plugin_config

    monkeypatch.setattr(plugin_config, "hermes_reactive_followup_window", 3)
    _seed_buffer(6, bot_at=1)  # bot 行是第 2 老的,必在尾部 3 条之外
    recent = await _run_turn(monkeypatch, explicit=False)
    assert [m.content for m in recent] == ["s5", "s4", "s3", "s1"]
    assert recent[-1].is_bot


@pytest.mark.asyncio
async def test_followup_zero_config_disables_trim(monkeypatch, _runtime):
    from nonebot_plugin_hermes.config import plugin_config

    monkeypatch.setattr(plugin_config, "hermes_reactive_followup_window", 0)
    _seed_buffer(6)
    recent = await _run_turn(monkeypatch, explicit=False)
    assert len(recent) == 6


@pytest.mark.asyncio
async def test_oversized_history_warning_scans_full_window_not_trimmed(monkeypatch, _runtime):
    """oversized 检测必须扫 recent_full,而不是裁剪后的 recent —— 否则被裁掉的脏数据永远不告警。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as handler_mod

    monkeypatch.setattr(plugin_config, "hermes_reactive_followup_window", 3)
    monkeypatch.setattr(handler_mod, "logger", MagicMock())

    # 最老的一条(会被 window=3 裁掉)超长;其余正常长度。
    for i in range(6):
        content = "x" * 2001 if i == 0 else f"s{i}"
        _mcp.message_buffer.append(_msg(ts=2000 + i, content=content))

    await _run_turn(monkeypatch, explicit=False)

    warning_texts = [str(call.args[0]) for call in handler_mod.logger.warning.call_args_list]
    assert any("超长历史行" in text for text in warning_texts), warning_texts


@pytest.mark.asyncio
async def test_debug_log_reports_window_over_full_size(monkeypatch, _runtime):
    """window= 调试日志格式钉住:len(recent)/len(recent_full)。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as handler_mod

    monkeypatch.setattr(plugin_config, "hermes_reactive_followup_window", 3)
    monkeypatch.setattr(handler_mod, "logger", MagicMock())

    _seed_buffer(6)  # 无 bot 行
    await _run_turn(monkeypatch, explicit=False)

    debug_texts = [str(call.args[0]) for call in handler_mod.logger.debug.call_args_list]
    assert any("window=3/6" in text for text in debug_texts), debug_texts
