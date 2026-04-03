"""
bilibili_video —— B 站新视频通知插件

功能：
  - 定时轮询配置中每个 uid 的最新投稿
  - 发现新视频时向绑定群推送通知（封面、标题、简介、时长、播放量、链接）

配置（.env.dev / .env.prod）：
  BILIBILI_VIDEO_UIDS={"uid": ["群号1", "群号2"]}
  BILIBILI_VIDEO_INTERVAL=300       # 轮询间隔（秒），默认 300
  BILIBILI_SESSDATA=你的SESSDATA    # B站登录Cookie，用于绕过风控
"""

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

# ── 配置 ──────────────────────────────────────────────────────────────────────
_cfg = load_config()
UIDS: Dict[str, List[str]] = _cfg["bilibili_video_uids"]
INTERVAL: int = _cfg["bilibili_video_interval"]
SESSDATA: str = _cfg["bilibili_sessdata"]

# ── 运行时状态：记录每个 uid 已知的最新视频 bvid ──────────────────────────────
# 首次启动时先静默加载一次，避免把历史视频当新视频推送
latest_bvid: Dict[str, str] = {}
initialized: Dict[str, bool] = {}

# ── 请求头 ────────────────────────────────────────────────────────────────────
BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://space.bilibili.com",
}


def _make_cookies() -> dict:
    if SESSDATA:
        return {"SESSDATA": SESSDATA}
    return {}


# ── B 站接口 ──────────────────────────────────────────────────────────────────

async def fetch_latest_videos(uid: str, ps: int = 5) -> List[dict]:
    """
    获取 uid 最新投稿视频列表，返回 [{bvid, title, desc, pic, duration, stat, pubdate, url}, ...]
    需要 SESSDATA Cookie 才能正常访问。
    """
    try:
        async with aiohttp.ClientSession(
            headers=BASE_HEADERS, cookies=_make_cookies()
        ) as s:
            async with s.get(
                "https://api.bilibili.com/x/space/arc/search",
                params={
                    "mid": uid, "ps": ps, "pn": 1,
                    "order": "pubdate", "jsonp": "jsonp",
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                data = await r.json(content_type=None)
                if data.get("code") != 0:
                    nonebot.logger.warning(
                        f"[bilibili_video] fetch uid={uid} code={data.get('code')} msg={data.get('message')}"
                    )
                    return []
                vlist = data.get("data", {}).get("list", {}).get("vlist", [])
                result = []
                for v in vlist:
                    result.append({
                        "bvid":     v.get("bvid", ""),
                        "title":    v.get("title", ""),
                        "desc":     v.get("description", ""),
                        "pic":      v.get("pic", ""),
                        "duration": v.get("length", ""),       # "mm:ss" 格式
                        "play":     v.get("play", 0),
                        "comment":  v.get("comment", 0),
                        "pubdate":  v.get("created", 0),
                        "url":      f"https://www.bilibili.com/video/{v.get('bvid', '')}",
                    })
                return result
    except Exception as e:
        nonebot.logger.warning(f"[bilibili_video] fetch_latest_videos uid={uid}: {e}")
    return []


async def fetch_cover_base64(url: str) -> Optional[str]:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                return base64.b64encode(await r.read()).decode()
    except Exception as e:
        nonebot.logger.warning(f"[bilibili_video] fetch_cover: {e}")
    return None


async def fetch_uname(uid: str) -> str:
    """获取 UP 主用户名"""
    try:
        async with aiohttp.ClientSession(
            headers=BASE_HEADERS, cookies=_make_cookies()
        ) as s:
            async with s.get(
                "https://api.bilibili.com/x/web-interface/card",
                params={"mid": uid, "photo": "false"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                data = await r.json(content_type=None)
                if data.get("code") == 0:
                    return data["data"]["card"].get("name", uid)
    except Exception:
        pass
    return uid


# ── 推送 ──────────────────────────────────────────────────────────────────────

async def notify_groups(group_ids: List[str], messages: list):
    try:
        bot: Bot = nonebot.get_bot()
    except Exception:
        nonebot.logger.warning("[bilibili_video] no bot available")
        return
    for gid in group_ids:
        for msg in messages:
            try:
                await bot.send_group_msg(group_id=int(gid), message=msg)
                await asyncio.sleep(0.5)
            except Exception as e:
                nonebot.logger.warning(f"[bilibili_video] send group {gid}: {e}")


async def notify_new_video(uid: str, video: dict):
    uname   = await fetch_uname(uid)
    title   = video["title"]
    desc    = video["desc"].strip()
    url     = video["url"]
    play    = video["play"]
    comment = video["comment"]
    duration = video["duration"]
    pubdate = datetime.fromtimestamp(video["pubdate"]).strftime("%Y-%m-%d %H:%M")

    # 播放量格式化
    play_str = f"{play / 10000:.1f}万" if play >= 10000 else str(play)

    # 简介截断
    desc_str = (desc[:60] + "……") if len(desc) > 60 else desc
    desc_line = f"📝 {desc_str}\n" if desc_str else ""

    text = (
        f"📹 {uname} 发布了新视频！\n"
        f"🎬 {title}\n"
        f"{desc_line}"
        f"⏱️ 时长：{duration}　👁️ 播放：{play_str}　💬 评论：{comment}\n"
        f"🕐 发布：{pubdate}\n"
        f"🔗 {url}"
    )

    msgs = []
    if video["pic"]:
        cover_b64 = await fetch_cover_base64(video["pic"])
        if cover_b64:
            msgs.append(MessageSegment.image(f"base64://{cover_b64}"))
    msgs.append(MessageSegment.text(text))

    group_ids = UIDS.get(uid, [])
    await notify_groups(group_ids, msgs)
    nonebot.logger.info(f"[bilibili_video] uid={uid} ({uname}) 新视频 {video['bvid']} 已通知群 {group_ids}")


# ── 定时轮询 ──────────────────────────────────────────────────────────────────

@scheduler.scheduled_job("interval", seconds=INTERVAL, id="bilibili_video_poll")
async def poll_new_videos():
    if not UIDS:
        return

    for uid in list(UIDS.keys()):
        videos = await fetch_latest_videos(uid, ps=5)
        if not videos:
            await asyncio.sleep(2)
            continue

        newest_bvid = videos[0]["bvid"]

        if uid not in initialized:
            # 首次运行：静默记录当前最新，不推送
            latest_bvid[uid] = newest_bvid
            initialized[uid] = True
            nonebot.logger.info(f"[bilibili_video] uid={uid} 初始化，最新视频={newest_bvid}")
            await asyncio.sleep(2)
            continue

        known_bvid = latest_bvid.get(uid, "")
        if newest_bvid == known_bvid:
            await asyncio.sleep(2)
            continue

        # 找出所有比 known_bvid 更新的视频（按 pubdate 倒序，取出新的部分）
        new_videos = []
        for v in videos:
            if v["bvid"] == known_bvid:
                break
            new_videos.append(v)

        # 从旧到新推送，避免顺序颠倒
        for v in reversed(new_videos):
            await notify_new_video(uid, v)
            await asyncio.sleep(1)

        latest_bvid[uid] = newest_bvid
        await asyncio.sleep(2)


nonebot.logger.info(
    f"[bilibili_video] 插件已加载，监控 {len(UIDS)} 个 uid，轮询间隔 {INTERVAL}s"
)
