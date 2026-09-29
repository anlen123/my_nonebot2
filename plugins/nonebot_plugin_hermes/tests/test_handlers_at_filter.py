"""C 层 @-目标过滤助手单测。

`_msg_at_only_other_users(uni_msg, bot_self_id)` 决定 reactive 入口是否要把
「只 @ 他人未点名 bot」这类消息当成非本路径触发(模式 1 修复)。
"""

from __future__ import annotations

BOT_SELF_ID = "999"


def _make_msg(*segments):
    """延迟到函数内 import,绕开 pytest collection 阶段 alconna 的 plugin loader 钩子。"""
    import nonebot_plugin_alconna as alconna

    return alconna.UniMessage(list(segments))


def _at(target):
    import nonebot_plugin_alconna as alconna

    return alconna.At(flag="user", target=str(target))


def _text(s):
    import nonebot_plugin_alconna as alconna

    return alconna.Text(s)


def test_no_at_segments_returns_false():
    """消息不含任何 @ → 不应该被 C 过滤(仍走原有逻辑)。"""
    from nonebot_plugin_hermes.handlers.message import _msg_at_only_other_users

    msg = _make_msg(_text("hello world"))
    assert _msg_at_only_other_users(msg, BOT_SELF_ID) is False


def test_only_at_other_user_returns_true():
    """消息只 @ 一个非 bot 的用户 → C 命中。"""
    from nonebot_plugin_hermes.handlers.message import _msg_at_only_other_users

    msg = _make_msg(_at("123"), _text(" 你看看"))
    assert _msg_at_only_other_users(msg, BOT_SELF_ID) is True


def test_multiple_at_others_returns_true():
    """消息 @ 多个非 bot 用户 → C 命中。"""
    from nonebot_plugin_hermes.handlers.message import _msg_at_only_other_users

    msg = _make_msg(_at("123"), _at("456"), _text(" 你们怎么看"))
    assert _msg_at_only_other_users(msg, BOT_SELF_ID) is True


def test_at_bot_only_returns_false():
    """消息只 @ bot 自己 → 不命中(走显式触发路径)。"""
    from nonebot_plugin_hermes.handlers.message import _msg_at_only_other_users

    msg = _make_msg(_at(BOT_SELF_ID), _text(" 帮我"))
    assert _msg_at_only_other_users(msg, BOT_SELF_ID) is False


def test_mixed_at_bot_and_other_returns_false():
    """消息混合 @ bot + @ 其他人 → 不命中(bot 在 @ 列表里)。"""
    from nonebot_plugin_hermes.handlers.message import _msg_at_only_other_users

    msg = _make_msg(_at("123"), _at(BOT_SELF_ID), _text(" 大家一起"))
    assert _msg_at_only_other_users(msg, BOT_SELF_ID) is False


def test_bot_self_id_compared_as_string():
    """bot_self_id 与 target 都按字符串比对,防止 int/str 类型不一致导致漏判。"""
    from nonebot_plugin_hermes.handlers.message import _msg_at_only_other_users

    msg = _make_msg(_at("999"), _text("x"))
    assert _msg_at_only_other_users(msg, 999) is False  # type: ignore[arg-type]


# --- _collect_at_placeholders: At 段回填到 plain text ---


def _at_all():
    import nonebot_plugin_alconna as alconna

    return alconna.AtAll()


def test_collect_at_placeholders_empty_when_no_at():
    from nonebot_plugin_hermes.handlers.message import _collect_at_placeholders

    msg = _make_msg(_text("just text"))
    assert _collect_at_placeholders(msg) == []


def test_collect_at_placeholders_single_user():
    from nonebot_plugin_hermes.handlers.message import _collect_at_placeholders

    msg = _make_msg(_at("123"), _text(" hi"))
    assert _collect_at_placeholders(msg) == ["@123"]


def test_collect_at_placeholders_multiple_users_preserve_order():
    from nonebot_plugin_hermes.handlers.message import _collect_at_placeholders

    msg = _make_msg(_at("123"), _text(" "), _at("456"), _text(" 你们看"))
    assert _collect_at_placeholders(msg) == ["@123", "@456"]


def test_collect_at_placeholders_at_all_first():
    """AtAll 比 At 优先呈现 — 强寻址信号放前面。"""
    from nonebot_plugin_hermes.handlers.message import _collect_at_placeholders

    msg = _make_msg(_at("123"), _at_all(), _text(" 通知"))
    assert _collect_at_placeholders(msg) == ["@全体", "@123"]


def test_collect_at_placeholders_includes_bot_self():
    """bot 自己的 At 不特判,LLM 通过 [bot] 行 id= 字段自行识别。"""
    from nonebot_plugin_hermes.handlers.message import _collect_at_placeholders

    msg = _make_msg(_at(BOT_SELF_ID), _text(" 帮我"))
    assert _collect_at_placeholders(msg) == [f"@{BOT_SELF_ID}"]


# --- _has_at_bot_in_original / stripped @bot 兜底 ---


class _FakeOnebotSeg:
    """模拟 OneBot v11 MessageSegment 的最小 duck:`.type` + `.data` 字典。"""

    def __init__(self, type_, **data):
        self.type = type_
        self.data = dict(data)


def _make_event_with_original(*segments):
    """造一个最小 event mock,只填 original_message 字段。"""
    from unittest.mock import MagicMock

    event = MagicMock()
    event.original_message = list(segments)
    return event


def test_has_at_bot_in_original_finds_bot():
    """original_message 含 @bot 段 → True (OneBot v11 _check_at_me 剥走前的状态)。"""
    from nonebot_plugin_hermes.handlers.message import _has_at_bot_in_original

    event = _make_event_with_original(
        _FakeOnebotSeg("at", qq=BOT_SELF_ID),
        _FakeOnebotSeg("at", qq="other"),
        _FakeOnebotSeg("text", text=" hi"),
    )
    assert _has_at_bot_in_original(event, BOT_SELF_ID) is True


def test_has_at_bot_in_original_no_bot():
    """original_message 含 @ 段但全是别人 → False。"""
    from nonebot_plugin_hermes.handlers.message import _has_at_bot_in_original

    event = _make_event_with_original(
        _FakeOnebotSeg("at", qq="other1"),
        _FakeOnebotSeg("at", qq="other2"),
    )
    assert _has_at_bot_in_original(event, BOT_SELF_ID) is False


def test_has_at_bot_in_original_missing_field_safe():
    """event 没 original_message / 字段为 None → 静默返回 False (非 OneBot adapter)。"""
    from unittest.mock import MagicMock

    from nonebot_plugin_hermes.handlers.message import _has_at_bot_in_original

    event = MagicMock(spec=[])  # 不暴露任何属性 → getattr 都返 None
    assert _has_at_bot_in_original(event, BOT_SELF_ID) is False


def test_has_at_bot_in_original_compares_as_string():
    """qq 字段是 int / bot_self_id 是 str(或反之)→ 仍能匹配。"""
    from nonebot_plugin_hermes.handlers.message import _has_at_bot_in_original

    event = _make_event_with_original(_FakeOnebotSeg("at", qq=int(BOT_SELF_ID)))
    assert _has_at_bot_in_original(event, BOT_SELF_ID) is True
    assert _has_at_bot_in_original(event, int(BOT_SELF_ID)) is True  # type: ignore[arg-type]


def test_collect_at_placeholders_backfills_stripped_bot():
    """uni_msg 里只有 @other (因 OneBot v11 剥走 @bot) + original_message 里有 @bot
    → 把 @<bot_id> 补到 placeholders 开头,让 Hermes 在 prompt 里能看到 @bot 信号。
    """
    from nonebot_plugin_hermes.handlers.message import _collect_at_placeholders

    uni_msg = _make_msg(_at("other-1"), _text(" 怎么看"))
    event = _make_event_with_original(
        _FakeOnebotSeg("at", qq=BOT_SELF_ID),
        _FakeOnebotSeg("at", qq="other-1"),
        _FakeOnebotSeg("text", text=" 怎么看"),
    )
    result = _collect_at_placeholders(uni_msg, event=event, bot_self_id=BOT_SELF_ID)
    assert result == [f"@{BOT_SELF_ID}", "@other-1"], f"应把 stripped @bot 补到开头,实际: {result}"


def test_collect_at_placeholders_no_double_bot():
    """uni_msg 已含 @bot (无剥离场景) → 不重复补 @bot。"""
    from nonebot_plugin_hermes.handlers.message import _collect_at_placeholders

    uni_msg = _make_msg(_at(BOT_SELF_ID), _at("other-1"), _text(" hi"))
    event = _make_event_with_original(
        _FakeOnebotSeg("at", qq=BOT_SELF_ID),
        _FakeOnebotSeg("at", qq="other-1"),
    )
    result = _collect_at_placeholders(uni_msg, event=event, bot_self_id=BOT_SELF_ID)
    # @bot 只出现一次,顺序按 uni_msg 原样
    assert result == [f"@{BOT_SELF_ID}", "@other-1"]


def test_collect_at_placeholders_no_event_unchanged_behavior():
    """没传 event/bot_self_id (replied_message 等场景) → 与原行为完全一致。"""
    from nonebot_plugin_hermes.handlers.message import _collect_at_placeholders

    uni_msg = _make_msg(_at("other-1"), _text(" hi"))
    assert _collect_at_placeholders(uni_msg) == ["@other-1"]
