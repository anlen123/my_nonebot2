"""验证 `_ack_scope` async context manager 的所有分支。

不跑真实 chat() 流程——focus 在 context manager 的 set/clear/抑制语义。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _fake_event(message_id="abc123"):
    e = MagicMock()
    e.message_id = message_id
    return e


def _fake_bot():
    b = MagicMock()
    b.call_api = AsyncMock(return_value={})
    return b


@pytest.mark.asyncio
async def test_disabled_yields_without_api_call(monkeypatch):
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers.message import _ack_scope

    monkeypatch.setattr(plugin_config, "hermes_ack_feedback_enabled", False)

    bot = _fake_bot()
    event = _fake_event()

    async with _ack_scope(bot, event, adapter_name="onebotv11", is_explicit_trigger=True, is_private=False):
        pass

    assert bot.call_api.await_count == 0


@pytest.mark.asyncio
async def test_non_explicit_trigger_yields_without_api_call(monkeypatch):
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers.message import _ack_scope

    monkeypatch.setattr(plugin_config, "hermes_ack_feedback_enabled", True)

    bot = _fake_bot()
    event = _fake_event()

    async with _ack_scope(bot, event, adapter_name="onebotv11", is_explicit_trigger=False, is_private=False):
        pass

    assert bot.call_api.await_count == 0


@pytest.mark.asyncio
async def test_non_onebot_adapter_yields_without_api_call(monkeypatch):
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers.message import _ack_scope

    monkeypatch.setattr(plugin_config, "hermes_ack_feedback_enabled", True)

    bot = _fake_bot()
    event = _fake_event()

    async with _ack_scope(bot, event, adapter_name="telegram", is_explicit_trigger=True, is_private=False):
        pass

    assert bot.call_api.await_count == 0


@pytest.mark.asyncio
async def test_private_chat_yields_without_api_call(monkeypatch):
    """私聊 (is_private=True) → 跳过, QQ NT 协议下 set_msg_emoji_like 私聊会 raise
    '只支持群聊消息', 我们在入口处先挡住, 不浪费一次失败的 API call。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers.message import _ack_scope

    monkeypatch.setattr(plugin_config, "hermes_ack_feedback_enabled", True)

    bot = _fake_bot()
    event = _fake_event(message_id=12345)

    async with _ack_scope(bot, event, adapter_name="onebotv11", is_explicit_trigger=True, is_private=True):
        pass

    assert bot.call_api.await_count == 0


@pytest.mark.asyncio
async def test_missing_message_id_yields_without_api_call(monkeypatch):
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers.message import _ack_scope

    monkeypatch.setattr(plugin_config, "hermes_ack_feedback_enabled", True)

    bot = _fake_bot()
    event = MagicMock(spec=[])  # 没有 message_id 属性

    async with _ack_scope(bot, event, adapter_name="onebotv11", is_explicit_trigger=True, is_private=False):
        pass

    assert bot.call_api.await_count == 0


@pytest.mark.asyncio
async def test_happy_path_sets_and_clears(monkeypatch):
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers.message import _ack_scope

    monkeypatch.setattr(plugin_config, "hermes_ack_feedback_enabled", True)
    monkeypatch.setattr(plugin_config, "hermes_ack_emoji_id", 424)

    bot = _fake_bot()
    event = _fake_event(message_id=12345)

    async with _ack_scope(bot, event, adapter_name="onebotv11", is_explicit_trigger=True, is_private=False):
        pass

    assert bot.call_api.await_count == 2
    # 第一次添加: set_msg_emoji_like(message_id, emoji_id) (无 set 参数)
    # 第二次撤销 LLOneBot 风格优先: unset_msg_emoji_like(message_id, emoji_id)
    first_call = bot.call_api.call_args_list[0]
    second_call = bot.call_api.call_args_list[1]
    assert first_call.args == ("set_msg_emoji_like",)
    assert first_call.kwargs == {"message_id": 12345, "emoji_id": 424}
    assert second_call.args == ("unset_msg_emoji_like",)
    assert second_call.kwargs == {"message_id": 12345, "emoji_id": 424}


@pytest.mark.asyncio
async def test_body_raises_still_clears(monkeypatch):
    """主路径抛异常时,finally 仍要 clear。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers.message import _ack_scope

    monkeypatch.setattr(plugin_config, "hermes_ack_feedback_enabled", True)

    bot = _fake_bot()
    event = _fake_event(message_id=12345)

    with pytest.raises(RuntimeError, match="boom"):
        async with _ack_scope(bot, event, adapter_name="onebotv11", is_explicit_trigger=True, is_private=False):
            raise RuntimeError("boom")

    # set 进 + LLOneBot unset 出, 异常被重抛
    assert bot.call_api.await_count == 2
    assert bot.call_api.call_args_list[1].args == ("unset_msg_emoji_like",)


@pytest.mark.asyncio
async def test_set_fails_clear_skipped(monkeypatch):
    """set 失败 (网络/权限) 时, finally 不调 clear, 避免无意义错误日志。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers.message import _ack_scope

    monkeypatch.setattr(plugin_config, "hermes_ack_feedback_enabled", True)

    bot = MagicMock()
    bot.call_api = AsyncMock(side_effect=RuntimeError("api forbidden"))
    event = _fake_event(message_id=12345)

    async with _ack_scope(bot, event, adapter_name="onebotv11", is_explicit_trigger=True, is_private=False):
        pass

    # 只调了一次 (set), 没尝试 clear
    assert bot.call_api.await_count == 1


@pytest.mark.asyncio
async def test_unset_fails_falls_back_to_set_false(monkeypatch):
    """LLOneBot unset 失败 (老版 LLOneBot 没这个 endpoint) → 自动 fallback 到 NapCat set=False。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers.message import _ack_scope

    monkeypatch.setattr(plugin_config, "hermes_ack_feedback_enabled", True)
    monkeypatch.setattr(plugin_config, "hermes_ack_emoji_id", 424)

    call_log = []

    async def routed(api_name, **kw):
        call_log.append((api_name, kw))
        if api_name == "unset_msg_emoji_like":
            raise RuntimeError("no such endpoint")
        return {}

    bot = MagicMock()
    bot.call_api = AsyncMock(side_effect=routed)
    event = _fake_event(message_id=12345)

    async with _ack_scope(bot, event, adapter_name="onebotv11", is_explicit_trigger=True, is_private=False):
        pass

    # 添加 + 尝试 unset (失败) + fallback set=False = 3 次
    assert len(call_log) == 3
    assert call_log[0][0] == "set_msg_emoji_like"
    assert "set" not in call_log[0][1]  # 添加表情时不传 set
    assert call_log[1][0] == "unset_msg_emoji_like"
    assert call_log[2][0] == "set_msg_emoji_like"
    assert call_log[2][1]["set"] is False


@pytest.mark.asyncio
async def test_all_clear_paths_fail_silently(monkeypatch):
    """unset 失败 + fallback set=False 也失败 → 全部静默吞掉, 不抛, 不影响主路径。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers.message import _ack_scope

    monkeypatch.setattr(plugin_config, "hermes_ack_feedback_enabled", True)

    call_log = []

    async def all_clear_fails(api_name, **kw):
        call_log.append((api_name, kw))
        # 添加 (无 set 参数) 成功; 撤销路径全失败
        if api_name == "unset_msg_emoji_like":
            raise RuntimeError("LLOneBot unset failed")
        if api_name == "set_msg_emoji_like" and kw.get("set") is False:
            raise RuntimeError("NapCat set=False also failed")
        return {}

    bot = MagicMock()
    bot.call_api = AsyncMock(side_effect=all_clear_fails)
    event = _fake_event(message_id=12345)

    # 不抛
    async with _ack_scope(bot, event, adapter_name="onebotv11", is_explicit_trigger=True, is_private=False):
        pass

    # 添加 + unset 失败 + set=False 失败 = 3 次
    assert len(call_log) == 3


@pytest.mark.asyncio
async def test_old_llonebot_warns_once(monkeypatch):
    """老 LLOneBot: unset 返回 1404 + set=False 静默接受 (但实际不撤销) →
    一次性记录到 `_ACK_CANCEL_UNSUPPORTED_WARNED` (用于 WARN 去重)。
    同一 bot 第二次进 _ack_scope 时 set 已在表里, 不再发 WARN。
    我们 (loguru 不走 caplog) 通过验证模块级 dedupe set 来测 WARN-once 语义。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as msg_mod

    monkeypatch.setattr(plugin_config, "hermes_ack_feedback_enabled", True)
    # 清空模块级 dedupe set, 让本测试独立 (其他测试可能已 populate)
    monkeypatch.setattr(msg_mod, "_ACK_CANCEL_UNSUPPORTED_WARNED", set())

    async def old_llonebot(api_name, **_kw):
        if api_name == "unset_msg_emoji_like":
            # 模拟真实 1404 错误信息
            raise RuntimeError("ActionFailed(retcode=1404, message='不支持的api unset_msg_emoji_like')")
        # 老 LLOneBot 的 set_msg_emoji_like (含 set=False) 静默接受
        return {}

    bot = MagicMock()
    bot.self_id = "987654"
    bot.call_api = AsyncMock(side_effect=old_llonebot)
    event = _fake_event(message_id=12345)

    # 第 1 turn: 老版本 + fallback 静默接受 → bot id 进入 dedupe set
    async with msg_mod._ack_scope(bot, event, adapter_name="onebotv11", is_explicit_trigger=True, is_private=False):
        pass
    assert "987654" in msg_mod._ACK_CANCEL_UNSUPPORTED_WARNED

    # 第 2 turn 同一 bot: dedupe set 已存在, WARN 路径不会重复触发
    # (我们无法直接抓 loguru 的 WARN 调用, 但模块逻辑保证: bot_id in set → skip warning.warning)
    before_size = len(msg_mod._ACK_CANCEL_UNSUPPORTED_WARNED)
    async with msg_mod._ack_scope(bot, event, adapter_name="onebotv11", is_explicit_trigger=True, is_private=False):
        pass
    assert len(msg_mod._ACK_CANCEL_UNSUPPORTED_WARNED) == before_size  # 没新加 entry

    # 另一个 bot id 出现同样问题 → 应该再 WARN 一次 (单独的 entry)
    bot2 = MagicMock()
    bot2.self_id = "111222"
    bot2.call_api = AsyncMock(side_effect=old_llonebot)
    async with msg_mod._ack_scope(bot2, event, adapter_name="onebotv11", is_explicit_trigger=True, is_private=False):
        pass
    assert "111222" in msg_mod._ACK_CANCEL_UNSUPPORTED_WARNED


@pytest.mark.asyncio
async def test_warn_not_triggered_when_unset_error_unrelated(monkeypatch):
    """unset 失败但错误不是 1404 (例如网络错误) → 不归类为'版本过旧',
    不进 dedupe set, 也不 WARN。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as msg_mod

    monkeypatch.setattr(plugin_config, "hermes_ack_feedback_enabled", True)
    monkeypatch.setattr(msg_mod, "_ACK_CANCEL_UNSUPPORTED_WARNED", set())

    async def network_glitch(api_name, **_kw):
        if api_name == "unset_msg_emoji_like":
            raise RuntimeError("connection reset by peer")
        return {}

    bot = MagicMock()
    bot.self_id = "333"
    bot.call_api = AsyncMock(side_effect=network_glitch)
    event = _fake_event(message_id=12345)

    async with msg_mod._ack_scope(bot, event, adapter_name="onebotv11", is_explicit_trigger=True, is_private=False):
        pass

    # 网络错误不算"版本过旧", 不进 dedupe set
    assert "333" not in msg_mod._ACK_CANCEL_UNSUPPORTED_WARNED
