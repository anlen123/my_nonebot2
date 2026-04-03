"""
bilibili_live —— B 站直播监控插件

功能：
  - 定时轮询配置中每个 uid 的直播状态
  - 开播时向绑定群推送开播通知（含封面、标题、分区）
  - 下播时向绑定群推送下播通知，附带：
      · 本场直播时长、最高人气、弹幕总数、送礼总数
      · 弹幕词云图（base64 图片消息）

配置（.env.dev / .env.prod）：
  BILIBILI_LIVE_UIDS={"12345678": ["群号1", "群号2"], "87654321": ["群号1"]}
  BILIBILI_LIVE_INTERVAL=60   # 轮询间隔秒数，默认 60
"""

import io
import asyncio
import base64
from datetime import datetime
from typing import Dict, List, Optional

import aiohttp
import nonebot
from nonebot import require
from nonebot.adapters.onebot.v11 import Bot, MessageSegment

from .config import load_config

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

# ── 初始化配置 ────────────────────────────────────────────────────────────────
_cfg = load_config()
UIDS: Dict[str, List[str]] = _cfg["bilibili_live_uids"]     # uid -> [group_id, ...]
INTERVAL: int = _cfg["bilibili_live_interval"]

# ── 运行时状态 ────────────────────────────────────────────────────────────────
# live_status[uid] = True/False  (是否正在直播)
live_status: Dict[str, bool] = {uid: False for uid in UIDS}

# live_session[uid] = { "start_time", "danmaku", "gift_count", "peak_online" }
live_session: Dict[str, dict] = {}

# ── B 站 API ──────────────────────────────────────────────────────────────────
ROOM_INFO_URL = "https://api.live.bilibili.com/room/v1/Room/getRoomInfoOld"
DANMAKU_URL   = "https://api.live.bilibili.com/xlive/web-room/v1/dM/gethistory"
STAT_URL      = "https://api.live.bilibili.com/room/v1/Room/room_init"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://live.bilibili.com",
}


# ── 工具函数 ──────────────────────────────────────────────────────────────────

async def fetch_room_info(uid: str) -> Optional[dict]:
    """查询 uid 对应的直播间基础信息"""
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(
                ROOM_INFO_URL, params={"mid": uid}, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                data = await resp.json(content_type=None)
                if data.get("code") == 0:
                    return data["data"]
    except Exception as e:
        nonebot.logger.warning(f"[bilibili_live] fetch_room_info uid={uid} error: {e}")
    return None


async def fetch_danmaku(room_id: int) -> List[str]:
    """获取直播间最近弹幕列表（最多 100 条历史）"""
    danmaku_list: List[str] = []
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(
                DANMAKU_URL,
                params={"roomid": room_id, "csrf_token": "", "csrf": "", "visit_id": ""},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json(content_type=None)
                if data.get("code") == 0:
                    for item in data.get("data", {}).get("room", []):
                        text = item.get("text", "").strip()
                        if text:
                            danmaku_list.append(text)
    except Exception as e:
        nonebot.logger.warning(f"[bilibili_live] fetch_danmaku room_id={room_id} error: {e}")
    return danmaku_list


async def fetch_cover_base64(url: str) -> Optional[str]:
    """下载封面图并转 base64"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                raw = await resp.read()
                return base64.b64encode(raw).decode()
    except Exception as e:
        nonebot.logger.warning(f"[bilibili_live] fetch_cover error: {e}")
    return None


def build_wordcloud(texts: List[str]) -> Optional[bytes]:
    """
    用 jieba 分词 + wordcloud 生成词云，返回 PNG bytes。
    若依赖缺失则返回 None（不影响主流程）。
    """
    try:
        import jieba
        from wordcloud import WordCloud
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        words = []
        for t in texts:
            words.extend(jieba.cut(t, cut_all=False))
        text = " ".join(w for w in words if len(w) > 1)
        if not text.strip():
            return None

        # 尝试找一个中文字体，找不到就用默认
        font_path = None
        import os
        candidates = [
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/System/Library/Fonts/PingFang.ttc",
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
        ]
        for p in candidates:
            if os.path.exists(p):
                font_path = p
                break

        wc = WordCloud(
            font_path=font_path,
            width=800,
            height=400,
            background_color="white",
            max_words=150,
            collocations=False,
        ).generate(text)

        buf = io.BytesIO()
        wc.to_image().save(buf, format="PNG")
        return buf.getvalue()
    except ImportError as e:
        nonebot.logger.warning(f"[bilibili_live] wordcloud deps missing: {e}")
    except Exception as e:
        nonebot.logger.warning(f"[bilibili_live] build_wordcloud error: {e}")
    return None


def fmt_duration(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h}小时{m}分{s}秒"
    if m:
        return f"{m}分{s}秒"
    return f"{s}秒"


# ── 推送逻辑 ──────────────────────────────────────────────────────────────────

async def notify_groups(group_ids: List[str], messages: list):
    """向指定群列表推送消息列表（每条消息单独 send）"""
    bot: Optional[Bot] = None
    try:
        bot = nonebot.get_bot()
    except Exception:
        nonebot.logger.warning("[bilibili_live] no bot available, skip notify")
        return

    for gid in group_ids:
        for msg in messages:
            try:
                await bot.send_group_msg(group_id=int(gid), message=msg)
                await asyncio.sleep(0.5)   # 避免风控
            except Exception as e:
                nonebot.logger.warning(f"[bilibili_live] send to group {gid} failed: {e}")


async def on_live_start(uid: str, info: dict):
    """开播处理"""
    uname     = info.get("uname", uid)
    title     = info.get("title", "未知标题")
    area_name = info.get("area_name", "")
    room_id   = info.get("room_id", "")
    cover_url = info.get("cover", "") or info.get("user_cover", "")
    live_url  = f"https://live.bilibili.com/{room_id}"

    # 记录本场开始时间
    live_session[uid] = {
        "start_time": datetime.now(),
        "room_id": room_id,
        "danmaku": [],
        "gift_count": 0,
        "peak_online": 0,
    }

    msgs = []

    # 封面图
    if cover_url:
        cover_b64 = await fetch_cover_base64(cover_url)
        if cover_b64:
            msgs.append(MessageSegment.image(f"base64://{cover_b64}"))

    # 文字通知
    text = (
        f"🔴 【开播通知】\n"
        f"UP主：{uname}\n"
        f"标题：{title}\n"
        f"分区：{area_name}\n"
        f"直播间：{live_url}"
    )
    msgs.append(MessageSegment.text(text))

    group_ids = UIDS.get(uid, [])
    await notify_groups(group_ids, msgs)
    nonebot.logger.info(f"[bilibili_live] uid={uid} ({uname}) 开播，已通知群 {group_ids}")


async def on_live_end(uid: str, info: dict):
    """下播处理：发下播通知 + 弹幕词云"""
    uname   = info.get("uname", uid)
    room_id = info.get("room_id") or live_session.get(uid, {}).get("room_id", 0)

    session = live_session.pop(uid, {})
    start_time: Optional[datetime] = session.get("start_time")
    duration_str = "未知"
    if start_time:
        duration_sec = int((datetime.now() - start_time).total_seconds())
        duration_str = fmt_duration(duration_sec)

    # 获取最近弹幕（词云素材）
    danmaku_list: List[str] = []
    if room_id:
        danmaku_list = await fetch_danmaku(int(room_id))

    danmaku_count = len(danmaku_list)

    # 下播文字通知
    text = (
        f"⚫ 【下播通知】\n"
        f"UP主：{uname}\n"
        f"本场时长：{duration_str}\n"
        f"弹幕数（近期）：{danmaku_count} 条"
    )

    msgs = [MessageSegment.text(text)]

    # 弹幕词云
    if danmaku_list:
        wc_bytes = await asyncio.get_event_loop().run_in_executor(
            None, build_wordcloud, danmaku_list
        )
        if wc_bytes:
            wc_b64 = base64.b64encode(wc_bytes).decode()
            msgs.append(MessageSegment.text("📊 弹幕词云："))
            msgs.append(MessageSegment.image(f"base64://{wc_b64}"))
        else:
            # 词云生成失败时，降级展示弹幕文本
            preview = "、".join(danmaku_list[:20])
            if len(danmaku_list) > 20:
                preview += f"……（共 {danmaku_count} 条）"
            msgs.append(MessageSegment.text(f"💬 近期弹幕：\n{preview}"))

    group_ids = UIDS.get(uid, [])
    await notify_groups(group_ids, msgs)
    nonebot.logger.info(f"[bilibili_live] uid={uid} ({uname}) 下播，已通知群 {group_ids}")


# ── 定时任务 ──────────────────────────────────────────────────────────────────

@scheduler.scheduled_job("interval", seconds=INTERVAL, id="bilibili_live_poll")
async def poll_live_status():
    """每隔 INTERVAL 秒轮询一次所有配置 uid 的直播状态"""
    if not UIDS:
        return

    for uid in list(UIDS.keys()):
        info = await fetch_room_info(uid)
        if info is None:
            continue

        # live_status: 1=直播中, 0=未直播, 2=轮播
        is_live = info.get("live_status") == 1
        was_live = live_status.get(uid, False)

        if is_live and not was_live:
            # 刚开播
            live_status[uid] = True
            await on_live_start(uid, info)

        elif not is_live and was_live:
            # 刚下播
            live_status[uid] = False
            await on_live_end(uid, info)

        # 若正在直播，更新峰值人气
        if is_live and uid in live_session:
            online = info.get("online", 0)
            if online > live_session[uid].get("peak_online", 0):
                live_session[uid]["peak_online"] = online

        await asyncio.sleep(1)   # 相邻 uid 请求间隔，避免触发 B 站限流


nonebot.logger.info(
    f"[bilibili_live] 插件已加载，监控 {len(UIDS)} 个 uid，轮询间隔 {INTERVAL}s"
)
