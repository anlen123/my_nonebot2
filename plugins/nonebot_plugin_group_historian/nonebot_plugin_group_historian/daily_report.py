"""话痨榜每日零点推送（本仓库本地追加，上游没有这块）。

做法：
  * 每天 00:00（Asia/Shanghai，apscheduler 的默认时区）跑一次；
  * 发的是「刚结束的那一天」的榜 —— 零点新一天刚开始，当天的数据还是空的；
  * 发给那一天有发言记录的每个群，没记录的群跳过（不用额外配群号名单）；
  * 内容与 /今日话痨榜 命令一致：排行榜图片 + 一行说明文字。

注意：本文件是对第三方插件 nonebot_plugin_group_historian 的本地追加，
      上游更新覆盖插件目录后会丢失，需照 __init__.py.bak-localpatch 重放。
"""

from __future__ import annotations

import asyncio
from datetime import date as Date, datetime, timedelta
from typing import Optional

import nonebot
from nonebot import require
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.log import logger
from sqlalchemy import select

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from .data import DailyMessage, get_daily_ranking
from .image import create_ranking_image

SEND_GAP = 0.5      # 逐个群发送之间的间隔（秒），避免撞上风控
JOB_ID = "historian_daily_report"


async def groups_with_data(day: Date) -> list[str]:
    """某一天有发言记录的所有群号。"""
    from nonebot_plugin_orm import get_session

    async with get_session() as session:
        result = await session.execute(
            select(DailyMessage.group_id).where(DailyMessage.timestamp == day).distinct()
        )
        return [str(row[0]) for row in result.fetchall()]


async def send_daily_report(day: Optional[Date] = None) -> int:
    """把某一天的话痨榜推给当天有记录的每个群，返回成功发送的群数。

    day 不传时取「刚结束的那一天」（今天减一天）—— 定时任务就是这么调的；
    传具体日期便于手动补发与测试。
    """
    target = day or (datetime.now().date() - timedelta(days=1))

    groups = await groups_with_data(target)
    if not groups:
        logger.info(f"[群聊史官] {target} 没有任何群有发言记录，跳过每日推送")
        return 0

    try:
        bot = nonebot.get_bot()
    except Exception as exc:  # noqa: BLE001 — 机器人没连上就放弃本次，别把定时任务带崩
        logger.warning(f"[群聊史官] 没有可用的 bot，放弃本次推送：{exc}")
        return 0

    from . import config  # 延迟导入，避开包初始化顺序

    sent = 0
    for group_id in groups:
        try:
            ranking = await get_daily_ranking(group_id, target)
            if not ranking:
                continue
            img_bytes = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: create_ranking_image(
                    ranking, page=1, rank_count=config.historian_rank_count
                ),
            )
            await bot.send_group_msg(
                group_id=int(group_id),
                message=MessageSegment.image(img_bytes)
                + MessageSegment.text(f"📊 {target} 话痨榜出炉啦～"),
            )
            sent += 1
            await asyncio.sleep(SEND_GAP)
        except Exception as exc:  # noqa: BLE001 — 单个群失败不影响其他群
            logger.warning(f"[群聊史官] 群 {group_id} 推送话痨榜失败：{type(exc).__name__} {exc}")

    logger.info(f"[群聊史官] {target} 话痨榜已推送到 {sent}/{len(groups)} 个群")
    return sent


@scheduler.scheduled_job("cron", hour=0, minute=0, id=JOB_ID)
async def historian_daily_report() -> None:
    """每天零点，把刚结束的那一天的话痨榜发到各群。"""
    await send_daily_report()
