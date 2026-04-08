"""
bazaardb -- BazaarDB 物品 & 怪物查询插件

用法：bz <关键词>
示例：bz 光纤
      bz 多尔王

直接调用 bazaardb_scraper.py 生成 PNG，发送到 QQ。
"""

import asyncio
import base64
import sys
from pathlib import Path

import nonebot
from nonebot.plugin import on_regex
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment

# 脚本路径
SCRAPER_DIR = Path("/Users/liuhq/CatPaw-Desk")

# 缓存目录：插件同级的 cache/ 文件夹
CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

bz = on_regex(pattern=r"^bz ")


def _cache_path(keyword: str) -> Path:
    # 与 bazaardb_scraper.py 的命名保持一致：bazaardb_{safe_key}.png
    safe = keyword.replace("/", "_").replace("\\", "_")
    return CACHE_DIR / f"bazaardb_{safe}.png"


@bz.handle()
async def bz_rev(bot: Bot, event: Event):
    keyword = str(event.message).strip()[3:].strip()
    if not keyword:
        await bot.send(event=event, message=MessageSegment.text("请输入关键词，例如：bz 光纤"))
        return

    # 命中缓存直接发送
    cache_file = _cache_path(keyword)
    if cache_file.exists():
        nonebot.logger.info(f"[bazaardb] 命中缓存 keyword={keyword}")
        img_b64 = base64.b64encode(cache_file.read_bytes()).decode()
        await bot.send(event=event, message=MessageSegment.image(f"base64://{img_b64}"))
        return

    await bot.send(event=event, message=MessageSegment.text(f"正在查询「{keyword}」，请稍候..."))

    try:
        img_bytes = await asyncio.get_event_loop().run_in_executor(
            None, _query_sync, keyword
        )
    except Exception as e:
        nonebot.logger.warning(f"[bazaardb] 查询失败 keyword={keyword}: {e}")
        await bot.send(event=event, message=MessageSegment.text(f"查询失败：{e}"))
        return

    if img_bytes is None:
        await bot.send(event=event, message=MessageSegment.text(f"未找到「{keyword}」相关物品或怪物"))
        return

    # 写入缓存
    cache_file.write_bytes(img_bytes)
    nonebot.logger.info(f"[bazaardb] 已缓存 keyword={keyword} -> {cache_file}")

    img_b64 = base64.b64encode(img_bytes).decode()
    await bot.send(event=event, message=MessageSegment.image(f"base64://{img_b64}"))


def _query_sync(keyword: str) -> bytes | None:
    """
    直接调用 bazaardb_scraper.py 的 scrape_and_export，
    输出 PNG 到 CACHE_DIR，读取后返回 bytes。
    """
    import asyncio
    import importlib.util

    # 动态加载脚本模块
    scraper_path = SCRAPER_DIR / "bazaardb_scraper.py"
    spec = importlib.util.spec_from_file_location("bazaardb_scraper", scraper_path)
    scraper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scraper)

    # 用脚本自己的 asyncio.run 执行（在线程池中，不影响 NoneBot 事件循环）
    asyncio.run(scraper.scrape_and_export(keyword, str(CACHE_DIR)))

    png_path = CACHE_DIR / f"bazaardb_{keyword.replace('/', '_').replace(chr(92), '_')}.png"
    if png_path.exists():
        return png_path.read_bytes()
    return None

