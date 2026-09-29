"""复现:同时 @ bot + @ 其他人 时,触发判定与 ack emoji 行为。

用户报告 (2026-05-25):
  - `@bot @other 怎么看` (有文本) 时,bot 不发出 response emoji,也没有回复
  - 已有 3c449b3 修了 prompt 侧 (让 LLM 不把双 @ 判为 should_reply=false)
  - 已有 342660b 修了「quoted-bot + @-other」误触发,并增加 `has_real_content`

本测试用最小 mock 跑 handle_message,验证:
  - is_explicit_trigger 在 @bot+@other 场景下应为 True
  - ack_scope 应被调用 (即 set_msg_emoji_like 触发)
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture(autouse=True)
def _setup_runtime(monkeypatch, tmp_path):
    """初始化 _mcp 运行时单例并安装 stubs。"""
    from nonebot_plugin_hermes import mcp as _mcp
    from nonebot_plugin_hermes.core.active_session import ActiveSessionManager
    from nonebot_plugin_hermes.core.bot_registry import BotRegistry
    from nonebot_plugin_hermes.core.inflight import InflightRegistry
    from nonebot_plugin_hermes.core.message_buffer import MessageBuffer
    from nonebot_plugin_hermes.core.storage.image_cache import ImageCache
    from nonebot_plugin_hermes.core.storage.image_fetcher import ImageFetcher
    from nonebot_plugin_hermes.core.storage.message_store import MessageStore

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


def _build_uni_msg(*segments):
    """跑期内构造 UniMessage (绕开 pytest collection alconna 钩子)。"""
    import nonebot_plugin_alconna as alconna

    return alconna.UniMessage(list(segments))


def _at(target):
    import nonebot_plugin_alconna as alconna

    return alconna.At(flag="user", target=str(target))


def _text(s):
    import nonebot_plugin_alconna as alconna

    return alconna.Text(s)


class _FakeOnebotSeg:
    """模拟 nonebot OneBot v11 MessageSegment 的最小 duck:`.type` + `.data` 字典。"""

    def __init__(self, type_: str, **data):
        self.type = type_
        self.data = dict(data)


def _mock_event(
    *,
    user_id: str = "user-A",
    group_id: str = "g1",
    is_tome: bool = True,
    message_id: int = 5001,
    original_at_targets: list[str] | None = None,
) -> MagicMock:
    """造一个最小 mock Event,满足 handle_message 路径所需字段。

    若给出 `original_at_targets`,模拟 OneBot v11 _check_at_me 跑过之前的状态:
    event.original_message 含这些 @ 段(用于测试 stripped-@bot 检测路径)。
    """
    event = MagicMock()
    event.get_user_id = MagicMock(return_value=user_id)
    event.is_tome = MagicMock(return_value=is_tome)
    event.get_plaintext = MagicMock(return_value="怎么看")
    event.message_id = message_id
    event.reply = None
    event.get_message = MagicMock(return_value=[])
    if original_at_targets is not None:
        event.original_message = [_FakeOnebotSeg("at", qq=str(t)) for t in original_at_targets]
    else:
        event.original_message = None
    return event


class _FakeTarget:
    def __init__(self, id="g1", private=False, adapter="onebotv11"):
        self.id = id
        self.private = private
        self.adapter = adapter


@pytest.mark.asyncio
async def test_at_bot_and_at_other_with_text_triggers_explicit(monkeypatch):
    """`@bot @other 怎么看` 在 at 模式 / OneBot v11 群里 → is_explicit_trigger 应为 True,
    ack_scope 应被进入并调用 set_msg_emoji_like。

    若本测试失败:确认 _ack_scope 路径是否被 matcher.skip() 提前打断。
    """
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as handler_mod

    BOT_SELF_ID = "10001"
    OTHER_USER_ID = "20002"

    # 关键 config: at 模式 + ack 开启 + active off (走 passive 路径,简化)
    monkeypatch.setattr(plugin_config, "hermes_group_trigger", "at")
    monkeypatch.setattr(plugin_config, "hermes_ack_feedback_enabled", True)
    monkeypatch.setattr(plugin_config, "hermes_active_session_enabled", False)
    monkeypatch.setattr(plugin_config, "hermes_perception_enabled", True)
    monkeypatch.setattr(plugin_config, "hermes_allow_users", [])
    monkeypatch.setattr(plugin_config, "hermes_allow_groups", [])

    # Mock UniMessage.generate_without_reply → 直接返回 @bot @other 看看
    uni_msg = _build_uni_msg(_at(BOT_SELF_ID), _at(OTHER_USER_ID), _text(" 怎么看"))

    import nonebot_plugin_alconna as alconna

    monkeypatch.setattr(
        alconna.UniMessage,
        "generate_without_reply",
        MagicMock(return_value=uni_msg),
    )

    # Mock get_target
    target = _FakeTarget(id="g1", private=False, adapter="onebotv11")
    monkeypatch.setattr(alconna, "get_target", MagicMock(return_value=target))

    # Mock get_adapter_name
    monkeypatch.setattr(handler_mod, "get_adapter_name", lambda t: "onebotv11")

    # Mock check_isolation → allow
    monkeypatch.setattr(handler_mod, "check_isolation", lambda e, t: True)

    # Mock hermes_client.chat → return a normal reply (will not be used for ack assertion)
    from nonebot_plugin_hermes.core.hermes_client import ChatResult

    async def fake_chat(**kwargs):
        return ChatResult(
            raw_text="okay",
            media_urls=[],
            structured=None,
            parse_failed=False,
            is_transport_error=False,
        )

    monkeypatch.setattr(handler_mod.hermes_client, "chat", fake_chat)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    bot = MagicMock()
    bot.self_id = BOT_SELF_ID
    bot.call_api = AsyncMock()

    event = _mock_event(user_id="user-A", group_id="g1", is_tome=True, message_id=5001)

    matcher = MagicMock()
    matcher.skip = MagicMock(side_effect=lambda: (_ for _ in ()).throw(_SkipSentinel("skipped")))

    try:
        await handler_mod.handle_message(bot, event, matcher)
    except _SkipSentinel as e:
        pytest.fail(f"matcher.skip() called unexpectedly: {e}; emoji ack will not fire")

    # 验证 set_msg_emoji_like 至少被调用过(ack 进入 + 退出会各一次,共 2 次或更多)
    emoji_calls = [
        c
        for c in bot.call_api.await_args_list
        if c.args and c.args[0] in ("set_msg_emoji_like", "unset_msg_emoji_like")
    ]
    assert len(emoji_calls) > 0, (
        f"ack emoji 没有被调用 — set_msg_emoji_like 应至少触发一次。call_api 调用: "
        f"{[c.args for c in bot.call_api.await_args_list]}"
    )
    # 第一次调用应该是 set (id=341 默认 ack emoji)
    first = emoji_calls[0]
    assert first.args[0] == "set_msg_emoji_like"
    assert first.kwargs.get("message_id") == 5001
    assert first.kwargs.get("emoji_id") == plugin_config.hermes_ack_emoji_id


class _SkipSentinel(Exception):
    """模拟 matcher.skip() 抛 SkippedException。"""


@pytest.mark.asyncio
async def test_at_bot_and_at_other_no_text_skipped_due_to_has_real_content(monkeypatch):
    """`@bot @other` (无文本) 当前会被 has_real_content=False 提前 skip → 无 ack emoji。

    这是 342660b 引入的 has_real_content 快照逻辑直接副作用:at_placeholders 不计入
    has_real_content,纯 @-only 消息(包括 @bot+@other-only)被静默丢弃。
    """
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as handler_mod

    BOT_SELF_ID = "10001"
    OTHER_USER_ID = "20002"

    monkeypatch.setattr(plugin_config, "hermes_group_trigger", "at")
    monkeypatch.setattr(plugin_config, "hermes_ack_feedback_enabled", True)
    monkeypatch.setattr(plugin_config, "hermes_active_session_enabled", False)
    monkeypatch.setattr(plugin_config, "hermes_perception_enabled", True)
    monkeypatch.setattr(plugin_config, "hermes_allow_users", [])
    monkeypatch.setattr(plugin_config, "hermes_allow_groups", [])

    # 纯 @ 无文本
    uni_msg = _build_uni_msg(_at(BOT_SELF_ID), _at(OTHER_USER_ID))

    import nonebot_plugin_alconna as alconna

    monkeypatch.setattr(
        alconna.UniMessage,
        "generate_without_reply",
        MagicMock(return_value=uni_msg),
    )

    target = _FakeTarget(id="g1", private=False, adapter="onebotv11")
    monkeypatch.setattr(alconna, "get_target", MagicMock(return_value=target))
    monkeypatch.setattr(handler_mod, "get_adapter_name", lambda t: "onebotv11")
    monkeypatch.setattr(handler_mod, "check_isolation", lambda e, t: True)

    bot = MagicMock()
    bot.self_id = BOT_SELF_ID
    bot.call_api = AsyncMock()

    event = _mock_event(user_id="user-A", group_id="g1", is_tome=True, message_id=5002)
    event.get_plaintext = MagicMock(return_value="")  # 无 plaintext

    matcher = MagicMock()
    skip_called = {"flag": False}

    def _skip():
        skip_called["flag"] = True
        raise _SkipSentinel("skipped")

    matcher.skip = _skip

    try:
        await handler_mod.handle_message(bot, event, matcher)
    except _SkipSentinel:
        pass

    # 当前行为(可能是 bug):无 ack emoji,因为 has_real_content=False skip 在 ack 之前
    emoji_calls = [c for c in bot.call_api.await_args_list if c.args and c.args[0] == "set_msg_emoji_like"]
    # 这条 assert 记录 *当前* 行为:无 emoji。如果未来修了 bug,这条会变。
    assert skip_called["flag"], "expected matcher.skip() to fire for pure @-only message"
    assert len(emoji_calls) == 0, "ack emoji should NOT fire because handler was skipped"


@pytest.mark.asyncio
async def test_at_bot_at_other_post_strip_inline_double_at_triggers(monkeypatch):
    """**真实复现 bug**:`@bot @other 怎么看` 经过 OneBot v11 `_check_at_me` 后,
    event.message 里的 @bot 已被剥走、event.to_me=True;此时 uni_msg 只剩 @other,
    导致 `_msg_at_only_other_users=True`、342660b 的「is_tome + only_others」抑制
    误触发,is_explicit_trigger 被设为 False,emoji ack 永远不发。

    本测试模拟 strip 后的 uni_msg(仅 @other),is_tome=True,无 quoted reply。
    用户报告:这种 inline 双 @ 场景下「什么 emoji 都没出现」。
    """
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as handler_mod

    BOT_SELF_ID = "10001"
    OTHER_USER_ID = "20002"

    monkeypatch.setattr(plugin_config, "hermes_group_trigger", "at")
    monkeypatch.setattr(plugin_config, "hermes_ack_feedback_enabled", True)
    monkeypatch.setattr(plugin_config, "hermes_active_session_enabled", False)
    monkeypatch.setattr(plugin_config, "hermes_perception_enabled", True)
    monkeypatch.setattr(plugin_config, "hermes_allow_users", [])
    monkeypatch.setattr(plugin_config, "hermes_allow_groups", [])

    # **真实 adapter 后视图**:@bot 段已被 _check_at_me 剥走,仅剩 @other + 文本
    uni_msg = _build_uni_msg(_at(OTHER_USER_ID), _text(" 怎么看"))

    import nonebot_plugin_alconna as alconna

    monkeypatch.setattr(
        alconna.UniMessage,
        "generate_without_reply",
        MagicMock(return_value=uni_msg),
    )

    target = _FakeTarget(id="g1", private=False, adapter="onebotv11")
    monkeypatch.setattr(alconna, "get_target", MagicMock(return_value=target))
    monkeypatch.setattr(handler_mod, "get_adapter_name", lambda t: "onebotv11")
    monkeypatch.setattr(handler_mod, "check_isolation", lambda e, t: True)

    from nonebot_plugin_hermes.core.hermes_client import ChatResult

    async def fake_chat(**kwargs):
        return ChatResult(
            raw_text="okay",
            media_urls=[],
            structured=None,
            parse_failed=False,
            is_transport_error=False,
        )

    monkeypatch.setattr(handler_mod.hermes_client, "chat", fake_chat)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    bot = MagicMock()
    bot.self_id = BOT_SELF_ID
    bot.call_api = AsyncMock()

    # 关键:is_tome=True (因 @bot 被剥前 adapter 已置位),event.reply=None (无引用)
    # event.original_message 保留 @bot + @other (剥之前的全状态),uni_msg 仅见 @other (剥之后)
    event = _mock_event(
        user_id="user-A",
        group_id="g1",
        is_tome=True,
        message_id=5004,
        original_at_targets=[BOT_SELF_ID, OTHER_USER_ID],
    )
    event.reply = None

    matcher = MagicMock()
    matcher.skip = MagicMock(side_effect=_SkipSentinel)

    try:
        await handler_mod.handle_message(bot, event, matcher)
    except _SkipSentinel as e:
        pytest.fail(f"matcher.skip() called unexpectedly — inline 双 @ 经 OneBot v11 strip 后被错误抑制: {e}")

    emoji_calls = [c for c in bot.call_api.await_args_list if c.args and c.args[0] == "set_msg_emoji_like"]
    assert len(emoji_calls) > 0, (
        f"ack emoji 没有被调用 — inline `@bot @other 怎么看` 经 adapter strip 后应仍触发显式 @。"
        f"call_api 调用: {[c.args for c in bot.call_api.await_args_list]}"
    )


@pytest.mark.asyncio
async def test_quoted_bot_reply_with_at_other_correctly_suppressed(monkeypatch):
    """对照组:用户**引用** bot 旧消息 + 只 @ 他人 (无新的 @bot)。
    342660b 的初衷:这种场景下用户实际在跟群友讲话,只是顺手引用了 bot,不应触发。

    与上一个测试的关键区别:event.reply 存在(指向 bot 老消息),uni_msg 同样只有 @other。
    has_reply=True → 走 quoted-only 抑制路径,is_explicit_trigger=False。
    """
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as handler_mod

    BOT_SELF_ID = "10001"
    OTHER_USER_ID = "20002"

    monkeypatch.setattr(plugin_config, "hermes_group_trigger", "at")
    monkeypatch.setattr(plugin_config, "hermes_ack_feedback_enabled", True)
    monkeypatch.setattr(plugin_config, "hermes_active_session_enabled", False)
    monkeypatch.setattr(plugin_config, "hermes_perception_enabled", True)
    monkeypatch.setattr(plugin_config, "hermes_allow_users", [])
    monkeypatch.setattr(plugin_config, "hermes_allow_groups", [])

    uni_msg = _build_uni_msg(_at(OTHER_USER_ID), _text(" 怎么看"))

    import nonebot_plugin_alconna as alconna

    monkeypatch.setattr(
        alconna.UniMessage,
        "generate_without_reply",
        MagicMock(return_value=uni_msg),
    )

    target = _FakeTarget(id="g1", private=False, adapter="onebotv11")
    monkeypatch.setattr(alconna, "get_target", MagicMock(return_value=target))
    monkeypatch.setattr(handler_mod, "get_adapter_name", lambda t: "onebotv11")
    monkeypatch.setattr(handler_mod, "check_isolation", lambda e, t: True)

    bot = MagicMock()
    bot.self_id = BOT_SELF_ID
    bot.call_api = AsyncMock()

    event = _mock_event(user_id="user-A", group_id="g1", is_tome=True, message_id=5005)
    # 模拟有 quoted reply 指向 bot 老消息
    event.reply = MagicMock()
    event.reply.message = "old bot reply text"

    matcher = MagicMock()
    skipped = {"flag": False}

    def _skip():
        skipped["flag"] = True
        raise _SkipSentinel("skipped")

    matcher.skip = _skip

    try:
        await handler_mod.handle_message(bot, event, matcher)
    except _SkipSentinel:
        pass

    # 342660b 原意:quoted-bot + 只 @ 他人 → 抑制,无 emoji
    assert skipped["flag"], "matcher.skip 应被触发(quoted-bot + 只 @ 他人)"
    emoji_calls = [c for c in bot.call_api.await_args_list if c.args and c.args[0] == "set_msg_emoji_like"]
    assert len(emoji_calls) == 0, "quoted-bot + 只 @ 他人 不应触发 ack emoji"


@pytest.mark.asyncio
async def test_at_bot_and_at_other_active_window_triggers(monkeypatch):
    """`@bot @other 怎么看` 在 active_session ON 的群里 (reactive 路径) → ack 应触发。"""
    from nonebot_plugin_hermes import mcp as _mcp
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as handler_mod

    BOT_SELF_ID = "10001"
    OTHER_USER_ID = "20002"

    monkeypatch.setattr(plugin_config, "hermes_group_trigger", "at")
    monkeypatch.setattr(plugin_config, "hermes_ack_feedback_enabled", True)
    monkeypatch.setattr(plugin_config, "hermes_active_session_enabled", True)
    monkeypatch.setattr(plugin_config, "hermes_perception_enabled", True)
    monkeypatch.setattr(plugin_config, "hermes_reactive_post_reply_cooldown_sec", 0)
    monkeypatch.setattr(plugin_config, "hermes_allow_users", [])
    monkeypatch.setattr(plugin_config, "hermes_allow_groups", [])

    # 群里已有 active 窗口 (模拟之前的对话)
    now_ms = int(time.time() * 1000)
    _mcp.active_sessions.trigger("onebotv11", "g1", "seed", now_ms=now_ms)

    uni_msg = _build_uni_msg(_at(BOT_SELF_ID), _at(OTHER_USER_ID), _text(" 怎么看"))

    import nonebot_plugin_alconna as alconna

    monkeypatch.setattr(
        alconna.UniMessage,
        "generate_without_reply",
        MagicMock(return_value=uni_msg),
    )

    target = _FakeTarget(id="g1", private=False, adapter="onebotv11")
    monkeypatch.setattr(alconna, "get_target", MagicMock(return_value=target))
    monkeypatch.setattr(handler_mod, "get_adapter_name", lambda t: "onebotv11")
    monkeypatch.setattr(handler_mod, "check_isolation", lambda e, t: True)

    from nonebot_plugin_hermes.core.hermes_client import ChatResult

    async def fake_chat(**kwargs):
        return ChatResult(
            raw_text="okay",
            media_urls=[],
            structured={"should_reply": True, "reply_text": "okay", "should_exit_active": False},
            parse_failed=False,
            is_transport_error=False,
        )

    monkeypatch.setattr(handler_mod.hermes_client, "chat", fake_chat)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    bot = MagicMock()
    bot.self_id = BOT_SELF_ID
    bot.call_api = AsyncMock()

    event = _mock_event(user_id="user-A", group_id="g1", is_tome=True, message_id=5003)
    matcher = MagicMock()
    matcher.skip = MagicMock(side_effect=_SkipSentinel)

    try:
        await handler_mod.handle_message(bot, event, matcher)
    except _SkipSentinel as e:
        pytest.fail(f"matcher.skip() called unexpectedly in active window: {e}")

    emoji_calls = [c for c in bot.call_api.await_args_list if c.args and c.args[0] == "set_msg_emoji_like"]
    assert len(emoji_calls) > 0, (
        f"ack emoji 没有被调用 (active window path). call_api 调用: {[c.args for c in bot.call_api.await_args_list]}"
    )
