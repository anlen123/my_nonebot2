"""
bilibili_live —— B 站直播监控插件

功能：
  - 开播通知（封面、标题、直播间链接）
  - 下播通知（UP主名、时长、粉丝数变化、弹幕排行榜、词云图）
  - 直播中每小时播报（当前时长、在线人数、直播间链接）

配置（.env.dev / .env.prod）：
  BILIBILI_LIVE_UIDS={"uid": ["群号1", "群号2"]}
  BILIBILI_LIVE_INTERVAL=60   # 状态轮询间隔（秒）
"""

import io
import asyncio
import base64
from collections import Counter
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import aiohttp
import nonebot
from nonebot import require
from nonebot.adapters.onebot.v11 import Bot, MessageSegment

from .config import load_config

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

# ── 配置 ──────────────────────────────────────────────────────────────────────
_cfg = load_config()
UIDS: Dict[str, List[str]] = _cfg["bilibili_live_uids"]
INTERVAL: int = _cfg["bilibili_live_interval"]

# ── 运行时状态 ────────────────────────────────────────────────────────────────
live_status: Dict[str, bool] = {uid: False for uid in UIDS}

# live_session[uid] = {
#   start_time, room_id, fans_start,
#   danmaku_counter: Counter(username->count),
#   seen_danmaku: set(已记录的弹幕去重key),
#   peak_online, last_hourly_notify
# }
live_session: Dict[str, dict] = {}

# ── B 站 API ──────────────────────────────────────────────────────────────────
ROOM_INFO_URL   = "https://api.live.bilibili.com/room/v1/Room/getRoomInfoOld"
DANMAKU_URL     = "https://api.live.bilibili.com/xlive/web-room/v1/dM/gethistory"
USER_CARD_URL   = "https://api.bilibili.com/x/web-interface/card"

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
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as s:
            async with s.get(
                ROOM_INFO_URL, params={"mid": uid},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                data = await r.json(content_type=None)
                if data.get("code") == 0:
                    return data["data"]
    except Exception as e:
        nonebot.logger.warning(f"[bilibili_live] fetch_room_info uid={uid}: {e}")
    return None


async def fetch_user_card(uid: str) -> Tuple[str, int]:
    """返回 (用户名, 粉丝数)"""
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as s:
            async with s.get(
                USER_CARD_URL, params={"mid": uid, "photo": "false"},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                data = await r.json(content_type=None)
                if data.get("code") == 0:
                    d = data["data"]
                    name = d.get("card", {}).get("name", uid)
                    fans = d.get("follower", 0)
                    return name, fans
    except Exception as e:
        nonebot.logger.warning(f"[bilibili_live] fetch_user_card uid={uid}: {e}")
    return uid, 0


async def fetch_danmaku_with_user(room_id: int) -> List[Tuple[str, str]]:
    """返回 [(username, text), ...]"""
    result = []
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as s:
            async with s.get(
                DANMAKU_URL,
                params={"roomid": room_id, "csrf_token": "", "csrf": "", "visit_id": ""},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                data = await r.json(content_type=None)
                if data.get("code") == 0:
                    for item in data.get("data", {}).get("room", []):
                        text = item.get("text", "").strip()
                        user = item.get("nickname", "").strip()
                        if text:
                            result.append((user, text))
    except Exception as e:
        nonebot.logger.warning(f"[bilibili_live] fetch_danmaku room={room_id}: {e}")
    return result


async def fetch_cover_base64(url: str) -> Optional[str]:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                return base64.b64encode(await r.read()).decode()
    except Exception as e:
        nonebot.logger.warning(f"[bilibili_live] fetch_cover: {e}")
    return None


def build_wordcloud(texts: List[str]) -> Optional[bytes]:
    try:
        import os
        import jieba
        from wordcloud import WordCloud

        words = []
        for t in texts:
            words.extend(jieba.cut(t, cut_all=False))
        text = " ".join(w for w in words if len(w) > 1)
        if not text.strip():
            return None

        font_path = None
        for p in [
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/System/Library/Fonts/PingFang.ttc",
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
        ]:
            if os.path.exists(p):
                font_path = p
                break

        import matplotlib
        matplotlib.use("Agg")

        wc = WordCloud(
            font_path=font_path,
            width=900, height=450,
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
        nonebot.logger.warning(f"[bilibili_live] build_wordcloud: {e}")
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


def fmt_fans(n: int) -> str:
    if n >= 10000:
        return f"{n / 10000:.1f}万"
    return str(n)


# ── 推送 ──────────────────────────────────────────────────────────────────────

async def notify_groups(group_ids: List[str], messages: list):
    """将 messages 列表拼成一条消息发送，图片和文字合并在同一条"""
    from nonebot.adapters.onebot.v11 import Message
    combined = Message()
    for seg in messages:
        combined += seg
    try:
        bot: Bot = nonebot.get_bot()
    except Exception:
        nonebot.logger.warning("[bilibili_live] no bot available")
        return
    for gid in group_ids:
        try:
            await bot.send_group_msg(group_id=int(gid), message=combined)
            await asyncio.sleep(0.5)
        except Exception as e:
            nonebot.logger.warning(f"[bilibili_live] send group {gid}: {e}")


# ── 开播 ──────────────────────────────────────────────────────────────────────

async def on_live_start(uid: str, info: dict):
    uname, fans = await fetch_user_card(uid)
    title     = info.get("title", "未知标题")
    room_id   = info.get("roomid", "")
    cover_url = info.get("cover", "")
    live_url  = f"https://live.bilibili.com/{room_id}"

    live_session[uid] = {
        "start_time": datetime.now(),
        "room_id": room_id,
        "uname": uname,
        "fans_start": fans,
        "danmaku_counter": Counter(),
        "seen_danmaku": set(),
        "peak_online": info.get("online", 0),
        "last_hourly_notify": datetime.now(),
    }

    msgs = []
    if cover_url:
        cover_b64 = await fetch_cover_base64(cover_url)
        if cover_b64:
            msgs.append(MessageSegment.image(f"base64://{cover_b64}"))

    msgs.append(MessageSegment.text(
        f"🔴 {uname} 开播啦！\n"
        f"📺 {title}\n"
        f"🔗 {live_url}"
    ))

    await notify_groups(UIDS.get(uid, []), msgs)
    nonebot.logger.info(f"[bilibili_live] uid={uid} ({uname}) 开播")


# ── 下播 ──────────────────────────────────────────────────────────────────────

async def on_live_end(uid: str, info: dict):
    session = live_session.pop(uid, {})
    uname   = session.get("uname", uid)
    room_id = session.get("room_id") or info.get("roomid", 0)

    # 时长
    start_time: Optional[datetime] = session.get("start_time")
    duration_str = fmt_duration(
        int((datetime.now() - start_time).total_seconds()) if start_time else 0
    )

    # 粉丝数变化
    _, fans_now = await fetch_user_card(uid)
    fans_start = session.get("fans_start", fans_now)
    fans_delta = fans_now - fans_start
    fans_delta_str = f"+{fans_delta}" if fans_delta >= 0 else str(fans_delta)

    # 弹幕统计
    counter: Counter = session.get("danmaku_counter", Counter())
    # 再拉一次最新弹幕补充
    if room_id:
        latest = await fetch_danmaku_with_user(int(room_id))
        seen   = session.get("seen_danmaku", set())
        for user, text in latest:
            key = f"{user}:{text}"
            if key not in seen and user:
                counter[user] += 1
    total_danmaku = sum(counter.values())
    total_users   = len(counter)

    # 排行榜 Top3 + 特别嘉奖
    top = counter.most_common(5)

    medals = ["🥇", "🥈", "🥉"]
    rank_lines = []
    for i, (name, cnt) in enumerate(top[:3]):
        rank_lines.append(f"{medals[i]} {name} - {cnt} 条")
    special = [name for name, _ in top[3:5]]

    rank_text = "\n".join(rank_lines) if rank_lines else "暂无数据"
    special_text = (
        f"\n🎖️ 特别嘉奖：{'  &  '.join(special)}" if special else ""
    )

    # 下播通知文字
    end_text = (
        f"{uname} 下播啦，本次直播了 {duration_str}，粉丝数变化 {fans_delta_str}\n\n"
        f"🔍【弹幕情报站】本场直播数据如下：\n"
        f"🧍 总共 {total_users} 位观众上线\n"
        f"💬 共计 {total_danmaku} 条弹幕飞驰而过\n"
        f"📊 热词云图已生成，快来看看你有没有上榜！\n\n"
        f"👑 本场顶级输出选手：\n"
        f"{rank_text}"
        f"{special_text}\n\n"
        f"你们的弹幕，我们都记录在案！🕵️"
    )

    msgs = [MessageSegment.text(end_text)]

    # 词云
    all_texts = [text for _, text in (await fetch_danmaku_with_user(int(room_id)) if room_id else [])]
    # 用 counter 里已有的弹幕文本凑词云（从 seen_danmaku 提取 text 部分）
    seen = session.get("seen_danmaku", set())
    wc_texts = [k.split(":", 1)[1] for k in seen if ":" in k] + all_texts
    if wc_texts:
        wc_bytes = await asyncio.get_event_loop().run_in_executor(
            None, build_wordcloud, wc_texts
        )
        if wc_bytes:
            msgs.append(MessageSegment.image(f"base64://{base64.b64encode(wc_bytes).decode()}"))

    await notify_groups(UIDS.get(uid, []), msgs)
    nonebot.logger.info(f"[bilibili_live] uid={uid} ({uname}) 下播")


# ── 直播中弹幕累积 & 每小时播报 ───────────────────────────────────────────────

async def update_session_danmaku(uid: str, room_id: int):
    """拉取最新弹幕，增量更新 counter"""
    session = live_session.get(uid)
    if not session:
        return
    items = await fetch_danmaku_with_user(room_id)
    seen: set = session["seen_danmaku"]
    counter: Counter = session["danmaku_counter"]
    for user, text in items:
        key = f"{user}:{text}"
        if key not in seen and user:
            seen.add(key)
            counter[user] += 1


async def send_hourly_report(uid: str, info: dict):
    session = live_session.get(uid)
    if not session:
        return

    uname    = session.get("uname", uid)
    start    = session["start_time"]
    duration = fmt_duration(int((datetime.now() - start).total_seconds()))
    online   = info.get("online", 0)
    room_id  = session.get("room_id", "")
    live_url = f"https://live.bilibili.com/{room_id}"

    online_str = fmt_fans(online) if online else "未知"

    text = (
        f"📡 {uname} 正在直播\n"
        f"⏱️ 目前已播 {duration}\n"
        f"👥 当前在线人数：{online_str}\n"
        f"🔗 {live_url}"
    )
    await notify_groups(UIDS.get(uid, []), [MessageSegment.text(text)])
    session["last_hourly_notify"] = datetime.now()
    nonebot.logger.info(f"[bilibili_live] uid={uid} 每小时播报已发送")


# ── 定时轮询 ──────────────────────────────────────────────────────────────────

@scheduler.scheduled_job("interval", seconds=INTERVAL, id="bilibili_live_poll")
async def poll_live_status():
    if not UIDS:
        return

    nonebot.logger.debug(f"[bilibili_live] 开始轮询，共 {len(UIDS)} 个 uid")

    for uid in list(UIDS.keys()):
        nonebot.logger.debug(f"[bilibili_live] 正在请求 uid={uid} 的直播状态...")
        info = await fetch_room_info(uid)
        if info is None:
            nonebot.logger.warning(f"[bilibili_live] uid={uid} 接口请求失败，跳过")
            continue

        is_live  = info.get("liveStatus") == 1
        was_live = live_status.get(uid, False)
        nonebot.logger.debug(
            f"[bilibili_live] uid={uid} liveStatus={info.get('liveStatus')} "
            f"is_live={is_live} was_live={was_live} title={info.get('title','')[:20]}"
        )

        if is_live and not was_live:
            nonebot.logger.info(f"[bilibili_live] uid={uid} 检测到开播，触发开播通知")
            live_status[uid] = True
            await on_live_start(uid, info)

        elif not is_live and was_live:
            nonebot.logger.info(f"[bilibili_live] uid={uid} 检测到下播，触发下播通知")
            live_status[uid] = False
            await on_live_end(uid, info)

        elif is_live and uid in live_session:
            session = live_session[uid]

            online = info.get("online", 0)
            if online > session.get("peak_online", 0):
                session["peak_online"] = online

            room_id = session.get("room_id")
            if room_id:
                before = sum(session["danmaku_counter"].values())
                await update_session_danmaku(uid, int(room_id))
                after = sum(session["danmaku_counter"].values())
                if after > before:
                    nonebot.logger.debug(f"[bilibili_live] uid={uid} 新增弹幕 {after - before} 条，累计 {after} 条")

            last = session.get("last_hourly_notify")
            if last and (datetime.now() - last).total_seconds() >= 3600:
                nonebot.logger.info(f"[bilibili_live] uid={uid} 触发每小时播报")
                await send_hourly_report(uid, info)

        await asyncio.sleep(1)

    nonebot.logger.debug(f"[bilibili_live] 本轮轮询结束")


nonebot.logger.info(
    f"[bilibili_live] 插件已加载，监控 {len(UIDS)} 个 uid，轮询间隔 {INTERVAL}s"
)
