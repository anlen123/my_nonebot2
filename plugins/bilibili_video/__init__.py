"""bilibili_video —— B 站新视频通知插件

功能：
  - 定时轮询配置中每个 uid 的最新投稿
  - 发现新视频时向绑定群推送通知（封面、标题、简介、时长、播放量、链接）
  - 开启后第一次轮询只静默记录最新视频，不会把历史投稿当新视频推送

配置（.env.dev / .env.prod）：
  BILIBILI_VIDEO_UIDS={"uid": [{"groupId": "群号", "isAtAll": true}]}
  BILIBILI_VIDEO_INTERVAL=300       # 轮询间隔（秒），默认 300
  BILIBILI_SESSDATA=你的SESSDATA    # B站登录Cookie，用于绕过风控
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import time
import urllib.parse
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import nonebot
from nonebot import require
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment

from .config import load_config

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

# ── 配置 ──────────────────────────────────────────────────────────────────────
_cfg = load_config()
UIDS: Dict[str, List[Dict[str, Any]]] = _cfg["bilibili_video_uids"]
INTERVAL: int = _cfg["bilibili_video_interval"]
SESSDATA: str = _cfg["bilibili_sessdata"]

# ── 运行时状态：记录每个 uid 已知的最新视频 bvid ──────────────────────────────
# 首次启动时先静默加载一次，避免把历史视频当新视频推送
latest_bvid: Dict[str, str] = {}
initialized: Dict[str, bool] = {}

# ── 接口与请求头 ──────────────────────────────────────────────────────────────
NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
SPACE_SEARCH_URL = "https://api.bilibili.com/x/space/wbi/arc/search"
USER_CARD_URL = "https://api.bilibili.com/x/web-interface/card"
VIDEO_URL = "https://www.bilibili.com/video/{bvid}"

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://space.bilibili.com",
}

_FETCH_ATTEMPTS = 2      # 单次抓取最多请求次数（第二次换新签名）
_RETRY_DELAY = 2         # 遭遇 -352 风控后等待秒数
_STAGGER_DELAY = 0.3     # 多 uid 轮询的错峰间隔（秒）
_NOTIFY_INTERVAL = 1     # 同一 UP 多个新视频之间的推送间隔（秒）
_PAGE_SIZE = 20          # 每次抓取条数：留足缓冲，一轮失败下轮还能补上


def _make_cookies() -> Dict[str, str]:
    """B 站登录 Cookie；未配置 SESSDATA 时匿名访问。"""
    return {"SESSDATA": SESSDATA} if SESSDATA else {}


def _new_session() -> aiohttp.ClientSession:
    """构造带 UA / Referer 与登录 Cookie 的 B 站 API 会话。"""
    return aiohttp.ClientSession(headers=BASE_HEADERS, cookies=_make_cookies())


# ── WBI 签名 ────────────────────────────────────────────────────────────────────
# B 站 API 风控升级，所有空间类接口需携带 w_rid / wts 签名参数。
# img_key + sub_key 来自 https://api.bilibili.com/x/web-interface/nav，
# 混合后取固定索引位生成签名密钥，对排序后的参数做 MD5。

_wbi_keys: Optional[Tuple[str, str, float]] = None  # (img_key, sub_key, fetched_at)
_wbi_lock = asyncio.Lock()                          # 防止并发 fetch 时重复调 nav
_WBI_KEY_REFRESH = 3600                             # 密钥缓存 1 小时

# B 站固定混淆索引
_WBI_MIXIN_INDEX = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
)


def _wbi_key_from_url(url: str) -> str:
    """从 nav 返回的图片地址 .../<key>.png 里取出 key。"""
    return url.rsplit("/", 1)[-1].split(".")[0] if url else ""


def _cached_wbi_keys() -> Optional[Tuple[str, str]]:
    """密钥缓存仍新鲜时返回 (img_key, sub_key)，否则返回 None。"""
    if _wbi_keys and (time.time() - _wbi_keys[2]) < _WBI_KEY_REFRESH:
        return _wbi_keys[0], _wbi_keys[1]
    return None


async def _fetch_wbi_keys() -> Tuple[str, str]:
    """获取并缓存 WBI 签名所需的 img_key / sub_key（带锁防并发）。"""
    global _wbi_keys
    cached = _cached_wbi_keys()
    if cached:
        return cached

    async with _wbi_lock:
        # 二级检查：等锁期间可能有其他协程已经刷新过
        cached = _cached_wbi_keys()
        if cached:
            return cached

        try:
            async with _new_session() as session:
                async with session.get(
                    NAV_URL,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json(content_type=None)
                    wbi_img = (data.get("data") or {}).get("wbi_img") or {}
                    img_key = _wbi_key_from_url(wbi_img.get("img_url", ""))
                    sub_key = _wbi_key_from_url(wbi_img.get("sub_url", ""))
                    if img_key and sub_key:
                        _wbi_keys = (img_key, sub_key, time.time())
                        nonebot.logger.debug("[bilibili_video] WBI keys refreshed")
                    return img_key, sub_key
        except Exception as exc:  # 拿不到新密钥时退回旧密钥，仍不行就返回空让上层跳过
            nonebot.logger.warning(f"[bilibili_video] fetch WBI keys failed: {exc}")
            if _wbi_keys:
                return _wbi_keys[0], _wbi_keys[1]
        return "", ""


def _wbi_sign_params(params: Dict[str, Any], img_key: str, sub_key: str) -> Dict[str, Any]:
    """就地补上 wts / w_rid 并返回同一个字典（签名覆盖含 wts 的全部参数）。"""
    mixin = img_key + sub_key
    key = "".join(mixin[index] for index in _WBI_MIXIN_INDEX)
    params["wts"] = int(time.time())
    sorted_pairs = sorted(params.items(), key=lambda item: item[0])
    query = urllib.parse.urlencode(sorted_pairs)
    params["w_rid"] = hashlib.md5((query + key).encode()).hexdigest()
    return params


# ── 数据解析 ──────────────────────────────────────────────────────────────────

def _parse_duration(length: str) -> int:
    """把接口给的 "MM:SS" / "HH:MM:SS" 时长换算成秒，解析不了算 0。"""
    try:
        parts = [int(part) for part in length.split(":")]
    except (ValueError, TypeError):
        return 0
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return 0


def _parse_video(raw: Dict[str, Any]) -> Dict[str, Any]:
    """把接口返回的一条投稿转成插件内部统一的结构。"""
    bvid = raw.get("bvid", "")
    return {
        "bvid": bvid,
        "title": raw.get("title", ""),
        "desc": raw.get("description", ""),
        "pic": raw.get("pic", ""),
        "duration": _parse_duration(raw.get("length", "0:00")),
        "play": raw.get("play", 0),
        "comment": raw.get("comment", 0),
        "pubdate": raw.get("created", 0),
        "url": VIDEO_URL.format(bvid=bvid),
        "is_live_playback": bool(raw.get("is_live_playback")),
    }


def _format_duration(seconds: Any) -> str:
    """把秒数格式化成 MM:SS / H:MM:SS（直播回放可能超过 1 小时）。"""
    if not isinstance(seconds, int) or seconds <= 0:
        return str(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


def _collect_new_videos(videos: List[Dict[str, Any]], known_bvid: str) -> List[Dict[str, Any]]:
    """取列表中排在 known_bvid 之前的视频（接口按发布时间倒序返回）。"""
    new_videos: List[Dict[str, Any]] = []
    for video in videos:
        if video["bvid"] == known_bvid:
            break
        new_videos.append(video)
    return new_videos


# ── B 站接口 ──────────────────────────────────────────────────────────────────

async def _fetch_videos_once(uid: str, ps: int) -> Optional[List[Dict[str, Any]]]:
    """请求一次空间投稿接口。

    返回 None 表示需要换新签名重试（-352 风控校验失败或暂无可用密钥），
    返回空列表表示接口明确报错、重试也没用。
    """
    img_key, sub_key = await _fetch_wbi_keys()
    if not img_key or not sub_key:
        nonebot.logger.warning("[bilibili_video] no WBI keys, cannot sign request")
        return None

    params = _wbi_sign_params(
        {"mid": uid, "ps": str(ps), "pn": "1", "order": "pubdate"},
        img_key, sub_key,
    )

    async with _new_session() as session:
        async with session.get(
            SPACE_SEARCH_URL,
            params=params,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            data = await resp.json(content_type=None)
            code = data.get("code")
            if code != 0:
                # -352 = 风控校验失败，可重试；其余不重试
                if code == -352:
                    return None
                nonebot.logger.warning(
                    f"[bilibili_video] fetch uid={uid} code={code} "
                    f"msg={data.get('message', '')}"
                )
                return []

            vlist = (data.get("data") or {}).get("list", {}).get("vlist") or []
            return [_parse_video(video) for video in vlist]


async def fetch_latest_videos(uid: str, ps: int = _PAGE_SIZE) -> List[Dict[str, Any]]:
    """
    通过 space/wbi/arc/search 接口获取 uid 最新投稿视频。

    B 站 2024–2025 风控升级：
      - api.vc.bilibili.com / polymer API 全线返回 412（WAF 拦截）
      - x/space/arc/search 同样被 412
      - 只有 x/space/wbi/arc/search + WBI 签名可正常访问

    参数 w_rid / wts 通过 nav 接口获取的 img_key + sub_key 动态计算。

    返回 [{bvid, title, desc, pic, duration, play, comment, pubdate, url}, ...]
    """
    # 一次请求 + 一次重试（仅 -352 风控场景）
    for attempt in range(_FETCH_ATTEMPTS):
        try:
            videos = await _fetch_videos_once(uid, ps)
        except Exception as exc:
            nonebot.logger.warning(f"[bilibili_video] fetch_latest_videos uid={uid}: {exc}")
            return []
        if videos is not None:
            return videos
        if attempt + 1 < _FETCH_ATTEMPTS:
            nonebot.logger.debug(
                f"[bilibili_video] uid={uid} -352, retrying with fresh signature..."
            )
            await asyncio.sleep(_RETRY_DELAY)  # 等风控窗口过去
        else:
            nonebot.logger.warning(f"[bilibili_video] uid={uid} -352 retry also failed")
    return []


async def fetch_cover_base64(url: str) -> Optional[str]:
    """下载封面并转成 base64；失败返回 None（通知退回纯文字）。"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                return base64.b64encode(await resp.read()).decode()
    except Exception as exc:
        nonebot.logger.warning(f"[bilibili_video] fetch_cover: {exc}")
    return None


async def fetch_uname(uid: str) -> str:
    """获取 UP 主用户名；接口失败时用 uid 顶替。"""
    try:
        async with _new_session() as session:
            async with session.get(
                USER_CARD_URL,
                params={"mid": uid, "photo": "false"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json(content_type=None)
                if data.get("code") == 0:
                    return data["data"]["card"].get("name", uid)
    except Exception as exc:
        nonebot.logger.warning(f"[bilibili_video] fetch_uname uid={uid}: {exc}")
    return uid


# ── 推送 ──────────────────────────────────────────────────────────────────────

def _build_notify_message(messages: List[MessageSegment], with_at: bool) -> Message:
    """把各个消息段拼成一条消息，with_at 为真时在开头插入 @全体成员。"""
    combined = Message()
    if with_at:
        combined += MessageSegment(type="at", data={"qq": "all"})
        combined += MessageSegment.text("\n")
    for segment in messages:
        combined += segment
    return combined


async def notify_groups(groups: List[Dict[str, Any]], messages: List[MessageSegment]) -> None:
    """
    groups: [{"groupId": "xxx", "isAtAll": bool}, ...]
    将 messages 拼成一条消息发送，isAtAll=True 时在消息头部插入 @全体成员
    """
    try:
        bot: Bot = nonebot.get_bot()
    except Exception as exc:  # 没有已连接的 bot 时放弃这次推送
        nonebot.logger.warning(f"[bilibili_video] no bot available: {exc}")
        return
    for group in groups:
        group_id = group["groupId"]
        is_at_all = group.get("isAtAll", False)

        try:
            await bot.send_group_msg(
                group_id=int(group_id),
                message=_build_notify_message(messages, is_at_all),
            )
            await asyncio.sleep(0.5)
        except Exception as exc:
            if is_at_all:
                # @全体失败多半是机器人没有权限，去掉 @全体重试，保证消息本身能送达
                nonebot.logger.warning(
                    f"[bilibili_video] send group {group_id} with @all failed ({exc})，降级重试"
                )
                try:
                    await bot.send_group_msg(
                        group_id=int(group_id),
                        message=_build_notify_message(messages, False),
                    )
                    await asyncio.sleep(0.5)
                except Exception as retry_exc:
                    nonebot.logger.warning(
                        f"[bilibili_video] send group {group_id} retry failed: {retry_exc}"
                    )
            else:
                nonebot.logger.warning(f"[bilibili_video] send group {group_id}: {exc}")


async def notify_new_video(uid: str, video: Dict[str, Any]) -> None:
    """组织一条新视频通知（封面图 + 文字）并发给该 uid 绑定的所有群。"""
    uname = await fetch_uname(uid)
    title = video["title"]
    desc = video["desc"].strip()
    url = video["url"]
    play = video["play"]
    comment = video["comment"]
    is_replay = video.get("is_live_playback", False)

    duration = _format_duration(video["duration"])
    pubdate = datetime.fromtimestamp(video["pubdate"]).strftime("%Y-%m-%d %H:%M")

    # 播放量格式化
    play_str = f"{play / 10000:.1f}万" if play >= 10000 else str(play)

    # 简介截断
    desc_str = (desc[:60] + "……") if len(desc) > 60 else desc
    desc_line = f"📝 {desc_str}\n" if desc_str else ""

    label = "📺 直播回放" if is_replay else "📹 新视频"

    text = (
        f"{label} · {uname}\n"
        f"🎬 {title}\n"
        f"{desc_line}"
        f"⏱️ 时长：{duration}　👁️ 播放：{play_str}　💬 评论：{comment}\n"
        f"🕐 发布：{pubdate}\n"
        f"🔗 {url}"
    )

    msgs: List[MessageSegment] = []
    if video["pic"]:
        cover_b64 = await fetch_cover_base64(video["pic"])
        if cover_b64:
            msgs.append(MessageSegment.image(f"base64://{cover_b64}"))
    msgs.append(MessageSegment.text(text))

    group_ids = UIDS.get(uid, [])
    await notify_groups(group_ids, msgs)
    nonebot.logger.info(
        f"[bilibili_video] uid={uid} ({uname}) 新视频 {video['bvid']} 已通知群 {group_ids}"
    )


# ── 定时轮询 ──────────────────────────────────────────────────────────────────

async def _fetch_staggered(uid: str, delay: float) -> List[Dict[str, Any]]:
    """延迟 delay 秒后再抓取：多 uid 请求错开，避免同一秒 wts 相同被风控。"""
    await asyncio.sleep(delay)
    return await fetch_latest_videos(uid, ps=_PAGE_SIZE)


@scheduler.scheduled_job("interval", seconds=INTERVAL, id="bilibili_video_poll")
async def poll_new_videos() -> None:
    if not UIDS:
        return

    uid_list = list(UIDS.keys())
    nonebot.logger.info(f"[bilibili_video] 开始轮询，共 {len(uid_list)} 个 uid")

    # return_exceptions=True 时 gather 的结果可能是异常对象，故按 Any 逐项判类型
    results: List[Any] = await asyncio.gather(
        *[_fetch_staggered(uid, index * _STAGGER_DELAY) for index, uid in enumerate(uid_list)],
        return_exceptions=True,
    )

    for uid, videos in zip(uid_list, results):
        if isinstance(videos, Exception):
            nonebot.logger.warning(f"[bilibili_video] uid={uid} 接口异常: {videos}")
            continue
        if not videos:
            nonebot.logger.warning(f"[bilibili_video] uid={uid} 获取视频失败（风控/网络），跳过")
            continue

        nonebot.logger.info(
            f"[bilibili_video] uid={uid} 获取到 {len(videos)} 条视频，"
            f"最新={videos[0]['bvid']} 《{videos[0]['title'][:30]}》"
        )

        newest_bvid = videos[0]["bvid"]

        # 首次见到该 uid：只记下来，不推历史投稿
        if uid not in initialized:
            latest_bvid[uid] = newest_bvid
            initialized[uid] = True
            nonebot.logger.info(
                f"[bilibili_video] uid={uid} 初始化完成，"
                f"最新视频={newest_bvid} 《{videos[0]['title'][:30]}》"
            )
            continue

        known_bvid = latest_bvid.get(uid, "")
        if newest_bvid == known_bvid:
            nonebot.logger.debug(f"[bilibili_video] uid={uid} 无新视频（最新仍为 {known_bvid}）")
            continue

        new_videos = _collect_new_videos(videos, known_bvid)
        nonebot.logger.info(f"[bilibili_video] uid={uid} 发现 {len(new_videos)} 个新视频，准备推送")

        # 由上一条已知视频往新推，保证群里按发布时间先后出现
        for video in reversed(new_videos):
            nonebot.logger.info(
                f"[bilibili_video] 推送新视频 bvid={video['bvid']} "
                f"《{video['title'][:30]}》 -> 群 {UIDS.get(uid, [])}"
            )
            await notify_new_video(uid, video)
            await asyncio.sleep(_NOTIFY_INTERVAL)

        latest_bvid[uid] = newest_bvid

    nonebot.logger.info("[bilibili_video] 本轮轮询结束")


@scheduler.scheduled_job("date", id="bilibili_video_wbi_prefetch")
async def _prefetch_wbi_keys_on_startup() -> None:
    """启动时预取 WBI 密钥，避免首次轮询时并发竞争。"""
    if UIDS:
        img_key, sub_key = await _fetch_wbi_keys()
        if img_key and sub_key:
            nonebot.logger.info("[bilibili_video] WBI 密钥预取成功")
        else:
            nonebot.logger.warning("[bilibili_video] WBI 密钥预取失败，将在首次轮询时重试")


nonebot.logger.info(
    f"[bilibili_video] 插件已加载，监控 {len(UIDS)} 个 uid，轮询间隔 {INTERVAL}s"
)
