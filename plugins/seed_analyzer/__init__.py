"""seed_analyzer —— 磁力链接 / 种子 hash 分析插件

用法：
  验车 <磁力链接或 32/40 位 hash>   （种子分析 / 种子信息 / 种子详情 等价）

输入：
  完整 magnet:?xt=... 链接，或纯 32/40 位十六进制 hash

说明：
  * 详情来自 whatslink.info 公开接口：名称、类型、大小、文件数，附截图预览；
  * 群聊走合并转发、私聊走普通消息，截图先竖向拼成一张长图再发；
  * 发送带退避重试，用来应付 QQ 风控（retcode 1200）。
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import re
from collections.abc import Awaitable, Callable
from io import BytesIO
from typing import Any, Optional

import aiohttp
import nonebot
from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent, Message, MessageSegment
from nonebot.adapters.onebot.v11.exception import ActionFailed
from nonebot.plugin import on_regex
from PIL import Image

# ── 命令 ──────────────────────────────────────────────────────────────────────
# 触发词只保留一份：命令正则与参数解析都从这里取，免得两处对不上
COMMANDS = ("验车", "种子分析", "种子信息", "种子详情")

cmd = on_regex(pattern="^(" + "|".join(COMMANDS) + ")")

# ── 常量 ──────────────────────────────────────────────────────────────────────
API_BASE = "https://whatslink.info/api/v1/link"
IMAGE_BASE = "https://whatslink.info/image"

INFO_TIMEOUT = 30       # 详情接口超时（秒）
IMAGE_TIMEOUT = 20      # 单张截图下载超时（秒）
IMAGE_GAP = 0.5         # 连续下载截图之间的间隔（秒），别把对方图床打急
MAX_IMAGE_WIDTH = 800   # 拼接长图的目标宽度

FILE_TYPE_ICONS = {
    "folder": "📁",
    "video": "🎬",
    "audio": "🎵",
    "archive": "📦",
    "image": "🖼️",
    "document": "📄",
    "text": "📝",
    "font": "🔤",
    "application": "⚙️",
}

_MAGNET_PATTERN = re.compile(r"magnet:\?[^\s]+", re.IGNORECASE)
_HASH_PATTERN = re.compile(r"\b([0-9a-fA-F]{32}|[0-9a-fA-F]{40})\b")


# ── 输入解析与格式化 ──────────────────────────────────────────────────────────
def extract_hash(text: str) -> Optional[str]:
    """从消息里取出磁力链接或 32/40 位 hash，都没有则返回 None。"""
    for command in COMMANDS:
        if text.startswith(command):
            text = text[len(command):].strip()
            break

    if not text:
        return None

    magnet = _MAGNET_PATTERN.search(text)
    if magnet:
        return magnet.group(0)

    digest = _HASH_PATTERN.search(text)
    return digest.group(1) if digest else None


def format_size(size: int) -> str:
    """字节数转人读的大小，非正数当未知。"""
    if size <= 0:
        return "未知"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} PB"


def get_icon(file_type: str) -> str:
    """取文件类型对应的图标，类型缺失或没收录时给默认图标。"""
    if not file_type:
        return "❓"
    return FILE_TYPE_ICONS.get(file_type.lower(), "📄")


# ── 截图处理 ──────────────────────────────────────────────────────────────────
def _fit_width(image: Image.Image, max_width: int) -> Image.Image:
    """超过 max_width 的图等比缩小，没超的原样返回。"""
    if image.width <= max_width:
        return image
    ratio = max_width / image.width
    return image.resize((max_width, int(image.height * ratio)), Image.LANCZOS)


def compose_images(images: list[Image.Image], max_width: int = MAX_IMAGE_WIDTH) -> Image.Image:
    """把多张截图竖向拼成一张白底长图；只有一张时就只做限宽。"""
    fitted = [_fit_width(image, max_width) for image in images]
    if len(fitted) == 1:
        return fitted[0]

    total_height = sum(image.height for image in fitted)
    combined = Image.new("RGB", (max_width, total_height), (255, 255, 255))
    offset = 0
    for image in fitted:
        combined.paste(image, (0, offset))
        offset += image.height
    return combined


def _screenshot_urls(screenshots: list) -> list[str]:
    """从接口返回的 screenshots 里挑出可用地址，相对路径补成绝对地址。"""
    urls: list[str] = []
    for shot in screenshots:
        if isinstance(shot, dict):
            url = shot.get("screenshot") or shot.get("url") or ""
        else:
            url = str(shot)

        if url and not url.startswith("http"):
            url = f"{IMAGE_BASE}/{url.lstrip('/')}"
        if url:
            urls.append(url)
    return urls


async def _download_screenshot(
    session: aiohttp.ClientSession, url: str
) -> Optional[Image.Image]:
    """下载一张截图；失败只记日志并返回 None，不影响其他截图。"""
    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=IMAGE_TIMEOUT)
        ) as response:
            if response.status != 200:
                nonebot.logger.warning(f"[seed_analyzer] 下载截图失败 ({response.status}): {url}")
                return None
            return Image.open(BytesIO(await response.read()))
    except (aiohttp.ClientError, OSError, ValueError, Image.DecompressionBombError) as exc:
        nonebot.logger.warning(f"[seed_analyzer] 下载截图异常: {exc}")
        return None


async def _collect_screenshots(
    session: aiohttp.ClientSession, screenshots: Any
) -> Optional[bytes]:
    """下载并竖向拼接截图，返回 JPEG 字节；一张可用的都没有则返回 None。"""
    if not isinstance(screenshots, list) or not screenshots:
        return None

    urls = _screenshot_urls(screenshots)
    if not urls:
        return None
    nonebot.logger.info(f"[seed_analyzer] 找到 {len(urls)} 张截图")

    downloaded: list[Image.Image] = []
    for url in urls:
        image = await _download_screenshot(session, url)
        if image is not None:
            downloaded.append(image)
        await asyncio.sleep(IMAGE_GAP)   # 连着下载容易被图床掐断，缓一下
    if not downloaded:
        return None

    buffer = BytesIO()
    compose_images(downloaded).save(buffer, format="JPEG", quality=85)
    image_bytes = buffer.getvalue()
    nonebot.logger.info(f"[seed_analyzer] 拼接完成，大小: {len(image_bytes) / 1024:.1f} KB")
    return image_bytes


def _image_cq(image_bytes: bytes) -> str:
    """把图片字节包成 CQ 码 —— 合并转发节点的 content 只认字符串。"""
    return f"[CQ:image,file=base64://{base64.b64encode(image_bytes).decode()}]"


# ── 发送重试（应对 QQ 风控 / retcode 1200）─────────────────────────────────────
async def _send_with_retry(
    label: str,
    send: Callable[[], Awaitable[None]],
    max_retries: int = 5,
    base_delay: float = 3.0,
) -> None:
    """带退避地执行一次发送；重试用尽仍失败则把 ActionFailed 抛给调用方。"""
    for attempt in range(max_retries):
        try:
            await send()
            return
        except ActionFailed as exc:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (attempt + 1)   # 递增延迟: 3, 6, 9, 12 秒
            nonebot.logger.warning(
                f"[seed_analyzer] {label}风控，{delay:.0f}秒后重试 "
                f"({attempt + 1}/{max_retries}): {exc.message}"
            )
            await asyncio.sleep(delay)


async def retry_send(
    bot: Bot,
    event: Event,
    message: Message | MessageSegment,
    max_retries: int = 5,
    base_delay: float = 3.0,
) -> None:
    """带退避重试的消息发送，应对 QQ 风控。"""
    await _send_with_retry(
        "发送消息",
        lambda: bot.send(event=event, message=message),
        max_retries,
        base_delay,
    )


async def retry_send_forward_msg(
    bot: Bot,
    event: GroupMessageEvent,
    node: dict,
    max_retries: int = 5,
    base_delay: float = 3.0,
) -> None:
    """带退避重试的合并转发发送，应对 QQ 风控。"""
    await _send_with_retry(
        "合并转发",
        lambda: bot.call_api(
            "send_group_forward_msg", group_id=event.group_id, messages=[node]
        ),
        max_retries,
        base_delay,
    )


async def _say(bot: Bot, event: Event, text: str) -> None:
    """回一条纯文本消息（自带风控重试）。"""
    await retry_send(bot, event, MessageSegment.text(text))


# ── 命令处理 ──────────────────────────────────────────────────────────────────
@cmd.handle()
async def seed_analyzer_handler(bot: Bot, event: Event) -> None:
    """验车 / 种子分析 / 种子信息 / 种子详情 的统一入口。"""
    hash_or_magnet = extract_hash(str(event.message).strip())
    if not hash_or_magnet:
        await _say(
            bot,
            event,
            "❌ 请提供磁力链接或32/40位hash\n"
            "用法: 验车 <磁力链接/hash>\n"
            "示例: 验车 08ada5a7a618ea0a19f23f9e1e6e8a5b5e5e5e5e",
        )
        return

    nonebot.logger.info(f"[seed_analyzer] 解析: {hash_or_magnet[:60]}...")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                API_BASE,
                params={"url": hash_or_magnet},
                timeout=aiohttp.ClientTimeout(total=INFO_TIMEOUT),
            ) as response:
                if response.status != 200:
                    await _say(bot, event, f"❌ API 请求失败 (HTTP {response.status})")
                    return
                data = await response.json(content_type=None)

            if data.get("error"):
                await _say(bot, event, f"❌ 解析失败: {data['error']}")
                return

            name = data.get("name") or "未知"
            if not name or name.lower() == "unknown":
                await _say(bot, event, "❌ 未找到该种子信息，请检查链接/hash是否正确")
                return

            file_type = data.get("file_type") or data.get("type") or "unknown"
            result_text = "\n".join(
                [
                    f"{get_icon(file_type)} {name}",
                    f"📂 类型: {file_type}",
                    f"📦 大小: {format_size(data.get('size', 0))}",
                    f"📄 文件数: {data.get('count', 0)}",
                ]
            )
            image_bytes = await _collect_screenshots(session, data.get("screenshots"))

            if isinstance(event, GroupMessageEvent):
                # 群聊：合并转发 —— 单节点，文字和图片 CQ 码拼成一个字符串
                content = result_text
                if image_bytes:
                    content += "\n" + _image_cq(image_bytes)
                node = {
                    "type": "node",
                    "data": {
                        "name": "种子分析",
                        "uin": str(bot.self_id),
                        "content": content,
                    },
                }
                await retry_send_forward_msg(bot, event, node)
            else:
                # 私聊：文字 + 图片合并成一条消息
                if image_bytes:
                    message = MessageSegment.text(result_text + "\n") + MessageSegment.image(
                        image_bytes
                    )
                else:
                    message = MessageSegment.text(result_text)
                await retry_send(bot, event, message)

    except asyncio.TimeoutError:
        await _say(bot, event, "⏱️ 请求超时，请稍后再试")
    except aiohttp.ClientError as exc:
        nonebot.logger.error(f"[seed_analyzer] 网络错误: {exc}")
        await _say(bot, event, f"❌ 网络请求失败: {exc}")
    except ActionFailed as exc:
        # 风控重试已用尽，再发也是白发
        nonebot.logger.error(f"[seed_analyzer] 风控重试耗尽: {exc}")
    except Exception as exc:  # noqa: BLE001 — 兜底：任何意外都要如实回给用户
        nonebot.logger.error(f"[seed_analyzer] 未知错误: {exc}")
        with contextlib.suppress(ActionFailed):   # 又撞上风控就静默，不再叠加报错
            await _say(bot, event, f"❌ 解析失败: {type(exc).__name__}")
