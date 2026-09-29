"""验证非文本段占位收集 + sticker 跳过 vision URL 提取。

针对 `_collect_nontext_placeholders` 和 `_extract_image_urls` 的 sticker 分支。
不跑全 perception/handle_message 流程——那些路径有 mcp runtime 依赖,
单测聚焦在 helper 行为本身。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _make_uni_msg(*segments):
    """绕过 pytest collection 阶段 alconna plugin loader 钩子,延迟到函数内 import。"""
    import nonebot_plugin_alconna as alconna

    return alconna.UniMessage(list(segments))


# --- _collect_nontext_placeholders ---


def test_collect_sticker_image_returns_emoji_pack_placeholder():
    import nonebot_plugin_alconna as alconna

    from nonebot_plugin_hermes.handlers.message import _collect_nontext_placeholders

    msg = _make_uni_msg(alconna.Image(url="https://x/a.png", sticker=True))
    assert _collect_nontext_placeholders(msg) == ["[表情包]"]


def test_collect_normal_image_returns_empty():
    """普通 image 不算非文本段(它已经走 _extract_image_urls + [图片] 占位),
    不要重复贴 [表情包]。"""
    import nonebot_plugin_alconna as alconna

    from nonebot_plugin_hermes.handlers.message import _collect_nontext_placeholders

    msg = _make_uni_msg(alconna.Image(url="https://x/a.png", sticker=False))
    assert _collect_nontext_placeholders(msg) == []


def test_collect_voice_returns_voice_placeholder():
    import nonebot_plugin_alconna as alconna

    from nonebot_plugin_hermes.handlers.message import _collect_nontext_placeholders

    msg = _make_uni_msg(alconna.Voice(url="https://x/v.amr"))
    assert _collect_nontext_placeholders(msg) == ["[语音]"]


def test_collect_video_returns_video_placeholder():
    import nonebot_plugin_alconna as alconna

    from nonebot_plugin_hermes.handlers.message import _collect_nontext_placeholders

    msg = _make_uni_msg(alconna.Video(url="https://x/v.mp4"))
    assert _collect_nontext_placeholders(msg) == ["[视频]"]


def test_collect_emoji_with_name_returns_named_placeholder():
    import nonebot_plugin_alconna as alconna

    from nonebot_plugin_hermes.handlers.message import _collect_nontext_placeholders

    msg = _make_uni_msg(alconna.Emoji(id="123", name="微笑"))
    assert _collect_nontext_placeholders(msg) == ["[表情:微笑]"]


def test_collect_emoji_without_name_returns_bare_placeholder():
    import nonebot_plugin_alconna as alconna

    from nonebot_plugin_hermes.handlers.message import _collect_nontext_placeholders

    msg = _make_uni_msg(alconna.Emoji(id="123"))
    assert _collect_nontext_placeholders(msg) == ["[表情]"]


def test_collect_mixed_segments_preserves_each():
    import nonebot_plugin_alconna as alconna

    from nonebot_plugin_hermes.handlers.message import _collect_nontext_placeholders

    msg = _make_uni_msg(
        alconna.Text("hi"),
        alconna.Voice(url="https://x/v.amr"),
        alconna.Emoji(id="1", name="笑"),
        alconna.Image(url="https://x/a.png", sticker=True),
        alconna.Video(url="https://x/v.mp4"),
        alconna.File(id="f1", name="report.pdf"),
    )
    result = _collect_nontext_placeholders(msg)
    assert "[表情包]" in result
    assert "[语音]" in result
    assert "[视频]" in result
    assert "[表情:笑]" in result
    assert "[文件:report.pdf]" in result
    assert len(result) == 5


def test_collect_text_only_returns_empty():
    import nonebot_plugin_alconna as alconna

    from nonebot_plugin_hermes.handlers.message import _collect_nontext_placeholders

    msg = _make_uni_msg(alconna.Text("hello world"))
    assert _collect_nontext_placeholders(msg) == []


def test_collect_file_with_name_returns_named_placeholder():
    import nonebot_plugin_alconna as alconna

    from nonebot_plugin_hermes.handlers.message import _collect_nontext_placeholders

    msg = _make_uni_msg(alconna.File(id="abc", name="report.pdf"))
    assert _collect_nontext_placeholders(msg) == ["[文件:report.pdf]"]


def test_collect_file_without_name_returns_unknown_placeholder():
    """显式 name=None 走 fallback 分支。

    注意:alconna.File() 不传 name 时会自动设默认值 'file.bin',
    会无声越过 fallback 路径。显式传 None 是该测试的正确写法,不要"清理"掉。
    """
    import nonebot_plugin_alconna as alconna

    from nonebot_plugin_hermes.handlers.message import _collect_nontext_placeholders

    msg = _make_uni_msg(alconna.File(id="abc", name=None))
    assert _collect_nontext_placeholders(msg) == ["[文件:未命名]"]


# --- _extract_image_urls sticker skip ---


@pytest.mark.asyncio
async def test_extract_image_urls_skips_sticker():
    """sticker=True 的 Image 不进 URL list, 避免走 vision API 烧 token。"""
    import nonebot_plugin_alconna as alconna

    from nonebot_plugin_hermes.handlers.message import _extract_image_urls

    msg = _make_uni_msg(
        alconna.Image(url="https://x/normal.png", sticker=False),
        alconna.Image(url="https://x/sticker.png", sticker=True),
    )
    bot = MagicMock()
    urls = await _extract_image_urls(msg, bot, "onebotv11")
    assert "https://x/normal.png" in urls
    assert "https://x/sticker.png" not in urls
    assert len(urls) == 1
