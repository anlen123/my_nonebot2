"""Tests for data: URL image delivery in send_text_with_media.

Hermes api_server inlines locally-generated images (≤5MB) as
``![image](data:image/png;base64,…)`` markdown. These must be decoded and
attached as raw-bytes Image segments — they have no fetchable URL.

Covers:
  - Valid data:image URL → Image(raw=bytes, mimetype=…) segment attached
  - Invalid base64 payload → skipped gracefully, text still sent
  - Non-base64 data URL (percent-encoded form) → skipped gracefully
  - Only invalid media and no text → empty-message guard returns False
  - Only valid data URL, no text → sends (guard passes)
  - Long-reply forward path (OneBot v11 group) → forward node carries
    {"type": "image", "data": {"file": "base64://…"}}
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_MAX_LEN = 4000  # matches default plugin_config.hermes_max_length

_PNG_BYTES = b"\x89PNG\r\n\x1a\nfakepixels"
_PNG_B64 = base64.b64encode(_PNG_BYTES).decode()
_PNG_DATA_URL = f"data:image/png;base64,{_PNG_B64}"


@pytest.fixture(autouse=True)
def _clear_nickname_cache():
    from nonebot_plugin_hermes.core import outbound

    outbound._bot_nickname_cache.clear()
    yield
    outbound._bot_nickname_cache.clear()


def _make_bot(self_id: str = "123", nickname: str = "TestBot") -> MagicMock:
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


def _image_segments(msg) -> list:
    import nonebot_plugin_alconna as alconna

    return [seg for seg in msg if isinstance(seg, alconna.Image)]


async def _send(text: str, media_urls: list[str], *, private: bool = True):
    """Run send_text_with_media with mocks; return (result, sent UniMessage | None)."""
    from nonebot_plugin_hermes.core.outbound import send_text_with_media

    bot = _make_bot()
    target = _make_target(private=private)

    with patch("nonebot_plugin_hermes.core.outbound.plugin_config") as mock_cfg:
        mock_cfg.hermes_max_length = _MAX_LEN
        mock_cfg.hermes_long_reply_forward = True

        with patch("nonebot_plugin_alconna.UniMessage.send", autospec=True) as mock_send:
            result = await send_text_with_media(
                bot=bot,
                target=target,
                text=text,
                media_urls=media_urls,
            )

    sent_msg = mock_send.call_args.args[0] if mock_send.call_args else None
    return result, sent_msg


@pytest.mark.asyncio
async def test_data_url_image_attached_as_raw_bytes():
    result, msg = await _send("图来了", [_PNG_DATA_URL])

    assert result is True
    images = _image_segments(msg)
    assert len(images) == 1
    assert images[0].raw == _PNG_BYTES
    assert images[0].mimetype == "image/png"


@pytest.mark.asyncio
async def test_invalid_base64_payload_skipped_text_still_sent():
    result, msg = await _send("文字照发", ["data:image/png;base64,@@@not-base64@@@"])

    assert result is True
    assert _image_segments(msg) == []
    assert "文字照发" in msg.extract_plain_text()


@pytest.mark.asyncio
async def test_non_base64_data_url_skipped():
    # RFC 2397 allows percent-encoded (non-base64) payloads; we don't support them.
    result, msg = await _send("ok", ["data:image/svg+xml,%3Csvg%3E"])

    assert result is True
    assert _image_segments(msg) == []


@pytest.mark.asyncio
async def test_only_invalid_data_url_and_no_text_returns_false():
    result, _ = await _send("", ["data:image/png;base64,@@@"])

    assert result is False


@pytest.mark.asyncio
async def test_only_valid_data_url_and_no_text_sends():
    result, msg = await _send("", [_PNG_DATA_URL])

    assert result is True
    assert len(_image_segments(msg)) == 1


@pytest.mark.asyncio
async def test_forward_path_carries_data_url_as_base64_file():
    from nonebot_plugin_hermes.core.outbound import send_text_with_media

    bot = _make_bot()
    target = _make_target(private=False)
    long_text = "B" * (_MAX_LEN + 500)

    with patch("nonebot_plugin_hermes.core.outbound.plugin_config") as mock_cfg:
        mock_cfg.hermes_max_length = _MAX_LEN
        mock_cfg.hermes_long_reply_forward = True

        with patch("nonebot_plugin_alconna.UniMessage.send", new_callable=AsyncMock):
            result = await send_text_with_media(
                bot=bot,
                target=target,
                text=long_text,
                media_urls=[_PNG_DATA_URL],
                adapter_name="onebotv11",
            )

    assert result is True
    forward_calls = [c for c in bot.call_api.call_args_list if c.args and c.args[0] == "send_group_forward_msg"]
    assert len(forward_calls) == 1
    nodes = forward_calls[0].kwargs["messages"]
    last_content = nodes[-1]["data"]["content"]
    image_items = [item for item in last_content if item["type"] == "image"]
    assert image_items == [{"type": "image", "data": {"file": f"base64://{_PNG_B64}"}}]
