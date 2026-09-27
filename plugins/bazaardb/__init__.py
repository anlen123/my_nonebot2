"""巴扎卡片查询 —— 数据来自 bazaar-cards.com

用法：
  巴扎 <关键词>              查卡，例如：巴扎 光纤 / 巴扎 Lionfish
  巴扎别名                   列出所有别名
  巴扎别名 <别名>            查看某个别名指向哪张卡
  巴扎别名 <别名> <卡名>     设置别名

说明：
  * 卡牌名、技能、标签、档位全部走站点官方简中词条，没有译文的地方保留英文原文；
  * 结果附一张站点上的卡牌原图，和描述拼成一条消息发出，不带网站链接；
  * 多张匹配时默认取最相关的一张给详情。
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Dict

import nonebot
from nonebot import on_regex
from nonebot.adapters.onebot.v11 import Bot, Event, Message, MessageSegment
from PIL import Image

from .cards import Card, fetch_image, library
from .view import format_card

bazaar = on_regex(pattern=r"^巴扎")

HELP = """用法：
  巴扎 <关键词>　　　　　查卡，例如：巴扎 光纤
  巴扎别名　　　　　　　列出所有别名
  巴扎别名 <别名>　　　　查看别名
  巴扎别名 <别名> <卡名>　设置别名
说明：卡牌正文为官方简中，未收录译文处保留英文；结果另附卡牌原图。"""

_HELP_WORDS = {"", "help", "-h", "--help", "?", "？", "帮助"}
_MAX_MATCHES = 8

# ── 别名持久化：{ "别名": "卡名" } ─────────────────────────────────────────────
ALIAS_FILE = Path(__file__).parent / "aliases.json"


def _load_aliases() -> Dict[str, str]:
    try:
        return json.loads(ALIAS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001 — 别名文件坏掉不该拖垮插件
        nonebot.logger.warning(f"[bazaardb] 别名文件不可用，按空处理：{exc}")
        return {}


def _save_aliases(aliases: Dict[str, str]) -> None:
    try:
        ALIAS_FILE.write_text(
            json.dumps(aliases, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as exc:  # noqa: BLE001
        nonebot.logger.warning(f"[bazaardb] 写入别名失败：{exc}")


_aliases: Dict[str, str] = _load_aliases()


# ── 命令入口：只有一个匹配器，子命令在这里分发 ──────────────────────────────────
@bazaar.handle()
async def bazaar_rev(bot: Bot, event: Event) -> None:
    argument = str(event.message).strip()[2:].strip()  # 去掉开头的「巴扎」
    if argument.lower() in _HELP_WORDS:
        await bot.send(event=event, message=MessageSegment.text(HELP))
        return

    command, _, rest = argument.partition(" ")
    if command == "别名":
        await _handle_alias(bot, event, rest.strip())
        return

    await _handle_query(bot, event, argument)


# ── 查卡 ──────────────────────────────────────────────────────────────────────
async def _handle_query(bot: Bot, event: Event, keyword: str) -> None:
    keyword = _aliases.get(keyword, keyword)
    try:
        await library.ensure()
    except Exception as exc:  # noqa: BLE001
        nonebot.logger.warning(f"[bazaardb] 卡牌库不可用：{exc}")
        await bot.send(event=event, message=MessageSegment.text(f"卡牌库暂时取不到：{exc}"))
        return

    matches = library.search(keyword, limit=_MAX_MATCHES)
    if not matches:
        await bot.send(
            event=event,
            message=MessageSegment.text(
                f"没找到「{keyword}」相关的卡，换个关键词试试，例如：巴扎 护盾 / 巴扎 Lionfish"
            ),
        )
        return

    # 多张匹配时默认取第一张 —— search 已按「完全命中 → 前缀 → 包含 → 标签/英雄」排好序。
    card = matches[0]
    nonebot.logger.info(
        f"[bazaardb] 命中卡片 keyword={keyword} card={card.name} 匹配数={len(matches)}"
    )

    # 描述和图片拼成同一条消息发出，不分两条。
    # 图取站点上的卡牌原图（webp 无损转 PNG），取不到就只发描述。
    text = format_card(card, await library.detail(card.id))
    if len(matches) > 1:  # 顺带说明为什么给的是这张
        total = f"{len(matches)} 张以上" if len(matches) >= _MAX_MATCHES else f"{len(matches)} 张"
        text = f"🔍 「{keyword}」匹配到 {total}，取最相关的一张：\n{text}"
    message = Message(MessageSegment.text(text)) + await _card_image(card)
    await bot.send(event=event, message=message)


def _as_png(raw: bytes) -> bytes:
    """站点原图是 webp，无损转成 PNG 再发 —— QQ 对 PNG 兼容最好，画质不变。"""
    try:
        with Image.open(io.BytesIO(raw)) as image:
            buffer = io.BytesIO()
            image.save(buffer, "PNG", optimize=True)
    except Exception as exc:  # noqa: BLE001 — 转不动就原样发，别把图弄丢
        nonebot.logger.warning(f"[bazaardb] 原图转 PNG 失败，改为原样发送：{exc}")
        return raw
    return buffer.getvalue()


async def _card_image(card: Card) -> Message:
    """附上站点上的卡牌原图；取不到返回空消息，描述照常给。"""
    raw = await fetch_image(card.image_url)
    if not raw:
        return Message()
    data = _as_png(raw)
    nonebot.logger.info(
        f"[bazaardb] 卡牌原图就绪：{card.name}（{len(raw) / 1024:.0f} KB → PNG {len(data) / 1024:.0f} KB）"
    )
    return Message(MessageSegment.image(f"base64://{base64.b64encode(data).decode()}"))


# ── 别名 ──────────────────────────────────────────────────────────────────────
async def _handle_alias(bot: Bot, event: Event, argument: str) -> None:
    parts = argument.split()

    if len(parts) >= 2:
        alias, target = parts[0], " ".join(parts[1:])
        _aliases[alias] = target
        _save_aliases(_aliases)
        nonebot.logger.info(f"[bazaardb] 设置别名 {alias} → {target}")
        await bot.send(
            event=event,
            message=MessageSegment.text(f"✅ 已设置别名：巴扎 {alias} → 实际查询「{target}」"),
        )
        return

    if parts:
        alias = parts[0]
        target = _aliases.get(alias)
        text = f"📌 巴扎 {alias} → 「{target}」" if target else f"「{alias}」还没有设别名"
        await bot.send(event=event, message=MessageSegment.text(text))
        return

    if not _aliases:
        await bot.send(event=event, message=MessageSegment.text("当前没有任何别名"))
        return
    body = "\n".join(f"　{alias} → {target}" for alias, target in _aliases.items())
    await bot.send(event=event, message=MessageSegment.text(f"📋 当前别名：\n{body}"))
