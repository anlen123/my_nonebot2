"""xr <网址> —— 把网页拍成图片

用法：
  xr https://example.com
  xr example.com

说明：
  * 渲染全部在云端完成（thum.io 优先，失败自动换 microlink），本机不装也不跑浏览器；
  * 长图自动切片，单张过大自动转 JPEG，避免 QQ 发送失败。
"""

from __future__ import annotations

import nonebot
from nonebot import on_regex
from nonebot.adapters.onebot.v11 import Bot, Event, Message, MessageSegment

from plugins.common.cloudshot import capture, to_base64

xr = on_regex(pattern=r"^xr(\s|$)")

HELP = """用法：xr <网址>
例如：xr https://example.com
说明：渲染在云端完成，本机不需要浏览器；长图会自动切成多张。"""

_HELP_WORDS = {"", "help", "-h", "--help", "?", "？"}
# 中文标点常被误输入进网址，先规整掉
_PUNCTUATION = str.maketrans({"。": ".", "，": ".", "、": ".", "；": ".", "：": "."})


def _normalize(raw: str) -> str:
    """把用户输入整理成可用的网址；返回空串表示没给出网址。"""
    text = raw.strip().translate(_PUNCTUATION).split("\n", 1)[0]
    text = text.split(" ", 1)[0].strip()
    if not text:
        return ""
    return text if text.startswith(("http://", "https://")) else f"https://{text}"


@xr.handle()
async def xr_rev(bot: Bot, event: Event) -> None:
    raw = str(event.message).strip()[2:].strip()  # 去掉开头的 "xr"
    if raw.lower() in _HELP_WORDS:
        await bot.send(event=event, message=MessageSegment.text(HELP))
        return

    url = _normalize(raw)
    await bot.send(event=event, message=MessageSegment.text(f"正在云端渲染 {url}，请稍候…"))

    try:
        images, service = await capture(url)
    except Exception as exc:  # noqa: BLE001 —— 失败要如实回给用户，不吞异常
        nonebot.logger.warning(f"[xr] 渲染失败 url={url}：{exc}")
        await bot.send(event=event, message=MessageSegment.text(f"渲染失败：{exc}"))
        return

    message = Message()
    if len(images) > 1:
        message += MessageSegment.text(f"共 {len(images)} 张（{service}）")
    message += Message(MessageSegment.image(segment) for segment in to_base64(images))
    await bot.send(event=event, message=message)
