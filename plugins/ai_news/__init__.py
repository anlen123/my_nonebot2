"""
ai_news —— 每日 AI 资讯早报

功能：
  1. 每天早上 9:30（可配置）自动抓取 AI 资讯，渲染成表格图片，私聊推送给指定 QQ
  2. 手动命令（立即抓取并发送）：查询ai新闻（仅此一个，无别名）
  3. 想让机器人重启后立刻推一条：在 data/ai_news/ 下建一个空的 run_now 文件再重启

配置见 .env.dev / .env.prod 中的 AI_NEWS_* 项（详见 ai_news/config.py 顶部注释）
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
from datetime import datetime
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

import nonebot
from nonebot import on_command, require
from nonebot.adapters.onebot.v11 import Bot, Event, Message, MessageSegment

from .config import load_config
from .digest import (
    DATA_DIR,
    LOG_FILE,
    RUN_NOW_FLAG,
    RUN_NOW_STAMP,
    build_digest,
    mark_seen,
    render_failure_png,
    save_digest_png,
)
from .sources import NewsItem

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402

_cfg = load_config()

JOB_ID = "ai_news_daily_digest"

# 启动后等 OneBot 上线：每 5 秒探一次，最多 24 次（约 2 分钟）
_CONNECT_PROBE_TIMES = 24
_CONNECT_PROBE_INTERVAL = 5


def _log(line: str) -> None:
    """写一份可事后查看的日志（控制台日志窗口关了也能查）"""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as fp:
            fp.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [pid {os.getpid()}] {line}\n")
    except OSError as exc:
        nonebot.logger.warning(f"[ai_news] 日志写入失败：{exc}")


def _next_run_time() -> Optional[datetime]:
    """定时任务的下次触发时间；调度器没就绪或触发器不给算就返回 None（仅用于日志）"""
    job = scheduler.get_job(JOB_ID)
    if job is None:
        return None
    try:
        return job.trigger.get_next_fire_time(None, datetime.now())
    except Exception:  # noqa: BLE001 —— 触发器实现各异，量不出来就不显示
        return None


# ── 抓取 + 发送 ───────────────────────────────────────────────────────────────
def _caption(items: Sequence[NewsItem]) -> str:
    """消息里的标题行：日期 + 条数"""
    stamp = datetime.now().strftime("%m-%d")
    return f"🗞 AI 资讯早报 {stamp}（{len(items)} 条）"


def _image_segment(png: bytes) -> MessageSegment:
    """把 PNG 字节封成图片消息段（走 base64 直发，不依赖公网地址）"""
    return MessageSegment.image(f"base64://{base64.b64encode(png).decode()}")


def _digest_message(items: Sequence[NewsItem], png: bytes) -> Message:
    """早报正文：标题行 + 表格图（定时推送与手动命令共用）"""
    return Message(MessageSegment.text(_caption(items))) + _image_segment(png)


async def _make_and_send(bot: Bot, *, use_history: bool, target: str, ttype: str) -> None:
    """抓一轮并推送；use_history=True 时顺带更新历史去重表并存档图片"""
    nonebot.logger.info("[ai_news] 开始抓取 AI 资讯 ...")
    png, items, note = await build_digest(_cfg, use_history=use_history)

    if png is None:
        text = f"⚠️ {note or 'AI 资讯抓取失败'}"
        message = Message(MessageSegment.text(text))
        if not use_history:
            # 手动测试时把失败原因也渲染成图，便于排查
            fail_png = render_failure_png(note or "抓取失败")
            if fail_png:
                message += _image_segment(fail_png)
    else:
        message = _digest_message(items, png)
        if note:
            message += MessageSegment.text(f"\n（{note}）")

    if ttype == "group":
        await bot.send_group_msg(group_id=int(target), message=message)
    else:
        await bot.send_private_msg(user_id=int(target), message=message)

    if png is not None and use_history:
        mark_seen(items)
        save_digest_png(png)
    nonebot.logger.info(f"[ai_news] 推送完成 -> {ttype} {target}，共 {len(items)} 条")


async def push_daily() -> None:
    """定时任务入口（run_now 也复用这里）"""
    try:
        bot: Bot = nonebot.get_bot()
    except ValueError as exc:
        # 当前没有已连接的 bot（比如刚重启），等下一轮或补跑
        nonebot.logger.warning(f"[ai_news] 没有可用的 bot 连接：{exc}")
        return

    try:
        await _make_and_send(
            bot,
            use_history=True,
            target=_cfg["target"],
            ttype=_cfg["target_type"],
        )
    except Exception as exc:  # noqa: BLE001 —— 定时任务不能把异常抛回调度器
        nonebot.logger.error(f"[ai_news] 定时推送失败：{type(exc).__name__}: {exc}")


# ── 手动命令（立即抓取并发送） ──────────────────────────────────────────────
ai_news_cmd = on_command("查询ai新闻", priority=5, block=False)


@ai_news_cmd.handle()
async def _handle_ai_news(bot: Bot, event: Event) -> None:
    """查询ai新闻：立刻抓一轮，把表格图回给发命令的人

    bot / event 是 nonebot 按签名注入的，这个命令直接用 matcher 的 send/finish 回复。
    """
    await ai_news_cmd.send("正在抓取 AI 资讯，稍等十几秒 ...")
    try:
        png, items, note = await build_digest(_cfg, use_history=False)
    except Exception as exc:  # noqa: BLE001 —— 抓取踩坑要把原因回给用户，不能静默
        await ai_news_cmd.finish(f"抓取出错：{type(exc).__name__}: {exc}")
        return

    if png is None:
        await ai_news_cmd.finish(f"⚠️ {note or '没抓到内容'}")
        return

    await ai_news_cmd.finish(
        _digest_message(items, png)
        + (MessageSegment.text(f"\n（{note}）") if note else "")
    )


# ── 重启后立即推一条（data/ai_news/run_now 标记文件） ──────────────────────
def _recently_pushed(seconds: float = 180.0) -> bool:
    """run_now.stamp 是否是 seconds 秒内写的（防止多个进程重复推送）"""
    try:
        stamp = float(RUN_NOW_STAMP.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    return (time.time() - stamp) < seconds


async def _push_run_now() -> None:
    """启动后立即推送（用于「马上发一条给我」），推完删掉标记文件"""
    _log(f"run_now: 进入处理 标记文件={RUN_NOW_FLAG} exists={RUN_NOW_FLAG.exists()}")
    if _recently_pushed():
        _log("run_now: 刚刚已经推过，跳过")
        return
    if not RUN_NOW_FLAG.exists():
        _log("run_now: 没有标记文件，跳过")
        return

    _log("run_now: 开始等待 OneBot 连接")
    for _ in range(_CONNECT_PROBE_TIMES):
        try:
            nonebot.get_bot()
            break
        except ValueError:
            # 还没连上，过一会儿再探
            await asyncio.sleep(_CONNECT_PROBE_INTERVAL)
    else:
        _log("run_now: 2 分钟内没有可用的 OneBot 连接，放弃（标记文件保留）")
        return

    try:
        RUN_NOW_FLAG.unlink()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        RUN_NOW_STAMP.write_text(str(time.time()), encoding="utf-8")
    except OSError as exc:
        _log(f"run_now: 标记文件处理失败：{exc}")

    _log("run_now: 连接就绪，开始抓取并推送")
    await push_daily()
    _log("run_now: 推送流程结束")


async def _push_run_now_guarded() -> None:
    """包一层异常处理，避免后台任务静默死掉"""
    try:
        await _push_run_now()
    except Exception as exc:  # noqa: BLE001 —— 后台任务没人接异常，必须自己收尾
        _log(f"run_now: 执行出错 {type(exc).__name__}: {exc}")
        nonebot.logger.error(f"[ai_news] run_now 执行出错：{type(exc).__name__}: {exc}")


# ── 启动钩子：调度器就绪后按需立即推一条 ──────────────────────────────────────
_driver = nonebot.get_driver()


@_driver.on_startup
async def _on_startup() -> None:
    """driver 启动时打一行环境信息；有 run_now 标记就异步立刻推一条"""
    _log(
        f"driver 启动：调度器运行中={scheduler.running} "
        f"定时任务下次运行={_next_run_time()} "
        f"run_now标记={RUN_NOW_FLAG} exists={RUN_NOW_FLAG.exists()}"
    )
    if RUN_NOW_FLAG.exists():
        try:
            asyncio.create_task(_push_run_now_guarded())
            _log("run_now: 已创建立即推送任务")
        except RuntimeError as exc:
            _log(f"run_now: 创建任务失败 {type(exc).__name__}: {exc}")


# ── 注册定时任务 ──────────────────────────────────────────────────────────────
def _job_brief(*, with_next_run: bool = False) -> str:
    """定时任务的一句话摘要（文件日志与控制台日志共用，免得两处各写一遍）"""
    kind_text = "群" if _cfg["target_type"] == "group" else "私聊"
    names = ",".join(spec["name"] for spec in _cfg["sources"])
    brief = f"cron='{_cfg['raw_cron']}' 时区={_cfg['timezone'] or '本机'}"
    if with_next_run:
        brief += f" 下次运行={_next_run_time()}"
    return f"{brief} 推送={kind_text} {_cfg['target']} 条数={_cfg['max_items']} 源={names}"


def _register_job() -> None:
    """按配置注册每日定时任务；AI_NEWS_ENABLED=false 时不注册"""
    if not _cfg["enabled"]:
        nonebot.logger.info("[ai_news] AI_NEWS_ENABLED=false，未注册定时任务")
        return

    tz = None
    if _cfg["timezone"]:
        try:
            tz = ZoneInfo(_cfg["timezone"])
        except (KeyError, ValueError) as exc:  # ZoneInfoNotFoundError 也是 KeyError
            nonebot.logger.warning(f"[ai_news] 时区 {_cfg['timezone']} 无法识别，改用本机时区：{exc}")

    scheduler.add_job(
        push_daily,
        trigger="cron",
        id=JOB_ID,
        replace_existing=True,
        timezone=tz,
        # 到点时机器人不在线/正忙，一小时内补跑一次，避免漏推
        misfire_grace_time=3600,
        coalesce=True,
        **_cfg["cron"],
    )
    _log(f"插件已加载 {_job_brief(with_next_run=True)}")
    nonebot.logger.info(f"[ai_news] 定时任务已注册：{_job_brief()}")


_register_job()
