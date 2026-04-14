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

async def fetch_latest_videos(uid: str, ps: int = 20) -> List[dict]:
    """
    通过动态接口获取 uid 最新投稿视频（type=8），只需 SESSDATA Cookie。
    返回 [{bvid, title, desc, pic, duration, play, comment, pubdate, url}, ...]
    """
    import json as _json
    try:
        async with aiohttp.ClientSession(
            headers=BASE_HEADERS, cookies=_make_cookies()
        ) as s:
            async with s.get(
                "https://api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/space_history",
                params={"host_uid": uid, "offset_dynamic_id": 0, "need_top": 0, "platform": "web"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                data = await r.json(content_type=None)
                if data.get("code") != 0:
                    nonebot.logger.warning(
                        f"[bilibili_video] fetch uid={uid} code={data.get('code')} msg={data.get('message','')}"
                    )
                    return []
                cards = (data.get("data") or {}).get("cards") or []
                result = []
                for c in cards:
                    desc = c.get("desc", {})
                    # type=8 是投稿视频
                    if desc.get("type") != 8:
                        continue
                    card = _json.loads(c.get("card", "{}"))
                    bvid = desc.get("bvid", "")
                    stat = desc.get("stat", {})
                    result.append({
                        "bvid":    bvid,
                        "title":   card.get("title", ""),
                        "desc":    card.get("desc", ""),
                        "pic":     card.get("pic", ""),
                        "duration": card.get("duration", 0),   # 秒数
                        "play":    stat.get("view", 0),
                        "comment": stat.get("reply", 0),
                        "pubdate": desc.get("timestamp", 0),
                        "url":     f"https://www.bilibili.com/video/{bvid}",
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

async def notify_groups(groups: list, messages: list):
    """
    groups: [{"groupId": "xxx", "isAtAll": bool}, ...]
    将 messages 拼成一条消息发送，isAtAll=True 时在消息头部插入 @全体成员
    """
    from nonebot.adapters.onebot.v11 import Message
    try:
        bot: Bot = nonebot.get_bot()
    except Exception:
        nonebot.logger.warning("[bilibili_video] no bot available")
        return
    for g in groups:
        gid       = g["groupId"]
        is_at_all = g.get("isAtAll", False)

        def _build(with_at: bool) -> Message:
            combined = Message()
            if with_at:
                combined += MessageSegment(type="at", data={"qq": "all"})
                combined += MessageSegment.text("\n")
            for seg in messages:
                combined += seg
            return combined

        try:
            await bot.send_group_msg(group_id=int(gid), message=_build(is_at_all))
            await asyncio.sleep(0.5)
        except Exception as e:
            if is_at_all:
                nonebot.logger.warning(f"[bilibili_video] send group {gid} with @all failed ({e})，降级重试")
                try:
                    await bot.send_group_msg(group_id=int(gid), message=_build(False))
                    await asyncio.sleep(0.5)
                except Exception as e2:
                    nonebot.logger.warning(f"[bilibili_video] send group {gid} retry failed: {e2}")
            else:
                nonebot.logger.warning(f"[bilibili_video] send group {gid}: {e}")


async def notify_new_video(uid: str, video: dict):
    uname   = await fetch_uname(uid)
    title   = video["title"]
    desc    = video["desc"].strip()
    url     = video["url"]
    play    = video["play"]
    comment = video["comment"]
    dur_sec  = video["duration"]
    duration = f"{dur_sec // 60}:{dur_sec % 60:02d}" if isinstance(dur_sec, int) else str(dur_sec)
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

    nonebot.logger.debug(f"[bilibili_video] 开始轮询，共 {len(UIDS)} 个 uid")

    for uid in list(UIDS.keys()):
        nonebot.logger.debug(f"[bilibili_video] 正在请求 uid={uid} 的动态列表...")
        videos = await fetch_latest_videos(uid, ps=5)

        if not videos:
            nonebot.logger.warning(f"[bilibili_video] uid={uid} 未获取到视频，跳过")
            await asyncio.sleep(2)
            continue

        nonebot.logger.debug(f"[bilibili_video] uid={uid} 获取到 {len(videos)} 条视频，最新={videos[0]['bvid']} 《{videos[0]['title'][:20]}》")

        newest_bvid = videos[0]["bvid"]

        if uid not in initialized:
            latest_bvid[uid] = newest_bvid
            initialized[uid] = True
            nonebot.logger.info(f"[bilibili_video] uid={uid} 初始化完成，最新视频={newest_bvid} 《{videos[0]['title'][:30]}》")
            await asyncio.sleep(2)
            continue

        known_bvid = latest_bvid.get(uid, "")
        if newest_bvid == known_bvid:
            nonebot.logger.debug(f"[bilibili_video] uid={uid} 无新视频（最新仍为 {known_bvid}）")
            await asyncio.sleep(2)
            continue

        # 找出所有比 known_bvid 更新的视频
        new_videos = []
        for v in videos:
            if v["bvid"] == known_bvid:
                break
            new_videos.append(v)

        nonebot.logger.info(f"[bilibili_video] uid={uid} 发现 {len(new_videos)} 个新视频，准备推送")

        for v in reversed(new_videos):
            nonebot.logger.info(f"[bilibili_video] 推送新视频 bvid={v['bvid']} 《{v['title'][:30]}》 -> 群 {UIDS.get(uid, [])}")
            await notify_new_video(uid, v)
            await asyncio.sleep(1)

        latest_bvid[uid] = newest_bvid
        await asyncio.sleep(2)

    nonebot.logger.debug(f"[bilibili_video] 本轮轮询结束")


nonebot.logger.info(
    f"[bilibili_video] 插件已加载，监控 {len(UIDS)} 个 uid，轮询间隔 {INTERVAL}s"
)
