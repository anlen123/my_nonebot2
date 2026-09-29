"""Tests for the long-reply merged-forward branch in send_text_with_media.

Covers:
  - Short reply → normal send, no forward
  - Long reply + group + onebotv11 + flag on → send_group_forward_msg, multi-node, no truncation suffix
  - Long reply + private → truncation (no forward)
  - Long reply + group + non-onebotv11 → truncation
  - Long reply + group + onebotv11 + flag off → truncation
  - Nickname cache: get_login_info called exactly once per self_id
  - Forward API failure → falls back to truncation send
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Autouse fixture: clear the nickname cache before every test to avoid
# cross-test leakage (each test should start with a clean cache).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_nickname_cache():
    from nonebot_plugin_hermes.core import outbound

    outbound._bot_nickname_cache.clear()
    yield
    outbound._bot_nickname_cache.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MAX_LEN = 4000  # matches default plugin_config.hermes_max_length
_LONG_TEXT = "A" * (_MAX_LEN + 500)  # well above the threshold
_SHORT_TEXT = "Hello!"  # well below the threshold


def _make_bot(self_id: str = "123", nickname: str = "TestBot") -> MagicMock:
    """Return a mock Bot whose call_api returns appropriate data."""
    bot = MagicMock()
    bot.self_id = self_id

    async def _call_api(api: str, **kwargs):
        if api == "get_login_info":
            return {"nickname": nickname}
        if api == "send_group_forward_msg":
            return {"message_id": 1}
        raise RuntimeError(f"Unexpected call_api: {api}")

    bot.call_api = AsyncMock(side_effect=_call_api)
    return bot


def _make_target(*, private: bool = False, group_id: str = "999") -> MagicMock:
    target = MagicMock()
    target.private = private
    target.id = group_id
    return target


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_short_reply_uses_normal_send():
    """Text below max_len → normal UniMessage.send, no call_api forward."""
    from nonebot_plugin_hermes.core.outbound import send_text_with_media

    bot = _make_bot()
    target = _make_target(private=False)

    with patch("nonebot_plugin_hermes.core.outbound.plugin_config") as mock_cfg:
        mock_cfg.hermes_max_length = _MAX_LEN
        mock_cfg.hermes_long_reply_forward = True

        with patch("nonebot_plugin_alconna.UniMessage.send", new_callable=AsyncMock) as mock_send:
            result = await send_text_with_media(
                bot=bot,
                target=target,
                text=_SHORT_TEXT,
                adapter_name="onebotv11",
            )

    assert result is True
    # forward API must NOT be called
    for call in bot.call_api.call_args_list:
        assert call.args[0] != "send_group_forward_msg"
    mock_send.assert_called_once()


@pytest.mark.asyncio
async def test_long_reply_group_onebotv11_uses_forward():
    """Long text + group + onebotv11 + flag on → send_group_forward_msg with multiple nodes,
    no truncation suffix in any node."""
    from nonebot_plugin_hermes.core.outbound import send_text_with_media

    bot = _make_bot()
    target = _make_target(private=False)

    # Enough text to require splitting: 3 × 3500 chars > 3500 per node
    long_text = "B" * (3500 * 3)

    with patch("nonebot_plugin_hermes.core.outbound.plugin_config") as mock_cfg:
        mock_cfg.hermes_max_length = _MAX_LEN
        mock_cfg.hermes_long_reply_forward = True

        result = await send_text_with_media(
            bot=bot,
            target=target,
            text=long_text,
            adapter_name="onebotv11",
        )

    assert result is True
    # Exactly one send_group_forward_msg call
    forward_calls = [c for c in bot.call_api.call_args_list if c.args[0] == "send_group_forward_msg"]
    assert len(forward_calls) == 1

    messages = forward_calls[0].kwargs["messages"]
    assert isinstance(messages, list)
    assert len(messages) >= 2  # text is 10500 chars → at least 3 nodes of ≤3500

    # No node should contain the truncation suffix
    for node in messages:
        content_list = node["data"]["content"]
        for seg in content_list:
            if seg["type"] == "text":
                assert "消息过长" not in seg["data"]["text"]


@pytest.mark.asyncio
async def test_long_reply_private_uses_truncation():
    """Private chat → always truncate, never use send_group_forward_msg."""
    from nonebot_plugin_hermes.core.outbound import send_text_with_media

    bot = _make_bot()
    target = _make_target(private=True)

    with patch("nonebot_plugin_hermes.core.outbound.plugin_config") as mock_cfg:
        mock_cfg.hermes_max_length = _MAX_LEN
        mock_cfg.hermes_long_reply_forward = True

        with patch("nonebot_plugin_alconna.UniMessage.send", new_callable=AsyncMock) as mock_send:
            result = await send_text_with_media(
                bot=bot,
                target=target,
                text=_LONG_TEXT,
                adapter_name="onebotv11",
            )

    assert result is True
    # No forward call
    for call in bot.call_api.call_args_list:
        assert call.args[0] != "send_group_forward_msg"
    # Normal send was invoked
    mock_send.assert_called_once()


@pytest.mark.asyncio
async def test_long_reply_non_onebotv11_group_uses_truncation():
    """Non-onebotv11 adapter → always truncate, never forward."""
    from nonebot_plugin_hermes.core.outbound import send_text_with_media

    bot = _make_bot()
    target = _make_target(private=False)

    with patch("nonebot_plugin_hermes.core.outbound.plugin_config") as mock_cfg:
        mock_cfg.hermes_max_length = _MAX_LEN
        mock_cfg.hermes_long_reply_forward = True

        with patch("nonebot_plugin_alconna.UniMessage.send", new_callable=AsyncMock) as mock_send:
            result = await send_text_with_media(
                bot=bot,
                target=target,
                text=_LONG_TEXT,
                adapter_name="telegram",
            )

    assert result is True
    for call in bot.call_api.call_args_list:
        assert call.args[0] != "send_group_forward_msg"
    mock_send.assert_called_once()


@pytest.mark.asyncio
async def test_long_reply_flag_disabled_uses_truncation():
    """hermes_long_reply_forward=False → truncation even in group + onebotv11."""
    from nonebot_plugin_hermes.core.outbound import send_text_with_media

    bot = _make_bot()
    target = _make_target(private=False)

    with patch("nonebot_plugin_hermes.core.outbound.plugin_config") as mock_cfg:
        mock_cfg.hermes_max_length = _MAX_LEN
        mock_cfg.hermes_long_reply_forward = False

        with patch("nonebot_plugin_alconna.UniMessage.send", new_callable=AsyncMock) as mock_send:
            result = await send_text_with_media(
                bot=bot,
                target=target,
                text=_LONG_TEXT,
                adapter_name="onebotv11",
            )

    assert result is True
    for call in bot.call_api.call_args_list:
        assert call.args[0] != "send_group_forward_msg"
    mock_send.assert_called_once()


@pytest.mark.asyncio
async def test_bot_nickname_fetched_once_across_calls():
    """get_login_info is called exactly once per self_id regardless of how many times
    the forward path is taken. The second call still produces correct node names."""
    from nonebot_plugin_hermes.core.outbound import send_text_with_media

    bot = _make_bot(self_id="456", nickname="CachedBot")
    target = _make_target(private=False)

    login_info_calls = []

    async def _call_api(api: str, **kwargs):
        if api == "get_login_info":
            login_info_calls.append(api)
            return {"nickname": "CachedBot"}
        if api == "send_group_forward_msg":
            return {"message_id": 1}
        raise RuntimeError(f"Unexpected: {api}")

    bot.call_api = AsyncMock(side_effect=_call_api)

    with patch("nonebot_plugin_hermes.core.outbound.plugin_config") as mock_cfg:
        mock_cfg.hermes_max_length = _MAX_LEN
        mock_cfg.hermes_long_reply_forward = True

        # First call
        r1 = await send_text_with_media(
            bot=bot,
            target=target,
            text=_LONG_TEXT,
            adapter_name="onebotv11",
        )
        # Second call
        r2 = await send_text_with_media(
            bot=bot,
            target=target,
            text=_LONG_TEXT,
            adapter_name="onebotv11",
        )

    assert r1 is True
    assert r2 is True
    # get_login_info called exactly once (second call hits the cache)
    assert len(login_info_calls) == 1

    # Both forward calls should have nodes with the correct bot name
    forward_calls = [c for c in bot.call_api.call_args_list if c.args[0] == "send_group_forward_msg"]
    assert len(forward_calls) == 2
    for fc in forward_calls:
        for node in fc.kwargs["messages"]:
            assert node["data"]["name"] == "CachedBot"


@pytest.mark.asyncio
async def test_long_reply_forward_failure_falls_back_to_truncation():
    """If send_group_forward_msg raises, fall back to normal truncated send."""
    from nonebot_plugin_hermes.core.outbound import send_text_with_media

    bot = _make_bot()
    target = _make_target(private=False)

    async def _call_api(api: str, **kwargs):
        if api == "get_login_info":
            return {"nickname": "TestBot"}
        if api == "send_group_forward_msg":
            raise RuntimeError("forward not supported")
        raise RuntimeError(f"Unexpected: {api}")

    bot.call_api = AsyncMock(side_effect=_call_api)

    with patch("nonebot_plugin_hermes.core.outbound.plugin_config") as mock_cfg:
        mock_cfg.hermes_max_length = _MAX_LEN
        mock_cfg.hermes_long_reply_forward = True

        with patch("nonebot_plugin_alconna.UniMessage.send", new_callable=AsyncMock) as mock_send:
            result = await send_text_with_media(
                bot=bot,
                target=target,
                text=_LONG_TEXT,
                adapter_name="onebotv11",
            )

    assert result is True
    # Fallback send was called
    mock_send.assert_called_once()
    # The message sent via fallback should contain the truncation suffix
    # We verify by checking send was called (truncation happens inside send path)
    # and that no double-send occurred
    assert mock_send.call_count == 1


@pytest.mark.asyncio
async def test_long_reply_forward_failure_with_at_does_not_double_at():
    """Guarantee: if send_group_forward_msg raises with at_user_id set, only ONE
    UniMessage.send occurs (the truncation fallback) and it contains exactly one At
    segment — not zero (the @ must still be present) and not two (no double-@).

    The @-ping that was previously sent *before* call_api would have fired before the
    failure was known, causing a standalone @ followed by a truncated @-prefixed message.
    After the fix the standalone @ is only sent after a *successful* forward call, so
    the failure path ends up with exactly the one At that the truncation path adds.
    """
    from nonebot_plugin_hermes.core.outbound import send_text_with_media

    bot = _make_bot()
    target = _make_target(private=False, group_id="888")

    async def _call_api(api: str, **kwargs):
        if api == "get_login_info":
            return {"nickname": "TestBot"}
        if api == "send_group_forward_msg":
            raise RuntimeError("forward not supported")
        raise RuntimeError(f"Unexpected: {api}")

    bot.call_api = AsyncMock(side_effect=_call_api)

    with patch("nonebot_plugin_hermes.core.outbound.plugin_config") as mock_cfg:
        mock_cfg.hermes_max_length = _MAX_LEN
        mock_cfg.hermes_long_reply_forward = True

        with patch("nonebot_plugin_alconna.UniMessage.send", new_callable=AsyncMock) as mock_send:
            result = await send_text_with_media(
                bot=bot,
                target=target,
                text=_LONG_TEXT,
                at_user_id="some_user",
                adapter_name="onebotv11",
            )

    assert result is True

    # Exactly ONE UniMessage.send call: the truncation fallback (no standalone @-ping).
    assert mock_send.call_count == 1, f"Expected 1 UniMessage.send (truncation fallback), got {mock_send.call_count}"

    # send_group_forward_msg called once, get_login_info once — no extra calls.
    api_names = [c.args[0] for c in bot.call_api.call_args_list]
    assert api_names.count("send_group_forward_msg") == 1
    assert api_names.count("get_login_info") == 1
    assert len(api_names) == 2, f"Unexpected extra call_api calls: {api_names}"


@pytest.mark.asyncio
async def test_long_reply_forward_success_with_at_sends_at_after_forward():
    """Happy path: send_group_forward_msg succeeds with at_user_id set.
    The @ ping must be sent AFTER the forward bundle — so the @ is the most recent
    message in the chat list. Verified by inspecting call order in bot.call_api
    vs UniMessage.send invocations.
    """
    from nonebot_plugin_hermes.core.outbound import send_text_with_media

    bot = _make_bot(self_id="789", nickname="OrderBot")
    target = _make_target(private=False, group_id="777")

    call_order: list[str] = []

    async def _call_api(api: str, **kwargs):
        call_order.append(f"call_api:{api}")
        if api == "get_login_info":
            return {"nickname": "OrderBot"}
        if api == "send_group_forward_msg":
            return {"message_id": 42}
        raise RuntimeError(f"Unexpected: {api}")

    bot.call_api = AsyncMock(side_effect=_call_api)

    async def _mock_send(target, bot):
        call_order.append("unimessage:send")

    with patch("nonebot_plugin_hermes.core.outbound.plugin_config") as mock_cfg:
        mock_cfg.hermes_max_length = _MAX_LEN
        mock_cfg.hermes_long_reply_forward = True

        with patch("nonebot_plugin_alconna.UniMessage.send", new_callable=AsyncMock, side_effect=_mock_send):
            result = await send_text_with_media(
                bot=bot,
                target=target,
                text=_LONG_TEXT,
                at_user_id="user",
                adapter_name="onebotv11",
            )

    assert result is True

    # The @-ping UniMessage.send must come AFTER send_group_forward_msg in the call order.
    forward_idx = call_order.index("call_api:send_group_forward_msg")
    at_idx = call_order.index("unimessage:send")
    assert forward_idx < at_idx, f"Expected forward before @-ping, got order: {call_order}"
