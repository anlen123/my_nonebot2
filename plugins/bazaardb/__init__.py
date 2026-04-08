"""
bazaardb -- BazaarDB 物品 & 怪物 & 用户查询插件

用法：
  bz <关键词>          查询物品/怪物，例如：bz 光纤 / bz 多尔王
  巴扎查分 <用户名>    查询用户排位分数，例如：巴扎查分 xikala
"""

import asyncio
import base64
import importlib.util
from pathlib import Path

import nonebot
from nonebot.plugin import on_regex
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment

# 脚本目录（与插件同目录）
SCRAPER_DIR = Path(__file__).parent

# 缓存目录
CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

bz      = on_regex(pattern=r"^bz ")
bz_user = on_regex(pattern=r"^巴扎查分 ")


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _load_scraper(filename: str):
    path = SCRAPER_DIR / filename
    spec = importlib.util.spec_from_file_location(filename[:-3], path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _send_image(bot: Bot, event, img_bytes: bytes):
    img_b64 = base64.b64encode(img_bytes).decode()
    await bot.send(event=event, message=MessageSegment.image(f"base64://{img_b64}"))


# ── bz <关键词>：物品 / 怪物查询 ─────────────────────────────────────────────

def _item_cache_path(keyword: str) -> Path:
    safe = keyword.replace("/", "_").replace("\\", "_")
    return CACHE_DIR / f"bazaardb_{safe}.png"


def _query_item_sync(keyword: str) -> bytes | None:
    scraper = _load_scraper("bazaardb_scraper.py")
    asyncio.run(scraper.scrape_and_export(keyword, str(CACHE_DIR)))
    safe = keyword.replace("/", "_").replace("\\", "_")
    png  = CACHE_DIR / f"bazaardb_{safe}.png"
    return png.read_bytes() if png.exists() else None


@bz.handle()
async def bz_rev(bot: Bot, event: Event):
    keyword = str(event.message).strip()[3:].strip()
    if not keyword:
        await bot.send(event=event, message=MessageSegment.text("请输入关键词，例如：bz 光纤"))
        return

    cache_file = _item_cache_path(keyword)
    if cache_file.exists():
        nonebot.logger.info(f"[bazaardb] 命中缓存 keyword={keyword}")
        await _send_image(bot, event, cache_file.read_bytes())
        return

    await bot.send(event=event, message=MessageSegment.text(f"正在查询「{keyword}」，请稍候..."))
    try:
        img_bytes = await asyncio.get_event_loop().run_in_executor(None, _query_item_sync, keyword)
    except Exception as e:
        nonebot.logger.warning(f"[bazaardb] 查询失败 keyword={keyword}: {e}")
        await bot.send(event=event, message=MessageSegment.text(f"查询失败：{e}"))
        return

    if img_bytes is None:
        await bot.send(event=event, message=MessageSegment.text(f"未找到「{keyword}」相关物品或怪物"))
        return

    cache_file.write_bytes(img_bytes)
    nonebot.logger.info(f"[bazaardb] 已缓存 keyword={keyword}")
    await _send_image(bot, event, img_bytes)


# ── 巴扎查分 <用户名>：用户排位查询 ──────────────────────────────────────────

def _user_cache_path(username: str) -> Path:
    safe = username.replace("/", "_").replace("\\", "_")
    return CACHE_DIR / f"bazaar_{safe}_s14.png"


def _query_user_sync(username: str) -> bytes | None:
    scraper = _load_scraper("bazaar_user_scraper.py")
    asyncio.run(scraper.scrape_and_export(username, "14", str(CACHE_DIR)))
    safe = username.replace("/", "_").replace("\\", "_")
    png  = CACHE_DIR / f"bazaar_{safe}_s14.png"
    return png.read_bytes() if png.exists() else None


@bz_user.handle()
async def bz_user_rev(bot: Bot, event: Event):
    username = str(event.message).strip()[5:].strip()
    if not username:
        await bot.send(event=event, message=MessageSegment.text("请输入用户名，例如：巴扎查分 xikala"))
        return

    await bot.send(event=event, message=MessageSegment.text(f"正在查询「{username}」的排位数据，请稍候..."))
    try:
        img_bytes = await asyncio.get_event_loop().run_in_executor(None, _query_user_sync, username)
    except Exception as e:
        nonebot.logger.warning(f"[bazaardb] 查分失败 username={username}: {e}")
        await bot.send(event=event, message=MessageSegment.text(f"查询失败：{e}"))
        return

    if img_bytes is None:
        await bot.send(event=event, message=MessageSegment.text(f"未找到「{username}」的排位记录（仅记录传奇段位以上）"))
        return

    nonebot.logger.info(f"[bazaardb] 查分成功 username={username}")
    await _send_image(bot, event, img_bytes)

