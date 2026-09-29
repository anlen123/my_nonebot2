"""消息 handler coalesce 行为集成测试。

直接调 _handle_reactive_path / _handle_passive_path,mock hermes_client.chat
让它 sleep 一段时间模拟慢上游,断言并发触发的 chat 调用次数被 coalesce。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from nonebot_plugin_hermes import mcp as _mcp
from nonebot_plugin_hermes.core.active_session import ActiveSessionManager
from nonebot_plugin_hermes.core.bot_registry import BotRegistry
from nonebot_plugin_hermes.core.inflight import MAX_REFIRE_DEPTH, InflightRegistry
from nonebot_plugin_hermes.core.message_buffer import BufferedMessage, MessageBuffer
from nonebot_plugin_hermes.core.storage.image_cache import ImageCache
from nonebot_plugin_hermes.core.storage.image_fetcher import ImageFetcher
from nonebot_plugin_hermes.core.storage.message_store import MessageStore


@dataclass
class _FakeTarget:
    id: str
    private: bool = False
    adapter: str = "ob11"


def _fake_bot(self_id: str = "999"):
    bot = MagicMock()
    bot.self_id = self_id
    return bot


@pytest.fixture(autouse=True)
def _setup_runtime(monkeypatch, tmp_path):
    """每个测试用例独立运行时单例(SQLite 存到 tmp_path,fixture 拆除时关库)。"""
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


def _make_chat_result(text: str = "ok", transport_error: bool = False, structured=None):
    """造一个 ChatResult-like。reactive 需要 structured;passive 用 raw_text。"""
    from nonebot_plugin_hermes.core.hermes_client import ChatResult

    return ChatResult(
        raw_text=text,
        media_urls=[],
        structured=structured or {"should_reply": True, "reply_text": text, "should_exit_active": False},
        parse_failed=False,
        is_transport_error=transport_error,
    )


@pytest.mark.asyncio
async def test_reactive_burst_coalesces_to_two_chat_calls(monkeypatch):
    """同一 group 上 5 条 burst,chat 实际被调 2 次(初发 + 一次合并重燃)。

    cooldown 默认 8s 会在 refire 入口拦住第二发(初发后 mark_bot_replied 立即触发),
    本测试聚焦 coalesce 机制本身,显式关 cooldown 隔离两件事。
    """
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as handler_mod

    monkeypatch.setattr(plugin_config, "hermes_reactive_post_reply_cooldown_sec", 0)

    # 用 wall-clock,因为 _refire 内部读 _now_ms() 做 is_active 校验
    now = int(time.time() * 1000)
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)

    chat_calls: list[int] = []

    async def slow_chat(**kwargs):
        chat_calls.append(len(chat_calls))
        await asyncio.sleep(0.1)
        return _make_chat_result(text=f"reply-{len(chat_calls)}")

    monkeypatch.setattr(handler_mod.hermes_client, "chat", slow_chat)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    target = _FakeTarget(id="g1", private=False)
    bot = _fake_bot()

    tasks = []
    for i in range(5):
        tasks.append(
            asyncio.create_task(
                handler_mod._handle_reactive_path(
                    bot=bot,
                    target=target,
                    adapter_name="ob11",
                    user_id="u1",
                    group_id="g1",
                    text=f"msg-{i}",
                    image_urls=[],
                    is_explicit_trigger=False,
                    now_ms=now + i,
                )
            )
        )
    await asyncio.gather(*tasks)
    await asyncio.sleep(0.3)

    assert len(chat_calls) == 2, f"got {len(chat_calls)} chat calls, expected 2"


@pytest.mark.asyncio
async def test_passive_private_burst_coalesces(monkeypatch):
    """私聊连发 3 条,chat 实际被调 2 次(初发 + 合并重燃)。"""
    from nonebot_plugin_hermes.core.hermes_client import ChatResult
    from nonebot_plugin_hermes.handlers import message as handler_mod

    chat_calls: list[int] = []

    async def slow_chat_passive(**kwargs):
        chat_calls.append(len(chat_calls))
        await asyncio.sleep(0.1)
        return ChatResult(
            raw_text=f"reply-{len(chat_calls)}",
            media_urls=[],
            structured=None,
            parse_failed=False,
            is_transport_error=False,
        )

    monkeypatch.setattr(handler_mod.hermes_client, "chat", slow_chat_passive)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    target = _FakeTarget(id="u1", private=True)
    bot = _fake_bot()
    now = 2_000_000

    tasks = []
    for i in range(3):
        tasks.append(
            asyncio.create_task(
                handler_mod._handle_passive_path(
                    bot=bot,
                    target=target,
                    adapter_name="ob11",
                    user_id="u1",
                    group_id=None,
                    text=f"msg-{i}",
                    image_urls=[],
                    is_private=True,
                    now_ms=now + i,
                )
            )
        )
    await asyncio.gather(*tasks)
    await asyncio.sleep(0.3)

    assert len(chat_calls) == 2, f"got {len(chat_calls)} chat calls, expected 2"


@pytest.mark.asyncio
async def test_image_only_passive_in_window_skips_chat(monkeypatch):
    """active window 内、非显式触发、纯图无文本 → 不进 chat(),只写 buffer。"""
    from nonebot_plugin_hermes.handlers import message as handler_mod

    now = 3_000_000
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)

    chat_mock = AsyncMock()
    monkeypatch.setattr(handler_mod.hermes_client, "chat", chat_mock)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    target = _FakeTarget(id="g1", private=False)
    bot = _fake_bot()

    await handler_mod._handle_reactive_path(
        bot=bot,
        target=target,
        adapter_name="ob11",
        user_id="u2",
        group_id="g1",
        text="",
        image_urls=["http://example.com/cat.jpg"],
        is_explicit_trigger=False,
        now_ms=now + 100,
    )

    chat_mock.assert_not_called()


@pytest.mark.asyncio
async def test_image_with_text_passive_in_window_does_call_chat(monkeypatch):
    """active window 内、非显式触发、图 + 任意非空文本 → 进 chat()。"""
    from nonebot_plugin_hermes.handlers import message as handler_mod

    now = 3_100_000
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)

    async def fake_chat(**kwargs):
        return _make_chat_result(text="ok")

    chat_mock = AsyncMock(side_effect=fake_chat)
    monkeypatch.setattr(handler_mod.hermes_client, "chat", chat_mock)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    target = _FakeTarget(id="g1", private=False)
    bot = _fake_bot()

    await handler_mod._handle_reactive_path(
        bot=bot,
        target=target,
        adapter_name="ob11",
        user_id="u2",
        group_id="g1",
        text="看",
        image_urls=["http://example.com/cat.jpg"],
        is_explicit_trigger=False,
        now_ms=now + 100,
    )

    chat_mock.assert_called_once()


@pytest.mark.asyncio
async def test_image_only_explicit_trigger_does_call_chat(monkeypatch):
    """显式触发(@bot)+ 纯图 → 进 chat()(门控豁免)。"""
    from nonebot_plugin_hermes.handlers import message as handler_mod

    now = 3_200_000
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)

    async def fake_chat(**kwargs):
        return _make_chat_result(text="ok")

    chat_mock = AsyncMock(side_effect=fake_chat)
    monkeypatch.setattr(handler_mod.hermes_client, "chat", chat_mock)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    target = _FakeTarget(id="g1", private=False)
    bot = _fake_bot()

    await handler_mod._handle_reactive_path(
        bot=bot,
        target=target,
        adapter_name="ob11",
        user_id="u2",
        group_id="g1",
        text="",
        image_urls=["http://example.com/cat.jpg"],
        is_explicit_trigger=True,
        now_ms=now + 100,
    )

    chat_mock.assert_called_once()


@pytest.mark.asyncio
async def test_text_only_passive_in_window_does_call_chat(monkeypatch):
    """active window 内、非显式触发、纯文本无图 → 进 chat()(门控不适用)。"""
    from nonebot_plugin_hermes.handlers import message as handler_mod

    now = 3_300_000
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)

    async def fake_chat(**kwargs):
        return _make_chat_result(text="ok")

    chat_mock = AsyncMock(side_effect=fake_chat)
    monkeypatch.setattr(handler_mod.hermes_client, "chat", chat_mock)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    target = _FakeTarget(id="g1", private=False)
    bot = _fake_bot()

    await handler_mod._handle_reactive_path(
        bot=bot,
        target=target,
        adapter_name="ob11",
        user_id="u2",
        group_id="g1",
        text="hello",
        image_urls=[],
        is_explicit_trigger=False,
        now_ms=now + 100,
    )

    chat_mock.assert_called_once()


@pytest.mark.asyncio
async def test_refire_depth_caps_at_max(monkeypatch):
    """持续 burst:链尾最多重燃 MAX_REFIRE_DEPTH 次,触顶后 warn + drop pending。

    每次 chat 都返回 should_reply=true 会写 last_bot_reply_at,默认 cooldown 会切断
    refire 链。本测试聚焦 depth cap 机制,显式关 cooldown 以隔离。
    """
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as handler_mod

    monkeypatch.setattr(plugin_config, "hermes_reactive_post_reply_cooldown_sec", 0)

    # 用 wall-clock,因为 _refire 内部读 _now_ms() 做 is_active 校验
    now = int(time.time() * 1000)
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)

    chat_calls: list[int] = []
    warning_messages: list[str] = []

    # Capture loguru warnings (nonebot uses loguru, not stdlib logging)
    original_warning = handler_mod.logger.warning

    def capture_warning(msg, *args, **kwargs):
        warning_messages.append(str(msg))
        return original_warning(msg, *args, **kwargs)

    monkeypatch.setattr(handler_mod.logger, "warning", capture_warning)

    async def chat_then_queue_more(**kwargs):
        chat_calls.append(len(chat_calls))
        # 每次 chat 跑期间,模拟有新消息塞 pending
        _mcp.inflight.try_enter(
            ("ob11", "group:g1"),
            BufferedMessage(
                ts=now + 1000 * (len(chat_calls) + 1),
                adapter="ob11",
                group_id="g1",
                user_id="u2",
                nickname="u2",
                content=f"queued-{len(chat_calls)}",
                image_urls=[],
                reply_to_ts=None,
                is_bot=False,
            ),
            is_explicit_trigger=False,
            original_msg_id=None,
            now_ms=now + 1000 * (len(chat_calls) + 1),
        )
        return _make_chat_result(text=f"reply-{len(chat_calls)}")

    monkeypatch.setattr(handler_mod.hermes_client, "chat", chat_then_queue_more)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    target = _FakeTarget(id="g1", private=False)
    bot = _fake_bot()

    await handler_mod._handle_reactive_path(
        bot=bot,
        target=target,
        adapter_name="ob11",
        user_id="u1",
        group_id="g1",
        text="trigger",
        image_urls=[],
        is_explicit_trigger=True,
        now_ms=now,
    )
    await asyncio.sleep(0.5)

    # 初发 1 + 重燃 3 = 4 次 chat,第 4 次重燃 depth=4 > MAX_REFIRE_DEPTH=3,被丢
    assert len(chat_calls) == 1 + MAX_REFIRE_DEPTH, f"got {len(chat_calls)}, expected {1 + MAX_REFIRE_DEPTH}"
    assert any("refire depth exceeded" in msg for msg in warning_messages), (
        f"Expected 'refire depth exceeded' warning, got: {warning_messages}"
    )


@pytest.mark.asyncio
async def test_transport_error_does_not_refire(monkeypatch):
    """上一发 is_transport_error=True 且 pending 已设 → 不重燃,pending 被丢。"""
    from nonebot_plugin_hermes.handlers import message as handler_mod

    now = 5_000_000
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)

    chat_calls: list[int] = []

    async def transport_err_chat(**kwargs):
        chat_calls.append(len(chat_calls))
        if len(chat_calls) == 1:
            await asyncio.sleep(0.05)
            return _make_chat_result(text="err", transport_error=True)
        return _make_chat_result(text="ok")

    monkeypatch.setattr(handler_mod.hermes_client, "chat", transport_err_chat)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    target = _FakeTarget(id="g1", private=False)
    bot = _fake_bot()

    main = asyncio.create_task(
        handler_mod._handle_reactive_path(
            bot=bot,
            target=target,
            adapter_name="ob11",
            user_id="u1",
            group_id="g1",
            text="trigger",
            image_urls=[],
            is_explicit_trigger=True,
            now_ms=now,
        )
    )
    await asyncio.sleep(0.01)
    pending_task = asyncio.create_task(
        handler_mod._handle_reactive_path(
            bot=bot,
            target=target,
            adapter_name="ob11",
            user_id="u1",
            group_id="g1",
            text="follow-up",
            image_urls=[],
            is_explicit_trigger=False,
            now_ms=now + 10,
        )
    )
    await asyncio.gather(main, pending_task)
    await asyncio.sleep(0.2)

    assert len(chat_calls) == 1, f"got {len(chat_calls)} chat calls; expected 1 (no refire on transport_error)"
    assert _mcp.inflight.take_pending(("ob11", "group:g1")) is None


@pytest.mark.asyncio
async def test_exception_in_turn_does_not_refire(monkeypatch):
    """_run_*_turn 抛 Exception → 不重燃,exception 仍冒泡。"""
    from nonebot_plugin_hermes.handlers import message as handler_mod

    now = 6_000_000
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)

    chat_calls: list[int] = []

    async def boom_chat(**kwargs):
        chat_calls.append(len(chat_calls))
        await asyncio.sleep(0.05)
        raise RuntimeError("boom")

    monkeypatch.setattr(handler_mod.hermes_client, "chat", boom_chat)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    target = _FakeTarget(id="g1", private=False)
    bot = _fake_bot()

    main = asyncio.create_task(
        handler_mod._handle_reactive_path(
            bot=bot,
            target=target,
            adapter_name="ob11",
            user_id="u1",
            group_id="g1",
            text="trigger",
            image_urls=[],
            is_explicit_trigger=True,
            now_ms=now,
        )
    )
    await asyncio.sleep(0.01)
    pending_task = asyncio.create_task(
        handler_mod._handle_reactive_path(
            bot=bot,
            target=target,
            adapter_name="ob11",
            user_id="u1",
            group_id="g1",
            text="follow",
            image_urls=[],
            is_explicit_trigger=False,
            now_ms=now + 10,
        )
    )
    with pytest.raises(RuntimeError, match="boom"):
        await main
    await pending_task
    await asyncio.sleep(0.1)

    assert len(chat_calls) == 1, "no refire on exception"
    assert _mcp.inflight.take_pending(("ob11", "group:g1")) is None


@pytest.mark.asyncio
async def test_post_reply_cooldown_skips_non_explicit_trigger(monkeypatch):
    """B: bot 刚 mark_bot_replied 过、当前消息非显式触发 → 冷却内 chat 不被调。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as handler_mod

    monkeypatch.setattr(plugin_config, "hermes_reactive_post_reply_cooldown_sec", 8)

    now = 8_000_000
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)
    _mcp.active_sessions.mark_bot_replied("ob11", "g1", now_ms=now)

    chat_mock = AsyncMock()
    monkeypatch.setattr(handler_mod.hermes_client, "chat", chat_mock)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    target = _FakeTarget(id="g1", private=False)
    bot = _fake_bot()

    # 冷却中(now+1000 < now+8000)、非显式触发的旁观消息 → skip
    await handler_mod._handle_reactive_path(
        bot=bot,
        target=target,
        adapter_name="ob11",
        user_id="u2",
        group_id="g1",
        text="随便聊聊",
        image_urls=[],
        is_explicit_trigger=False,
        now_ms=now + 1000,
    )

    chat_mock.assert_not_called()


@pytest.mark.asyncio
async def test_post_reply_cooldown_bypassed_by_explicit_trigger(monkeypatch):
    """B: 冷却内但消息是显式 @bot 触发 → 必须立刻进 chat,不能被冷却拦下。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as handler_mod

    monkeypatch.setattr(plugin_config, "hermes_reactive_post_reply_cooldown_sec", 8)

    now = 8_100_000
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)
    _mcp.active_sessions.mark_bot_replied("ob11", "g1", now_ms=now)

    async def fake_chat(**kwargs):
        return _make_chat_result(text="ok")

    chat_mock = AsyncMock(side_effect=fake_chat)
    monkeypatch.setattr(handler_mod.hermes_client, "chat", chat_mock)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    target = _FakeTarget(id="g1", private=False)
    bot = _fake_bot()

    await handler_mod._handle_reactive_path(
        bot=bot,
        target=target,
        adapter_name="ob11",
        user_id="u2",
        group_id="g1",
        text="@bot 再问个事",
        image_urls=[],
        is_explicit_trigger=True,  # 显式触发应绕过 B
        now_ms=now + 1000,
    )

    chat_mock.assert_called_once()


@pytest.mark.asyncio
async def test_post_reply_cooldown_expires_after_window(monkeypatch):
    """B: 冷却窗已过(elapsed >= cooldown_ms)、非显式触发 → 正常进 chat。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as handler_mod

    monkeypatch.setattr(plugin_config, "hermes_reactive_post_reply_cooldown_sec", 8)

    now = 8_200_000
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)
    _mcp.active_sessions.mark_bot_replied("ob11", "g1", now_ms=now)

    async def fake_chat(**kwargs):
        return _make_chat_result(text="ok")

    chat_mock = AsyncMock(side_effect=fake_chat)
    monkeypatch.setattr(handler_mod.hermes_client, "chat", chat_mock)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    target = _FakeTarget(id="g1", private=False)
    bot = _fake_bot()

    # 冷却 8s 已过(now+9000 > now+8000)
    await handler_mod._handle_reactive_path(
        bot=bot,
        target=target,
        adapter_name="ob11",
        user_id="u2",
        group_id="g1",
        text="过了一会儿",
        image_urls=[],
        is_explicit_trigger=False,
        now_ms=now + 9000,
    )

    chat_mock.assert_called_once()


@pytest.mark.asyncio
async def test_post_reply_cooldown_disabled_when_zero(monkeypatch):
    """B: cooldown_sec=0 时冷却完全关闭,任何 mark 都不会拦截后续消息。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as handler_mod

    monkeypatch.setattr(plugin_config, "hermes_reactive_post_reply_cooldown_sec", 0)

    now = 8_300_000
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)
    _mcp.active_sessions.mark_bot_replied("ob11", "g1", now_ms=now)

    async def fake_chat(**kwargs):
        return _make_chat_result(text="ok")

    chat_mock = AsyncMock(side_effect=fake_chat)
    monkeypatch.setattr(handler_mod.hermes_client, "chat", chat_mock)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    target = _FakeTarget(id="g1", private=False)
    bot = _fake_bot()

    await handler_mod._handle_reactive_path(
        bot=bot,
        target=target,
        adapter_name="ob11",
        user_id="u2",
        group_id="g1",
        text="紧贴上一回",
        image_urls=[],
        is_explicit_trigger=False,
        now_ms=now + 100,  # 100ms 就紧跟,但 cooldown=0 应不拦
    )

    chat_mock.assert_called_once()


@pytest.mark.asyncio
async def test_reactive_turn_marks_bot_replied_after_send(monkeypatch):
    """B.2: 一发成功的 reactive 回复 send 后,ActiveSession.last_bot_reply_at 被写入。

    last_bot_reply_at 是 send 完的 wall clock,不是 entry now_ms ──
    见 test_mark_bot_replied_uses_wall_clock_after_slow_chat 的注释。
    本测试 mock _now_ms 钉死 send 时的 wall clock,直接断言。
    """
    from nonebot_plugin_hermes.handlers import message as handler_mod

    now = 8_400_000
    send_wall_clock = now + 50  # 模拟 send 比 entry 晚 50ms
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)
    assert _mcp.active_sessions.get("ob11", "g1").last_bot_reply_at == 0

    async def fake_chat(**kwargs):
        return _make_chat_result(text="hi back")

    monkeypatch.setattr(handler_mod.hermes_client, "chat", AsyncMock(side_effect=fake_chat))
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))
    monkeypatch.setattr(handler_mod, "_now_ms", lambda: send_wall_clock)

    target = _FakeTarget(id="g1", private=False)
    bot = _fake_bot()

    await handler_mod._handle_reactive_path(
        bot=bot,
        target=target,
        adapter_name="ob11",
        user_id="u1",
        group_id="g1",
        text="hi",
        image_urls=[],
        is_explicit_trigger=True,
        now_ms=now,
    )

    assert _mcp.active_sessions.get("ob11", "g1").last_bot_reply_at == send_wall_clock


@pytest.mark.asyncio
async def test_run_reactive_turn_suppresses_submit_reply_restating_the_push(monkeypatch):
    """同一 chat() agent loop 内先 push,又返 should_reply=True 且**内容是复述** →
    抑制第二条,否则群里收到两条一样的话。

    注意判据是"复述",不是"push 过":内容不同的第二条必须放行(见下面两个用例)。
    """
    from nonebot_plugin_hermes.handlers import message as handler_mod

    now = 9_000_000
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)

    async def chat_pushes_then_replies(**kwargs):
        # 模拟 agent loop 内调 push_message 的副作用:写 mark_bot_replied(带原文)
        _mcp.active_sessions.mark_bot_replied("ob11", "g1", now_ms=now + 100, text="duplicate body")
        # 紧接着又返 should_reply=True 同主题答案
        return _make_chat_result(
            structured={
                "should_reply": True,
                "reply_text": "duplicate body",
                "should_exit_active": False,
            },
        )

    monkeypatch.setattr(handler_mod.hermes_client, "chat", chat_pushes_then_replies)
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(handler_mod, "send_text_with_media", send_mock)

    target = _FakeTarget(id="g1", private=False)
    bot = _fake_bot()

    await handler_mod._handle_reactive_path(
        bot=bot,
        target=target,
        adapter_name="ob11",
        user_id="u1",
        group_id="g1",
        text="@bot 分析这图",
        image_urls=[],
        is_explicit_trigger=True,  # 显式触发,普通 cooldown 不生效;本闸门必须独立生效
        now_ms=now,
    )

    # 测试 mock 里的"push" 没真调 send,所以零次 send 才表示 reactive 那条被抑制了
    send_mock.assert_not_called()


@pytest.mark.asyncio
async def test_run_reactive_turn_still_sends_when_no_mid_turn_push(monkeypatch):
    """控制组:turn 内没有外部 mark_bot_replied → should_reply=True 应正常发送,
    防止同 turn 防重复闸门误杀正常 reactive 回复。
    """
    from nonebot_plugin_hermes.handlers import message as handler_mod

    now = 9_100_000
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)

    async def chat_replies_only(**kwargs):
        return _make_chat_result(
            structured={
                "should_reply": True,
                "reply_text": "normal reply",
                "should_exit_active": False,
            },
        )

    monkeypatch.setattr(handler_mod.hermes_client, "chat", chat_replies_only)
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(handler_mod, "send_text_with_media", send_mock)

    target = _FakeTarget(id="g1", private=False)
    bot = _fake_bot()

    await handler_mod._handle_reactive_path(
        bot=bot,
        target=target,
        adapter_name="ob11",
        user_id="u1",
        group_id="g1",
        text="@bot",
        image_urls=[],
        is_explicit_trigger=True,
        now_ms=now,
    )

    send_mock.assert_called_once()


@pytest.mark.asyncio
async def test_refire_respects_post_reply_cooldown(monkeypatch):
    """T1 跑期间 mark_bot_replied 被(外部, 如 MCP push_message)写入, T2 已被存为
    pending → T1 完成后 _refire 起 T2, refire 入口必须复用同款 cooldown 闸门,
    不调 chat()。 否则 T1 在 agent loop 里已经答了, T2 refire 又答一遍同主题。
    """
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as handler_mod

    monkeypatch.setattr(plugin_config, "hermes_reactive_post_reply_cooldown_sec", 8)

    # 用 wall-clock,因为 _refire 内部读 _now_ms()
    now = int(time.time() * 1000)
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)

    chat_calls: list[dict] = []

    async def chat_then_external_mark(**kwargs):
        chat_calls.append(kwargs)
        # 模拟 T1 在 chat 跑期间 Hermes 通过 push_message 工具调用,引发外部 mark_bot_replied
        # (类似 push_message_impl 现在的副作用)
        if len(chat_calls) == 1:
            await asyncio.sleep(0.05)
            _mcp.active_sessions.mark_bot_replied("ob11", "g1", now_ms=now + 30)
            # T1 自己的 submit_decision 返 silent(should_reply=false),
            # 触发 _run_reactive_turn 早返;_refire 才会成为唯一可能产生第二次 chat 的路径。
            return _make_chat_result(
                structured={"should_reply": False, "reply_text": "", "should_exit_active": False},
            )
        # 若闸门没生效,T2 refire 会落到这里再次调 chat
        return _make_chat_result(text="dup-reply")

    monkeypatch.setattr(handler_mod.hermes_client, "chat", chat_then_external_mark)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    target = _FakeTarget(id="g1", private=False)
    bot = _fake_bot()

    main = asyncio.create_task(
        handler_mod._handle_reactive_path(
            bot=bot,
            target=target,
            adapter_name="ob11",
            user_id="u1",
            group_id="g1",
            text="@bot 问题",
            image_urls=[],
            is_explicit_trigger=True,
            now_ms=now,
        )
    )
    await asyncio.sleep(0.01)
    follow = asyncio.create_task(
        handler_mod._handle_reactive_path(
            bot=bot,
            target=target,
            adapter_name="ob11",
            user_id="u2",
            group_id="g1",
            text="跟话",
            image_urls=[],
            is_explicit_trigger=False,
            now_ms=now + 10,
        )
    )
    await asyncio.gather(main, follow)
    await asyncio.sleep(0.3)

    assert len(chat_calls) == 1, (
        f"refire should be blocked by cooldown (last_bot_reply_at written mid-T1), got {len(chat_calls)} chat calls"
    )


@pytest.mark.asyncio
async def test_mark_bot_replied_uses_wall_clock_after_slow_chat(monkeypatch):
    """chat() 任意长耗时(上游重试 / 压缩 / 慢工具)后,last_bot_reply_at 必须是
    send 完成时的 wall clock,不是 _run_reactive_turn 的入参 now_ms。 复用 stale
    入参会让 cooldown 闸门算出的 elapsed 失真,放过本该窗内挡住的 refire。
    """
    from nonebot_plugin_hermes.handlers import message as handler_mod

    entry_ms = 1_000_000
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=entry_ms)

    async def slow_chat(**kwargs):
        return _make_chat_result(text="ok")  # should_reply=True

    monkeypatch.setattr(handler_mod.hermes_client, "chat", slow_chat)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    # 模拟 chat 跑了 60s, wall clock 跳到 entry + 60s
    fake_send_time = entry_ms + 60_000
    monkeypatch.setattr(handler_mod, "_now_ms", lambda: fake_send_time)

    await handler_mod._run_reactive_turn(
        bot=_fake_bot(),
        target=_FakeTarget(id="g1", private=False),
        adapter_name="ob11",
        user_id="u1",
        group_id="g1",
        text="hi",
        image_urls=[],
        is_explicit_trigger=True,
        now_ms=entry_ms,  # entry 时的 wall clock(早 60s)
    )

    sess = _mcp.active_sessions.get("ob11", "g1")
    assert sess.last_bot_reply_at == fake_send_time, (
        f"send 完后 last_bot_reply_at 应该用 wall clock {fake_send_time}, "
        f"实际 {sess.last_bot_reply_at}(看起来复用了 entry now_ms,会让后续 refire 误以为'很久之前回复过')"
    )


@pytest.mark.asyncio
async def test_reactive_transport_error_sends_friendly_fallback(monkeypatch):
    """Hermes 上游 5xx / transport error 时 raw_text 是服务端错误信息原文,
    plugin 不该把它当 LLM 输出转发到群里, 应该走 config 里的友好兜底文本。
    """
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.core.hermes_client import ChatResult
    from nonebot_plugin_hermes.handlers import message as handler_mod

    monkeypatch.setattr(plugin_config, "hermes_transport_error_fallback_text", "嗯…我这边遇到点状况")

    now = 10_000_000
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)

    async def chat_502(**kwargs):
        # 模拟 Hermes 返 502,body 是服务端英文错误信息(走 hermes_client 后变成
        # raw_text + parse_failed=True + is_transport_error=True)
        return ChatResult(
            raw_text="Model generated invalid tool call: mcp_nonebot_bridge_push_message",
            media_urls=[],
            structured=None,
            parse_failed=True,
            is_transport_error=True,
        )

    monkeypatch.setattr(handler_mod.hermes_client, "chat", chat_502)
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(handler_mod, "send_text_with_media", send_mock)

    target = _FakeTarget(id="g1", private=False)
    bot = _fake_bot()

    await handler_mod._handle_reactive_path(
        bot=bot,
        target=target,
        adapter_name="ob11",
        user_id="u1",
        group_id="g1",
        text="@bot ?",
        image_urls=[],
        is_explicit_trigger=True,
        now_ms=now,
    )

    send_mock.assert_called_once()
    kwargs = send_mock.call_args.kwargs
    assert kwargs["text"] == "嗯…我这边遇到点状况"
    assert kwargs["at_user_id"] == "u1"


@pytest.mark.asyncio
async def test_reactive_transport_error_silent_when_fallback_empty(monkeypatch):
    """空 fallback_text → 完全静默, 不发任何内容(用户偏好"宁静"时的逃生口)。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.core.hermes_client import ChatResult
    from nonebot_plugin_hermes.handlers import message as handler_mod

    monkeypatch.setattr(plugin_config, "hermes_transport_error_fallback_text", "")

    now = 10_100_000
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)

    async def chat_502(**kwargs):
        return ChatResult(
            raw_text="upstream error",
            media_urls=[],
            structured=None,
            parse_failed=True,
            is_transport_error=True,
        )

    monkeypatch.setattr(handler_mod.hermes_client, "chat", chat_502)
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(handler_mod, "send_text_with_media", send_mock)

    await handler_mod._handle_reactive_path(
        bot=bot if (bot := _fake_bot()) else None,
        target=_FakeTarget(id="g1", private=False),
        adapter_name="ob11",
        user_id="u1",
        group_id="g1",
        text="@bot ?",
        image_urls=[],
        is_explicit_trigger=True,
        now_ms=now,
    )

    send_mock.assert_not_called()


@pytest.mark.asyncio
async def test_reactive_parse_failed_non_transport_still_sends_raw_text(monkeypatch):
    """回归: parse_failed=True 但 is_transport_error=False 时(LLM 真说了点啥但
    structured 解不出来), 仍维持原 raw_text 兜底,不被新闸门误伤。
    """
    from nonebot_plugin_hermes.core.hermes_client import ChatResult
    from nonebot_plugin_hermes.handlers import message as handler_mod

    now = 10_200_000
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)

    async def chat_malformed(**kwargs):
        # LLM 自己输出了文本但 JSON5 解析失败 ── raw_text 是 LLM 的真实文本,有用
        return ChatResult(
            raw_text="嗯应该是这样吧, 但我也不太确定",
            media_urls=[],
            structured=None,
            parse_failed=True,
            is_transport_error=False,
        )

    monkeypatch.setattr(handler_mod.hermes_client, "chat", chat_malformed)
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(handler_mod, "send_text_with_media", send_mock)

    await handler_mod._handle_reactive_path(
        bot=_fake_bot(),
        target=_FakeTarget(id="g1", private=False),
        adapter_name="ob11",
        user_id="u1",
        group_id="g1",
        text="@bot ?",
        image_urls=[],
        is_explicit_trigger=True,
        now_ms=now,
    )

    send_mock.assert_called_once()
    kwargs = send_mock.call_args.kwargs
    assert kwargs["text"] == "嗯应该是这样吧, 但我也不太确定"


@pytest.mark.asyncio
async def test_passive_transport_error_sends_friendly_fallback(monkeypatch):
    """passive 私聊路径同款保护: 上游 transport_error → 友好兜底, 不发原始 raw_text。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.core.hermes_client import ChatResult
    from nonebot_plugin_hermes.handlers import message as handler_mod

    monkeypatch.setattr(plugin_config, "hermes_transport_error_fallback_text", "稍后再问一次")

    async def chat_502(**kwargs):
        return ChatResult(
            raw_text="Internal Server Error",
            media_urls=[],
            structured=None,
            parse_failed=False,
            is_transport_error=True,
        )

    monkeypatch.setattr(handler_mod.hermes_client, "chat", chat_502)
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(handler_mod, "send_text_with_media", send_mock)

    await handler_mod._handle_passive_path(
        bot=_fake_bot(),
        target=_FakeTarget(id="u1", private=True),
        adapter_name="ob11",
        user_id="u1",
        group_id=None,
        text="hi",
        image_urls=[],
        is_private=True,
        now_ms=11_000_000,
    )

    send_mock.assert_called_once()
    kwargs = send_mock.call_args.kwargs
    assert kwargs["text"] == "稍后再问一次"
    # 私聊不 @
    assert kwargs["at_user_id"] is None


@pytest.mark.asyncio
async def test_refire_when_active_session_expired(monkeypatch):
    """重燃时 session 已过期 → _run_reactive_turn 返回 None,registry 干净 exit,不抛。"""
    from nonebot_plugin_hermes.handlers import message as handler_mod

    now = 7_000_000
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)

    chat_calls: list[int] = []

    async def chat_and_expire(**kwargs):
        chat_calls.append(len(chat_calls))
        if len(chat_calls) == 1:
            await asyncio.sleep(0.05)
        # 第一发完成时:把 session 主动 end,让重燃看到 get_if_active=None
        _mcp.active_sessions.end("ob11", "g1")
        return _make_chat_result(text=f"reply-{len(chat_calls)}")

    monkeypatch.setattr(handler_mod.hermes_client, "chat", chat_and_expire)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    target = _FakeTarget(id="g1", private=False)
    bot = _fake_bot()

    main = asyncio.create_task(
        handler_mod._handle_reactive_path(
            bot=bot,
            target=target,
            adapter_name="ob11",
            user_id="u1",
            group_id="g1",
            text="trigger",
            image_urls=[],
            is_explicit_trigger=True,
            now_ms=now,
        )
    )
    await asyncio.sleep(0.01)
    follow = asyncio.create_task(
        handler_mod._handle_reactive_path(
            bot=bot,
            target=target,
            adapter_name="ob11",
            user_id="u1",
            group_id="g1",
            text="follow",
            image_urls=[],
            is_explicit_trigger=False,
            now_ms=now + 10,
        )
    )
    await asyncio.gather(main, follow)
    await asyncio.sleep(0.2)

    # 主发 1 次 chat;重燃跑了但 _run_reactive_turn 立刻返回 None(session ended),
    # 不再次调 chat。
    assert len(chat_calls) == 1
    # registry 已 exit
    assert (
        _mcp.inflight.try_enter(
            ("ob11", "group:g1"),
            BufferedMessage(
                ts=now + 9999,
                adapter="ob11",
                group_id="g1",
                user_id="u1",
                nickname="u1",
                content="",
                image_urls=[],
                reply_to_ts=None,
                is_bot=False,
            ),
            is_explicit_trigger=False,
            original_msg_id=None,
            now_ms=now + 9999,
        )
        == "entered"
    )


@pytest.mark.asyncio
async def test_mid_turn_push_without_media_still_delivers_decision_image(monkeypatch):
    """中途 push 只发出了文本(图是主机本地路径,投不出去)→ submit_decision 里
    能投的图必须补发,不能被同 turn 去重闸门整条抑制。

    线上序列:push_message(text, image_urls=['/root/.hermes/cache/…jpeg']) 发出文本、
    图被跳过 → submit_decision 带着网关内联好的 data URL → 闸门抑制 → 用户没收到图。
    """
    from nonebot_plugin_hermes.core.hermes_client import ChatResult
    from nonebot_plugin_hermes.handlers import message as handler_mod

    now = 20_000_000
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)
    data_url = "data:image/jpeg;base64," + __import__("base64").b64encode(b"\xff\xd8\xff" + b"\x00" * 64).decode()

    async def chat_then_push(**kwargs):
        # 模拟 agent loop 里 push_message 先答了文本、没投出任何媒体
        _mcp.active_sessions.mark_bot_replied("ob11", "g1", now_ms=now + 1_000, media_count=0, text="图来啦")
        return ChatResult(
            raw_text="x",
            structured={"should_reply": True, "reply_text": f"图来啦 ![image]({data_url})"},
        )

    monkeypatch.setattr(handler_mod.hermes_client, "chat", chat_then_push)
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(handler_mod, "send_text_with_media", send_mock)

    await handler_mod._run_reactive_turn(
        bot=_fake_bot(),
        target=_FakeTarget(id="g1", private=False),
        adapter_name="ob11",
        user_id="u1",
        group_id="g1",
        text="再来个漫画风的",
        image_urls=[],
        is_explicit_trigger=True,
        now_ms=now,
    )

    send_mock.assert_awaited_once()
    kwargs = send_mock.await_args.kwargs
    assert kwargs["media_urls"] == [data_url], "图必须补发"
    assert kwargs["text"] == "", "文本已经由 push 发过,不能重复"
    assert kwargs["at_user_id"] is None


@pytest.mark.asyncio
async def test_mid_turn_push_with_media_still_suppresses(monkeypatch):
    """中途 push 已经投出了媒体 → submit_decision 属纯重复,照旧整条抑制。"""
    from nonebot_plugin_hermes.core.hermes_client import ChatResult
    from nonebot_plugin_hermes.handlers import message as handler_mod

    now = 21_000_000
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)

    async def chat_then_push(**kwargs):
        _mcp.active_sessions.mark_bot_replied("ob11", "g1", now_ms=now + 1_000, media_count=1, text="图来啦")
        return ChatResult(
            raw_text="x",
            structured={"should_reply": True, "reply_text": "图来啦 ![image](https://example.com/a.png)"},
        )

    monkeypatch.setattr(handler_mod.hermes_client, "chat", chat_then_push)
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(handler_mod, "send_text_with_media", send_mock)

    await handler_mod._run_reactive_turn(
        bot=_fake_bot(),
        target=_FakeTarget(id="g1", private=False),
        adapter_name="ob11",
        user_id="u1",
        group_id="g1",
        text="再来一张",
        image_urls=[],
        is_explicit_trigger=True,
        now_ms=now,
    )

    send_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# 同 turn push + submit_decision:只有"复述"才抑制
#
# 旧规则是"本 turn 内 push 过就整条抑制",把最自然的用法(push 一句「在查」、
# decision 给真答案)也一起打死 —— 答案被静默吞掉,agent 还以为发出去了。
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mid_turn_push_then_different_answer_sends_both(monkeypatch):
    """push「在查,稍等」+ decision 给真答案 → 答案必须发出去。"""
    from nonebot_plugin_hermes.core.hermes_client import ChatResult
    from nonebot_plugin_hermes.handlers import message as handler_mod

    now = 22_000_000
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)

    async def chat_acks_then_answers(**kwargs):
        _mcp.active_sessions.mark_bot_replied("ob11", "g1", now_ms=now + 500, text="稍等,我查一下")
        return ChatResult(
            raw_text="x",
            structured={"should_reply": True, "reply_text": "查到了:明天下午三点开始"},
        )

    monkeypatch.setattr(handler_mod.hermes_client, "chat", chat_acks_then_answers)
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(handler_mod, "send_text_with_media", send_mock)

    await handler_mod._run_reactive_turn(
        bot=_fake_bot(),
        target=_FakeTarget(id="g1", private=False),
        adapter_name="ob11",
        user_id="u1",
        group_id="g1",
        text="@bot 几点开始",
        image_urls=[],
        is_explicit_trigger=True,
        now_ms=now,
    )

    send_mock.assert_awaited_once()
    assert send_mock.await_args.kwargs["text"] == "查到了:明天下午三点开始"


@pytest.mark.asyncio
async def test_mid_turn_push_quoted_in_a_longer_reply_still_sends(monkeypatch):
    """decision 把刚 push 的话整段引用了、但后面补了新内容 → 仍要发。

    包含关系不能当复述判据:引用+补充是合法的第二条。
    """
    from nonebot_plugin_hermes.core.hermes_client import ChatResult
    from nonebot_plugin_hermes.handlers import message as handler_mod

    now = 23_000_000
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)
    pushed = "第一批结果:A、B"

    async def chat_quotes_then_adds(**kwargs):
        _mcp.active_sessions.mark_bot_replied("ob11", "g1", now_ms=now + 500, text=pushed)
        return ChatResult(
            raw_text="x",
            structured={
                "should_reply": True,
                "reply_text": f"刚发的「{pushed}」之外,还查到 C 和 D,其中 D 需要你确认一下权限",
            },
        )

    monkeypatch.setattr(handler_mod.hermes_client, "chat", chat_quotes_then_adds)
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(handler_mod, "send_text_with_media", send_mock)

    await handler_mod._run_reactive_turn(
        bot=_fake_bot(),
        target=_FakeTarget(id="g1", private=False),
        adapter_name="ob11",
        user_id="u1",
        group_id="g1",
        text="@bot 查一下",
        image_urls=[],
        is_explicit_trigger=True,
        now_ms=now,
    )

    send_mock.assert_awaited_once()
    assert "C 和 D" in send_mock.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_mid_turn_push_with_only_punctuation_diff_is_still_a_restatement(monkeypatch):
    """只差标点 / 空白 / 大小写 → 仍算复述,抑制。"""
    from nonebot_plugin_hermes.core.hermes_client import ChatResult
    from nonebot_plugin_hermes.handlers import message as handler_mod

    now = 24_000_000
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)

    async def chat_restates(**kwargs):
        _mcp.active_sessions.mark_bot_replied("ob11", "g1", now_ms=now + 500, text="任务已经跑完了")
        return ChatResult(
            raw_text="x",
            structured={"should_reply": True, "reply_text": "任务已经跑完了!"},
        )

    monkeypatch.setattr(handler_mod.hermes_client, "chat", chat_restates)
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(handler_mod, "send_text_with_media", send_mock)

    await handler_mod._run_reactive_turn(
        bot=_fake_bot(),
        target=_FakeTarget(id="g1", private=False),
        adapter_name="ob11",
        user_id="u1",
        group_id="g1",
        text="@bot 跑完了吗",
        image_urls=[],
        is_explicit_trigger=True,
        now_ms=now,
    )

    send_mock.assert_not_awaited()


def test_is_restatement_thresholds():
    """判据刻意很窄:失败方向选「宁可多发一条,不可吞答案」。"""
    from nonebot_plugin_hermes.handlers.message import _is_restatement

    assert _is_restatement("一样的话", "一样的话")
    assert _is_restatement("一样的话", " 一样的话!! ")
    # 引用 + 补充新内容 → 放行
    assert not _is_restatement("在查", "在查,结果是明天下午三点,地点在 B 座")
    # 完全不同 → 放行
    assert not _is_restatement("稍等", "答案是 42")
    # 没记到 push 原文(老 session / 外部调用)→ 放行,绝不因为不知道就吞
    assert not _is_restatement("", "任何内容")
