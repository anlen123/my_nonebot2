"""Unit tests for mcp/tools/push_message.py.

Early-return paths (empty payload, no active session, unknown target) are
exercised without mocking send_text_with_media or get_bot — execution stops
before those calls.  The success path and the bot-offline path require
lightweight monkeypatching.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nonebot_plugin_hermes.core.active_session import ActiveSessionManager
from nonebot_plugin_hermes.core.bot_registry import BotRegistry
from nonebot_plugin_hermes.core.message_buffer import BufferedMessage
from nonebot_plugin_hermes.core.routing import CallerScope
from nonebot_plugin_hermes.mcp.tools.push_message import (
    PushMessageInput,
    PushMessageResult,
    push_message_impl,
)

# 这些用例测的是工具自身的行为,不是反向通道的范围收敛(那在 test_mcp_scope.py),
# 所以统一给开发模式 scope。scope 是必填参数:漏传是 TypeError 而不是静默放行。
_ANY_SCOPE = CallerScope.dev()


class _RecordingBuffer:
    """轻量 MessageBuffer 桩,只记 append。

    真 MessageBuffer 依赖 MessageStore + ImageFetcher(SQLite + asyncio worker),对单测这个
    用例太重——push_message_impl 只 append、不读,记进 list 即可。

    保留 .get_recent 占位是为了 mypy 友好;实际不被测试代码调用。
    """

    def __init__(self) -> None:
        self.appended: list[BufferedMessage] = []

    def append(self, msg: BufferedMessage) -> None:
        self.appended.append(msg)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeTarget:
    private = False


def _make_inp(**kwargs) -> PushMessageInput:
    defaults = {"adapter": "ob11", "group_id": "g1", "text": "hello", "image_urls": []}
    defaults.update(kwargs)
    return PushMessageInput(**defaults)


def _populated_managers(*, now_ms: int = 0):
    """Return (ActiveSessionManager, BotRegistry) with ob11/g1 populated."""
    am = ActiveSessionManager(default_ttl_sec=300)
    br = BotRegistry()
    am.trigger("ob11", "g1", "u1", now_ms=now_ms)
    br.upsert("ob11", "group", "g1", "bot-001", _FakeTarget(), ts=now_ms)
    return am, br


# ---------------------------------------------------------------------------
# Early-return path 1: both text and image_urls empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_returns_error_when_text_and_images_both_empty():
    am, br = _populated_managers()
    inp = _make_inp(text="", image_urls=[])
    result = await push_message_impl(inp, active_sessions=am, bot_registry=br, scope=_ANY_SCOPE)
    assert isinstance(result, PushMessageResult)
    assert result.ok is False
    assert result.error == "text and image_urls both empty"


# ---------------------------------------------------------------------------
# Early-return path 2: no active reactive session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_returns_error_when_no_active_session():
    am = ActiveSessionManager(default_ttl_sec=300)
    br = BotRegistry()
    # BotRegistry has the target but ActiveSessionManager is empty
    br.upsert("ob11", "group", "g1", "bot-001", _FakeTarget(), ts=0)

    inp = _make_inp()
    result = await push_message_impl(inp, active_sessions=am, bot_registry=br, scope=_ANY_SCOPE)
    assert result.ok is False
    assert "no active reactive session" in (result.error or "")


# ---------------------------------------------------------------------------
# Early-return path 3: session active but target unknown in BotRegistry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_returns_error_when_target_unknown():
    am = ActiveSessionManager(default_ttl_sec=300)
    br = BotRegistry()
    # ActiveSessionManager has the session but BotRegistry is empty
    am.trigger("ob11", "g1", "u1", now_ms=0)

    inp = _make_inp()
    # Mock time so push_message_impl 看到的 now_ms 还在 300s TTL 内
    with patch("nonebot_plugin_hermes.mcp.tools.push_message.time") as mock_time:
        mock_time.time.return_value = 1.0  # 1000 ms,well within TTL
        result = await push_message_impl(inp, active_sessions=am, bot_registry=br, scope=_ANY_SCOPE)
    assert result.ok is False
    # 锁定具体错误形态:validate_push_context 在 session 通过、target 缺失时
    # 抛 "unknown target",其他 error 形态(如 "send failed")会引人误判
    assert "unknown target" in (result.error or "")


# ---------------------------------------------------------------------------
# Bot-offline path: get_bot raises KeyError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_returns_error_when_bot_offline():
    am, br = _populated_managers()
    inp = _make_inp()

    with (
        patch("nonebot_plugin_hermes.mcp.tools.push_message.get_bot", side_effect=KeyError("bot-001")),
        patch("nonebot_plugin_hermes.mcp.tools.push_message.time") as mock_time,
    ):
        mock_time.time.return_value = 1.0  # 1000 ms — within TTL
        result = await push_message_impl(inp, active_sessions=am, bot_registry=br, scope=_ANY_SCOPE)

    assert result.ok is False
    assert "bot offline" in (result.error or "")


# ---------------------------------------------------------------------------
# Success path: send succeeds → ok=True and session is touched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_success_touches_session():
    now_ms = 0
    am, br = _populated_managers(now_ms=now_ms)
    inp = _make_inp()

    fake_bot = MagicMock()
    mock_send = AsyncMock(return_value=True)

    with (
        patch("nonebot_plugin_hermes.mcp.tools.push_message.get_bot", return_value=fake_bot),
        patch("nonebot_plugin_hermes.mcp.tools.push_message.send_text_with_media", mock_send),
        patch("nonebot_plugin_hermes.mcp.tools.push_message.time") as mock_time,
    ):
        # Fix time so now_ms used inside push_message_impl is within TTL
        mock_time.time.return_value = 1.0  # 1000 ms — well within 300-s TTL
        result = await push_message_impl(inp, active_sessions=am, bot_registry=br, scope=_ANY_SCOPE)

    assert result.ok is True
    assert result.error is None
    # Session should still be active (touch was called)
    assert am.is_active("ob11", "g1", now_ms=2_000)  # 2 s after trigger, still active

    # 关键不变量:proactive push 必须 at_user_id=None(不 At 任何用户),
    # 否则 outbound 会在群里 @ 一个莫名其妙的人。锁定契约。
    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args.kwargs
    assert call_kwargs["at_user_id"] is None
    assert call_kwargs["bot"] is fake_bot
    assert call_kwargs["text"] == inp.text
    assert call_kwargs["media_urls"] == inp.image_urls


# ---------------------------------------------------------------------------
# send_text_with_media returns False → error reported
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_returns_error_when_send_fails():
    am, br = _populated_managers()
    inp = _make_inp()

    fake_bot = MagicMock()

    with (
        patch("nonebot_plugin_hermes.mcp.tools.push_message.get_bot", return_value=fake_bot),
        patch(
            "nonebot_plugin_hermes.mcp.tools.push_message.send_text_with_media",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch("nonebot_plugin_hermes.mcp.tools.push_message.time") as mock_time,
    ):
        mock_time.time.return_value = 1.0
        result = await push_message_impl(inp, active_sessions=am, bot_registry=br, scope=_ANY_SCOPE)

    assert result.ok is False
    assert "send failed" in (result.error or "")


# ---------------------------------------------------------------------------
# image_urls only (text empty) is allowed when images are present
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_accepts_image_only_message():
    am, br = _populated_managers()
    inp = _make_inp(text="", image_urls=["https://example.com/img.png"])

    fake_bot = MagicMock()

    with (
        patch("nonebot_plugin_hermes.mcp.tools.push_message.get_bot", return_value=fake_bot),
        patch(
            "nonebot_plugin_hermes.mcp.tools.push_message.send_text_with_media",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("nonebot_plugin_hermes.mcp.tools.push_message.time") as mock_time,
    ):
        mock_time.time.return_value = 1.0
        result = await push_message_impl(inp, active_sessions=am, bot_registry=br, scope=_ANY_SCOPE)

    assert result.ok is True


# ---------------------------------------------------------------------------
# Side-effects on success: mark_bot_replied + buffer append
# ---------------------------------------------------------------------------
#
# push_message 成功后必须把它当成"bot 在群里发了一句"来记账, 与
# _run_reactive_turn 末段写 last_bot_reply_at / append BufferedMessage(is_bot=True)
# 等价。 否则:
#   1. cooldown 闸门读不到 last_bot_reply_at,后续非显式触发会被放过去送进 LLM 决策
#   2. 后续 turn 拉到的 <recent_messages> 看不见 bot 已答,LLM 无从自决 should_reply=false
#
# 这里只钉死 push_message_impl 自己的副作用契约;_refire 入口、_run_reactive_turn
# 末段的 cooldown 闸门在 test_message_handler_coalesce.py 里有独立测试。


@pytest.mark.asyncio
async def test_push_success_marks_bot_replied():
    """成功 push 后 active_sessions.last_bot_reply_at 必须被写入。"""
    now_ms = 0
    am, br = _populated_managers(now_ms=now_ms)
    # trigger 后,last_bot_reply_at=0(初值)
    assert am.get("ob11", "g1").last_bot_reply_at == 0

    inp = _make_inp()
    fake_bot = MagicMock()

    with (
        patch("nonebot_plugin_hermes.mcp.tools.push_message.get_bot", return_value=fake_bot),
        patch(
            "nonebot_plugin_hermes.mcp.tools.push_message.send_text_with_media",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("nonebot_plugin_hermes.mcp.tools.push_message.time") as mock_time,
    ):
        mock_time.time.return_value = 1.5  # → now_ms 内部 = 1500
        result = await push_message_impl(inp, active_sessions=am, bot_registry=br, scope=_ANY_SCOPE)

    assert result.ok is True
    assert am.get("ob11", "g1").last_bot_reply_at == 1500


@pytest.mark.asyncio
async def test_push_success_appends_buffer_with_bot_message():
    """成功 push 后 message_buffer 必须 append 一条 is_bot=True 的 BufferedMessage。"""
    am, br = _populated_managers()
    buf = _RecordingBuffer()
    inp = _make_inp(text="hello group", image_urls=["https://example.com/img.png"])
    fake_bot = MagicMock()

    with (
        patch("nonebot_plugin_hermes.mcp.tools.push_message.get_bot", return_value=fake_bot),
        patch(
            "nonebot_plugin_hermes.mcp.tools.push_message.send_text_with_media",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("nonebot_plugin_hermes.mcp.tools.push_message.time") as mock_time,
    ):
        mock_time.time.return_value = 2.0
        result = await push_message_impl(inp, active_sessions=am, bot_registry=br, message_buffer=buf, scope=_ANY_SCOPE)

    assert result.ok is True
    assert len(buf.appended) == 1
    msg = buf.appended[0]
    assert msg.is_bot is True
    assert msg.adapter == "ob11"
    assert msg.group_id == "g1"
    assert msg.user_id == "bot-001"  # registry 里登记的 bot_self_id
    assert msg.content == "hello group"
    assert msg.image_urls == ["https://example.com/img.png"]
    assert msg.ts == 2000  # int(2.0 * 1000)


@pytest.mark.asyncio
async def test_push_buffer_optional_when_none():
    """message_buffer=None 时不应 raise(向下兼容,不强求调用方都接 buffer)。

    现实里 server.py 启动期 message_buffer 短暂为 None 也不该让 push 路径炸。
    """
    am, br = _populated_managers()
    inp = _make_inp()
    fake_bot = MagicMock()

    with (
        patch("nonebot_plugin_hermes.mcp.tools.push_message.get_bot", return_value=fake_bot),
        patch(
            "nonebot_plugin_hermes.mcp.tools.push_message.send_text_with_media",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("nonebot_plugin_hermes.mcp.tools.push_message.time") as mock_time,
    ):
        mock_time.time.return_value = 1.0
        result = await push_message_impl(
            inp, active_sessions=am, bot_registry=br, message_buffer=None, scope=_ANY_SCOPE
        )

    assert result.ok is True
    # last_bot_reply_at 仍要写;buffer 缺失不影响这件
    assert am.get("ob11", "g1").last_bot_reply_at == 1000


@pytest.mark.asyncio
async def test_push_send_failed_does_not_mark_or_append():
    """send_text_with_media 返回 False 时,既不 mark_bot_replied、也不 append buffer。

    防御:send 失败说明消息没真正发出去,记账就是制造幻觉,后续 cooldown / 历史
    会以为 bot 已说话,反而压制了本该回的话。
    """
    am, br = _populated_managers()
    buf = _RecordingBuffer()
    inp = _make_inp()
    fake_bot = MagicMock()
    assert am.get("ob11", "g1").last_bot_reply_at == 0

    with (
        patch("nonebot_plugin_hermes.mcp.tools.push_message.get_bot", return_value=fake_bot),
        patch(
            "nonebot_plugin_hermes.mcp.tools.push_message.send_text_with_media",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch("nonebot_plugin_hermes.mcp.tools.push_message.time") as mock_time,
    ):
        mock_time.time.return_value = 1.0
        result = await push_message_impl(inp, active_sessions=am, bot_registry=br, message_buffer=buf, scope=_ANY_SCOPE)

    assert result.ok is False
    assert am.get("ob11", "g1").last_bot_reply_at == 0
    assert buf.appended == []


# ---------------------------------------------------------------------------
# 主机本地路径当 image_urls:必须如实回报,不能报成功后静默丢
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_reports_undeliverable_local_path():
    """agent 常把 Hermes 主机上生成的图路径直接塞进 image_urls。

    bot 侧取不到那个文件,以前是交给 outbound 静默跳过 + 返回 ok=true ——
    调用方以为图发了,用户什么也没看到,于是 agent 原样重发一遍,群里出现两条
    一样的文本。文本照发,但 skipped_images / warning 必须把真相带回去。
    """
    am, br = _populated_managers()
    local = "/root/.hermes/cache/images/custom_local_20260813_191527_ef3f7ff5.jpeg"
    inp = _make_inp(text="漫画风自画像来啦", image_urls=[local])

    fake_bot = MagicMock()
    mock_send = AsyncMock(return_value=True)
    buf = _RecordingBuffer()
    with (
        patch("nonebot_plugin_hermes.mcp.tools.push_message.get_bot", return_value=fake_bot),
        patch("nonebot_plugin_hermes.mcp.tools.push_message.send_text_with_media", mock_send),
        patch("nonebot_plugin_hermes.mcp.tools.push_message.time") as mock_time,
    ):
        mock_time.time.return_value = 1.0  # 固定在 TTL 内
        result = await push_message_impl(inp, active_sessions=am, bot_registry=br, message_buffer=buf, scope=_ANY_SCOPE)

    assert result.ok is True, "文本发出去了,整体不算失败"
    assert result.skipped_images == [local]
    assert result.warning is not None
    assert "MEDIA:" in result.warning, "要告诉 agent 正确的发图通道"
    # 不可投递的引用不进 outbound,也不进 buffer(否则 ImageFetcher 会去 httpx 它)
    assert mock_send.await_args.kwargs["media_urls"] == []
    assert buf.appended[0].image_urls == []


@pytest.mark.asyncio
async def test_push_fails_when_only_undeliverable_images_and_no_text():
    """纯图 push + 图不可投递 → 什么都没发出去,不能返回 ok=true。"""
    am, br = _populated_managers()
    inp = _make_inp(text="", image_urls=["/root/.hermes/cache/images/a.jpeg"])

    with (
        patch("nonebot_plugin_hermes.mcp.tools.push_message.get_bot", return_value=MagicMock()),
        patch("nonebot_plugin_hermes.mcp.tools.push_message.time") as mock_time,
    ):
        mock_time.time.return_value = 1.0
        result = await push_message_impl(inp, active_sessions=am, bot_registry=br, scope=_ANY_SCOPE)

    assert result.ok is False
    assert "nothing deliverable" in (result.error or "")
    assert result.skipped_images == ["/root/.hermes/cache/images/a.jpeg"]


@pytest.mark.asyncio
async def test_push_keeps_http_and_data_urls():
    """http(s) / data: 照常投递,不受新校验影响。"""
    am, br = _populated_managers()
    urls = ["https://example.com/a.png", "data:image/png;base64,iVBORw0KGgo="]
    inp = _make_inp(image_urls=[*urls, "/local/b.png"])

    mock_send = AsyncMock(return_value=True)
    with (
        patch("nonebot_plugin_hermes.mcp.tools.push_message.get_bot", return_value=MagicMock()),
        patch("nonebot_plugin_hermes.mcp.tools.push_message.send_text_with_media", mock_send),
        patch("nonebot_plugin_hermes.mcp.tools.push_message.time") as mock_time,
    ):
        mock_time.time.return_value = 1.0
        result = await push_message_impl(inp, active_sessions=am, bot_registry=br, scope=_ANY_SCOPE)

    assert result.ok is True
    assert mock_send.await_args.kwargs["media_urls"] == urls
    assert result.skipped_images == ["/local/b.png"]


@pytest.mark.asyncio
async def test_push_success_tells_the_caller_it_has_spoken():
    """成功 push 后必须在返回值里提醒调用方"这一轮你已经说过话了"。

    agent 是拿到这个结果之后才写 submit_decision 的,所以这里是唯一能在它决策**之前**
    把"再回一条就是第二条消息"告诉它的地方 —— 只写进 SKILL.md 是不够的,线上就出现过
    agent 两个都用、然后向用户汇报"两条都发了"(实际第二条被当复述丢了)。
    """
    am = ActiveSessionManager(default_ttl_sec=300)
    br = BotRegistry()
    am.trigger("ob11", "g1", "u1", now_ms=0)
    br.upsert("ob11", "group", "g1", "999", _FakeTarget(), ts=0)
    buffer = _RecordingBuffer()

    with (
        patch("nonebot_plugin_hermes.mcp.tools.push_message.time") as mock_time,
        patch("nonebot_plugin_hermes.mcp.tools.push_message.get_bot", return_value=MagicMock()),
        patch(
            "nonebot_plugin_hermes.mcp.tools.push_message.send_text_with_media",
            new=AsyncMock(return_value=True),
        ),
    ):
        mock_time.time.return_value = 10.0
        result = await push_message_impl(
            PushMessageInput(adapter="ob11", group_id="g1", text="已经在查了"),
            active_sessions=am,
            bot_registry=br,
            message_buffer=buffer,
            scope=_ANY_SCOPE,
        )

    assert result.ok is True
    assert result.note is not None
    assert "already" in result.note.lower() or "have now spoken" in result.note
    assert "should_reply=false" in result.note
    # 原文要落进 session,供同 turn 去重判断复述
    assert am.get("ob11", "g1").last_bot_reply_text == "已经在查了"
