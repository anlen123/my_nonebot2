"""bilibili_live —— B 站直播监控插件

功能：
  * 开播通知：一张可爱卡片图（封面 + UP 主 + 标题 + 在线人数 + 直播间链接，可按群配置 @全体）；
  * 下播通知：UP 主、时长、粉丝数变化、弹幕排行榜与词云图；
  * 直播中每小时播报：已播时长、当前在线人数、直播间链接；
  * 群聊「开播」关键词：先回一条 td，再把本群配置里正在直播的 UP 主逐个按开播通知发出来。

配置（.env.dev / .env.prod）：
  BILIBILI_LIVE_UIDS={"uid": [{"groupId": "群号", "isAtAll": true}]}   # 也兼容 ["群号", ...] 旧写法
  BILIBILI_LIVE_INTERVAL=60    # 状态轮询间隔（秒）

说明：
  * 插件没有命令，加载后由 apscheduler 每隔 INTERVAL 秒轮询一次；
  * 首次轮询只静默记录状态，避免重启时给所有正在直播的房间补推开播通知。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, TypedDict

import aiohttp
import nonebot
from nonebot import require
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.plugin import on_message
from nonebot.rule import Rule

from . import direct_net
from .config import load_config
from .live_card import build_hourly_card, build_live_card
from .online_chart import build_online_chart
from .wordcloud_generator import build_wordcloud_from_texts

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

# ── 配置 ──────────────────────────────────────────────────────────────────────
_cfg = load_config()
UIDS: dict[str, list[dict[str, Any]]] = _cfg["bilibili_live_uids"]
INTERVAL: int = _cfg["bilibili_live_interval"]
# 弹幕历史接口在「出口在境外」的情况下，匿名请求恒返回 0 条，带上登录态才会给内容
SESSDATA: str = _cfg.get("bilibili_live_sessdata", "")

# ── B 站接口 ──────────────────────────────────────────────────────────────────
ROOM_INFO_URL = "https://api.live.bilibili.com/room/v1/Room/getRoomInfoOld"
DANMAKU_URL = "https://api.live.bilibili.com/xlive/web-room/v1/dM/gethistory"
USER_CARD_URL = "https://api.bilibili.com/x/web-interface/card"

REQUEST_TIMEOUT = 10    # 单次接口请求超时（秒）
COVER_TIMEOUT = 15      # 封面图下载超时（秒）
SEND_GAP = 0.5          # 同一条通知发给多个群之间的间隔（秒），避免撞上风控
HOURLY_REPORT_SECONDS = 3600   # 直播中每小时播报的间隔（秒）

# 下播后在多少秒内重新开播算「同一场」：时长与弹幕统计续接，不从头再来（默认 30 分钟）
RESUME_WINDOW_SECONDS = int(os.environ.get("BILIBILI_LIVE_RESUME_WINDOW", "1800"))
# 连续几轮没在播才认定真下播：接口抖动一次就切场的话，时长和榜单会被切碎（默认 2 轮）
OFFLINE_CONFIRM_POLLS = int(os.environ.get("BILIBILI_LIVE_OFFLINE_CONFIRM", "2"))
# 在线人数采样点上限，超长直播（十几小时）也只会占这点内存
MAX_ONLINE_SAMPLES = 2000

# ── 群聊「开播」关键词 ────────────────────────────────────────────────────────
OPENLIVE_KEYWORD = "开播"       # 群聊消息里出现这两个字就触发
OPENLIVE_REPLY = "td"          # 触发后先回的一条纯文本（小写）
OPENLIVE_PRIORITY = 5          # 越小越先拿到消息；不阻断后续匹配器
OPENLIVE_NOTIFY_WHEN_EMPTY = False   # 本群没配置 / 没有在播的 UP 主时，是否也回一句
OPENLIVE_RETRIES = 3                 # 手动查询的重试次数（B 站接口偶发握手失败，手动查询只打一次）

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://live.bilibili.com",
}

# 取弹幕要带登录态：本机出口在境外，匿名请求弹幕历史恒为 0 条，带上 SESSDATA 才有内容
DANMAKU_HEADERS = dict(HEADERS)
if SESSDATA:
    DANMAKU_HEADERS["Cookie"] = f"SESSDATA={SESSDATA}"

# ── 运行时状态 ────────────────────────────────────────────────────────────────
# 上一轮轮询到的开播状态，用来识别开播/下播跳变
live_status: dict[str, bool] = {uid: False for uid in UIDS}

# 已做过首次识别的 uid：初始化时若已经在播，只静默建会话，不补推开播通知
initialized: set[str] = set()


class LiveSession(TypedDict):
    """一位 UP 主本轮直播期间累积的会话状态。"""

    start_time: datetime
    room_id: int | str              # 接口可能给 int 也可能给 str，拼链接前统一转 int
    uname: str
    fans_start: int
    danmaku_counter: Counter[str]   # 用户名 -> 弹幕条数
    seen_danmaku: set[str]          # "用户名:弹幕原文" 去重键
    danmaku_texts: list[str]        # 弹幕原文，供词云使用
    last_hourly_notify: datetime
    online_samples: list[tuple[str, int]]   # [(HH:MM, 在线人数), ...]，下播折线图用
    resumed: bool                   # 是否是续接上一场（只影响日志措辞）


live_session: dict[str, LiveSession] = {}

# 上一场结束后的快照：短时间内重新开播要续接时长与弹幕统计，见 _open_session
last_live_session: dict[str, dict[str, Any]] = {}

# 「连续几轮没在播」的计数，用于把瞬时抖动和真下播区分开
offline_streak: dict[str, int] = {}


# ── 状态落盘 ──────────────────────────────────────────────────────────────────
# 机器人重启（或崩溃）时内存里的会话就没了；把在场次状态写到 data/ 下，
# 重启回来后接着算时长与弹幕统计。data/ 已被 .gitignore 覆盖，不会入库。
STATE_FILE = Path("data") / "bilibili_live_state.json"
STATE_VERSION = 1
DANMAKU_TEXT_KEEP = 2000     # 落盘时最多保留多少条弹幕原文（供词云）
SEEN_DANMAKU_KEEP = 5000     # 落盘时最多保留多少条去重键
COUNTER_KEEP = 300           # 落盘时最多保留多少个发言用户（按条数取前列）
STATE_SAVE_MIN_GAP = 20.0    # 两次落盘的最小间隔（秒），避免高频写盘

_last_state_save: float = 0.0


def _state_path() -> Path:
    """状态文件路径；以仓库根目录为基准（插件运行时的当前目录就是仓库根）。"""
    return Path.cwd() / STATE_FILE


def _session_to_state(session: Any, live: bool, ended_at: Optional[datetime]) -> dict[str, Any]:
    """把一个会话转成可 JSON 化的结构。"""
    counter = session.get("danmaku_counter") or Counter()
    top_counter = dict(counter.most_common(COUNTER_KEEP))
    return {
        "live": live,
        "start_time": session["start_time"].isoformat(),
        "room_id": session.get("room_id", ""),
        "uname": session.get("uname", ""),
        "fans_start": int(session.get("fans_start") or 0),
        "danmaku_counter": top_counter,
        "danmaku_texts": list(session.get("danmaku_texts") or [])[-DANMAKU_TEXT_KEEP:],
        "seen_danmaku": sorted(session.get("seen_danmaku") or set())[-SEEN_DANMAKU_KEEP:],
        "online_samples": [list(item) for item in (session.get("online_samples") or [])][-MAX_ONLINE_SAMPLES:],
        "ended_at": ended_at.isoformat() if ended_at else None,
    }


def _state_to_session(raw: dict[str, Any], ended_at: datetime) -> dict[str, Any]:
    """把落盘的结构还原成内存中的会话（含续接所需的 ended_at）。"""
    counter = Counter(
        {str(name): int(count) for name, count in (raw.get("danmaku_counter") or {}).items()}
    )
    samples = [
        (str(label), int(value))
        for label, value in (raw.get("online_samples") or [])
        if isinstance(label, str)
    ]
    return {
        "start_time": datetime.fromisoformat(raw["start_time"]),
        "room_id": raw.get("room_id", ""),
        "uname": raw.get("uname", ""),
        "fans_start": int(raw.get("fans_start") or 0),
        "danmaku_counter": counter,
        "seen_danmaku": set(raw.get("seen_danmaku") or []),
        "danmaku_texts": [str(text) for text in (raw.get("danmaku_texts") or [])],
        "online_samples": samples,
        "last_hourly_notify": datetime.now(),
        "resumed": False,
        "ended_at": ended_at,
    }


def _save_state(force: bool = False) -> None:
    """把当前的直播中会话与下播快照写到磁盘；任何异常都只记日志，不影响直播监控。"""
    global _last_state_save
    now = time.monotonic()
    if not force and now - _last_state_save < STATE_SAVE_MIN_GAP:
        return
    _last_state_save = now
    try:
        sessions: dict[str, Any] = {}
        for uid, session in live_session.items():
            sessions[uid] = _session_to_state(session, live=True, ended_at=None)
        for uid, snapshot in last_live_session.items():
            if uid in sessions:
                continue
            sessions[uid] = _session_to_state(
                snapshot, live=False, ended_at=snapshot.get("ended_at")
            )
        payload = {"version": STATE_VERSION, "saved_at": datetime.now().isoformat(), "sessions": sessions}
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 — 落盘失败不该影响直播监控本身
        nonebot.logger.warning(f"[bilibili_live] 状态落盘失败：{type(exc).__name__} {exc}")


def _load_state() -> None:
    """启动时读回落盘状态，让重启前后算同一场直播（时长与弹幕统计接着累加）。"""
    path = _state_path()
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        saved_at = datetime.fromisoformat(payload.get("saved_at")) if payload.get("saved_at") else datetime.now()
        restored_live = restored_ended = dropped = 0
        for uid, raw in (payload.get("sessions") or {}).items():
            if not isinstance(raw, dict) or not raw.get("start_time"):
                continue
            try:
                if raw.get("live"):
                    # 停机前还在直播：以落盘时间为「上次结束时间」，重启后照常续接
                    last_live_session[uid] = _state_to_session(raw, saved_at)
                    restored_live += 1
                else:
                    ended_at = datetime.fromisoformat(raw["ended_at"]) if raw.get("ended_at") else saved_at
                    if (datetime.now() - ended_at).total_seconds() > RESUME_WINDOW_SECONDS:
                        dropped += 1
                        continue
                    last_live_session[uid] = _state_to_session(raw, ended_at)
                    restored_ended += 1
            except (KeyError, ValueError, TypeError) as exc:
                nonebot.logger.warning(f"[bilibili_live] 状态里 uid={uid} 的记录读不动，跳过：{exc}")
        if restored_live or restored_ended or dropped:
            nonebot.logger.info(
                f"[bilibili_live] 已恢复落盘状态：在场次 {restored_live} 个、"
                f"可续接的旧场次 {restored_ended} 个、过期丢弃 {dropped} 个"
            )
    except Exception as exc:  # noqa: BLE001
        nonebot.logger.warning(f"[bilibili_live] 读回落盘状态失败：{type(exc).__name__} {exc}")


_load_state()


# ── B 站接口 ──────────────────────────────────────────────────────────────────
async def _fetch_data(
    url: str,
    params: dict[str, Any],
    label: str,
    headers: Optional[dict[str, str]] = None,
) -> Any:
    """GET 一个 B 站接口，返回响应里的 data 字段；失败只记日志并返回 None。

    label 用于日志定位（带上 uid / room_id）；接口返回 code != 0 也算失败。
    走 direct_net 的直连会话并自带重试 —— 这台机器到 B 站的握手时好时坏，
    单次失败不代表对方挂了。headers 不给就用默认请求头。
    """
    try:
        payload = await direct_net.fetch_json(url, params, headers or HEADERS, REQUEST_TIMEOUT)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        nonebot.logger.warning(f"[bilibili_live] {label} 请求失败（已重试）：{exc}")
        return None
    if not isinstance(payload, dict) or payload.get("code") != 0:
        nonebot.logger.warning(f"[bilibili_live] {label} 返回异常：{str(payload)[:120]}")
        return None
    return payload.get("data")


async def fetch_room_info(uid: str) -> Optional[dict[str, Any]]:
    """取直播间状态：liveStatus=1 表示在播，附带 roomid / title / cover / online。"""
    data = await _fetch_data(ROOM_INFO_URL, {"mid": uid}, f"fetch_room_info uid={uid}")
    return data if isinstance(data, dict) else None


async def fetch_user_card(uid: str) -> tuple[str, int]:
    """取 (用户名, 粉丝数)；接口失败时退回 (uid, 0)。"""
    data = await _fetch_data(
        USER_CARD_URL, {"mid": uid, "photo": "false"}, f"fetch_user_card uid={uid}"
    )
    if not isinstance(data, dict):
        return uid, 0
    card = data.get("card")
    if not isinstance(card, dict):
        return uid, 0
    return card.get("name", uid), data.get("follower", 0)


async def fetch_danmaku_with_user(room_id: int) -> list[tuple[str, str]]:
    """取最近的弹幕历史，返回 [(用户名, 弹幕原文), ...]，只保留有内容的弹幕。

    必须带 SESSDATA：同一台机器、同一个房间，匿名请求固定 0 条，登录态才有数据。
    """
    data = await _fetch_data(
        DANMAKU_URL,
        {"roomid": room_id, "csrf_token": "", "csrf": "", "visit_id": ""},
        f"fetch_danmaku room={room_id}",
        DANMAKU_HEADERS,
    )
    if not isinstance(data, dict):
        return []
    return [
        (item.get("nickname", "").strip(), item.get("text", "").strip())
        for item in data.get("room") or []
        if isinstance(item, dict) and item.get("text", "").strip()
    ]


async def fetch_cover_base64(url: str) -> Optional[str]:
    """把封面图下载成 base64 —— QQ 不认 B 站的图片直链，只能自己下回来发。"""
    data = await direct_net.fetch_bytes(url, HEADERS, COVER_TIMEOUT)
    if data is None:
        nonebot.logger.warning("[bilibili_live] 下载封面失败（已重试）")
        return None
    return base64.b64encode(data).decode()


# ── 文案格式 ──────────────────────────────────────────────────────────────────
def fmt_duration(seconds: int) -> str:
    """秒数转时长文案：有小时就带小时，没小时就只报到分。"""
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}小时{minutes}分{secs}秒"
    if minutes:
        return f"{minutes}分{secs}秒"
    return f"{secs}秒"


def fmt_fans(count: int) -> str:
    """数量按「万」折算，粉丝数与在线人数共用。"""
    return f"{count / 10000:.1f}万" if count >= 10000 else str(count)


# ── 推送 ──────────────────────────────────────────────────────────────────────
def _compose(messages: list[MessageSegment], at_all: bool) -> Message:
    """把消息段拼成一条消息；at_all 时最前面加上 @全体。"""
    message = Message()
    if at_all:
        message += MessageSegment(type="at", data={"qq": "all"})
        message += MessageSegment.text("\n")
    for segment in messages:
        message += segment
    return message


async def notify_groups(
    groups: list[dict[str, Any]], messages: list[MessageSegment], at_all: bool = True
) -> None:
    """把消息推给配置里绑定的每个群。

    groups: [{"groupId": "群号", "isAtAll": bool}, ...]
    at_all: False 时强制不 @全体，忽略 groups 里的 isAtAll 配置
    """
    try:
        bot: Bot = nonebot.get_bot()
    except Exception as exc:  # noqa: BLE001 — 机器人没连上时推送只能放弃，别把轮询带崩
        nonebot.logger.warning(f"[bilibili_live] 没有可用的 bot，放弃本次推送：{exc}")
        return

    for group in groups:
        group_id = group["groupId"]
        with_at = at_all and group.get("isAtAll", False)
        try:
            await bot.send_group_msg(group_id=int(group_id), message=_compose(messages, with_at))
            await asyncio.sleep(SEND_GAP)
        except Exception as exc:  # noqa: BLE001 — 单个群发失败不影响其他群
            if not with_at:
                nonebot.logger.warning(f"[bilibili_live] 向群 {group_id} 推送失败：{exc}")
                continue
            # @全体 容易被风控拦下，去掉 @全体 再试一次
            nonebot.logger.warning(
                f"[bilibili_live] 向群 {group_id} 带 @全体推送失败（{exc}），降级重试"
            )
            try:
                await bot.send_group_msg(
                    group_id=int(group_id), message=_compose(messages, False)
                )
                await asyncio.sleep(SEND_GAP)
            except Exception as retry_exc:  # noqa: BLE001
                nonebot.logger.warning(f"[bilibili_live] 向群 {group_id} 重试仍失败：{retry_exc}")


# ── 会话 ──────────────────────────────────────────────────────────────────────
def _record_online(session: Any, online: Any) -> None:
    """记一个在线人数采样点，供下播折线图使用；同一分钟同一数值不重复记。"""
    try:
        value = int(online)
    except (TypeError, ValueError):
        return
    if value < 0:
        return
    samples: list[tuple[str, int]] = session.setdefault("online_samples", [])
    label = datetime.now().strftime("%H:%M")
    if samples and samples[-1] == (label, value):
        return
    samples.append((label, value))
    if len(samples) > MAX_ONLINE_SAMPLES:
        del samples[: len(samples) - MAX_ONLINE_SAMPLES]


def _open_session(
    uid: str,
    info: dict[str, Any],
    uname: str,
    fans: int,
    *,
    allow_resume: bool = True,
) -> bool:
    """开播（或插件启动时已经在播）时建会话，供下播汇总与每小时播报使用。

    如果这个 uid 刚下播不久（RESUME_WINDOW_SECONDS 内）又重新开播 —— 主播下播喘口气
    又开、或接口抖了一下 —— 就续接上一场的时长与弹幕统计，不从头再来。
    返回 True 表示这次是续接。
    """
    now = datetime.now()
    previous = last_live_session.pop(uid, None) if allow_resume else None
    if previous:
        gap = (now - previous["ended_at"]).total_seconds()
        if gap <= RESUME_WINDOW_SECONDS:
            start_time = previous.get("start_time", now)
            live_session[uid] = {
                "start_time": start_time,
                "room_id": info.get("roomid") or previous.get("room_id", ""),
                "uname": uname,
                "fans_start": previous.get("fans_start", fans),
                "danmaku_counter": previous.get("danmaku_counter", Counter()),
                "seen_danmaku": previous.get("seen_danmaku", set()),
                "danmaku_texts": previous.get("danmaku_texts", []),
                "online_samples": previous.get("online_samples", []),
                "last_hourly_notify": now,
                "resumed": True,
            }
            _record_online(live_session[uid], info.get("online"))
            nonebot.logger.info(
                f"[bilibili_live] uid={uid} ({uname}) 距上次下播 {int(gap)}s，续接同一场："
                f"已播 {fmt_duration(int((now - start_time).total_seconds()))}，"
                f"已有弹幕 {sum(live_session[uid]['danmaku_counter'].values())} 条"
            )
            return True
        nonebot.logger.info(
            f"[bilibili_live] uid={uid} 距上次下播 {int(gap)}s"
            f"（超过 {RESUME_WINDOW_SECONDS}s），算作新的一场"
        )

    live_session[uid] = {
        "start_time": now,
        "room_id": info.get("roomid", ""),
        "uname": uname,
        "fans_start": fans,
        "danmaku_counter": Counter(),
        "seen_danmaku": set(),
        "danmaku_texts": [],
        "last_hourly_notify": now,
        "online_samples": [],
        "resumed": False,
    }
    _record_online(live_session[uid], info.get("online"))
    return False


# ── 开播 / 下播 ───────────────────────────────────────────────────────────────
async def build_live_notice(uid: str, info: dict[str, Any], uname: str) -> list[MessageSegment]:
    """开播通知的内容：一张可爱卡片图；渲染失败时退回「封面图 + 文字」。

    定时轮询的开播推送、群聊「开播」关键词的手动查询共用这一份，保证两处内容一致。
    """
    live_url = f"https://live.bilibili.com/{info.get('roomid', '')}"

    cover_url = info.get("cover", "")
    cover_b64 = await fetch_cover_base64(cover_url) if cover_url else None

    online = int(info.get("online") or 0)
    card_jpeg = None
    try:
        card_jpeg = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: build_live_card(
                uname=uname,
                title=str(info.get("title") or "未知标题"),
                room_id=info.get("roomid", ""),
                online_text=fmt_fans(online) if online else "",
                cover_bytes=base64.b64decode(cover_b64) if cover_b64 else None,
            ),
        )
    except Exception as exc:  # noqa: BLE001 — 出图失败不能影响通知本身
        nonebot.logger.warning(
            f"[bilibili_live] uid={uid} 开播卡片渲染异常（{type(exc).__name__} {exc}），退回文字版"
        )

    if card_jpeg:
        return [MessageSegment.image(f"base64://{base64.b64encode(card_jpeg).decode()}")]

    messages: list[MessageSegment] = []
    if cover_b64:
        messages.append(MessageSegment.image(f"base64://{cover_b64}"))
    messages.append(
        MessageSegment.text(
            f"🔴 {uname} 开播啦！\n"
            f"📺 {info.get('title', '未知标题')}\n"
            f"🔗 {live_url}"
        )
    )
    return messages


async def on_live_start(uid: str, info: dict[str, Any]) -> None:
    """开播通知：一张开播卡片（封面 + 名字 + 标题 + 在线人数 + 直播间链接）。"""
    uname, fans = await fetch_user_card(uid)
    _open_session(uid, info, uname, fans)

    messages = await build_live_notice(uid, info, uname)
    await notify_groups(UIDS.get(uid, []), messages)
    nonebot.logger.info(f"[bilibili_live] uid={uid} ({uname}) 开播")


async def on_live_end(uid: str, info: dict[str, Any]) -> None:
    """下播通知：时长、粉丝数变化、弹幕排行榜，末尾附词云图与在线人数折线图。"""
    session = live_session.pop(uid, {})
    if session:
        _record_online(session, info.get("online"))
        # 留一份快照：短时间内重新开播要续接时长与弹幕统计（见 _open_session）
        last_live_session[uid] = {**session, "ended_at": datetime.now()}
    uname = session.get("uname", uid)
    room_id = session.get("room_id") or info.get("roomid", 0)

    start_time = session.get("start_time")
    duration_str = fmt_duration(
        int((datetime.now() - start_time).total_seconds()) if start_time else 0
    )

    _, fans_now = await fetch_user_card(uid)
    fans_start = session.get("fans_start", fans_now)
    fans_delta = fans_now - fans_start
    fans_delta_str = f"+{fans_delta}" if fans_delta >= 0 else str(fans_delta)

    # 弹幕统计：直播中累积的，再补上下播前最后一轮拉到的
    counter = session.get("danmaku_counter", Counter())
    if room_id:
        seen = session.get("seen_danmaku", set())
        for user, text in await fetch_danmaku_with_user(int(room_id)):
            if user and f"{user}:{text}" not in seen:
                counter[user] += 1
    total_danmaku = sum(counter.values())
    total_users = len(counter)

    # 排行榜 Top3 + 特别嘉奖（第 4、5 名）
    top = counter.most_common(5)
    medals = ("🥇", "🥈", "🥉")
    rank_lines = [f"{medals[i]} {name} - {cnt} 条" for i, (name, cnt) in enumerate(top[:3])]
    special = [name for name, _ in top[3:5]]

    rank_text = "\n".join(rank_lines) if rank_lines else "暂无数据"
    special_text = f"\n🎖️ 特别嘉奖：{'  &  '.join(special)}" if special else ""
    end_text = (
        f"{uname} 下播啦，本次直播了 {duration_str}，粉丝数变化 {fans_delta_str}\n\n"
        f"🔍【弹幕情报站】本场直播数据如下：\n"
        f"🧍 总共 {total_users} 位观众上线\n"
        f"💬 共计 {total_danmaku} 条弹幕飞驰而过\n"
        f"👑 本场顶级输出选手：\n"
        f"{rank_text}"
        f"{special_text}\n\n"
        f"你们的弹幕，我们都记录在案！🕵️"
    )

    messages = [MessageSegment.text(end_text)]

    # 词云：直播中记录的弹幕原文，再补上下播前最后一轮拉到的
    danmaku_texts: list[str] = session.get("danmaku_texts", [])
    if room_id:
        danmaku_texts.extend(
            text for _, text in await fetch_danmaku_with_user(int(room_id)) if text
        )
    if danmaku_texts:
        wordcloud_png = await asyncio.get_event_loop().run_in_executor(
            None, build_wordcloud_from_texts, danmaku_texts
        )
        if wordcloud_png:
            messages.append(
                MessageSegment.image(f"base64://{base64.b64encode(wordcloud_png).decode()}")
            )

    # 在线人数折线图：直播中每个轮询周期采一个点，两点以上才画得成线
    samples = session.get("online_samples", [])
    if len(samples) >= 2:
        chart_jpeg = await asyncio.get_event_loop().run_in_executor(
            None, build_online_chart, samples, uname, duration_str
        )
        if chart_jpeg:
            messages.append(
                MessageSegment.image(f"base64://{base64.b64encode(chart_jpeg).decode()}")
            )
        else:
            nonebot.logger.warning(f"[bilibili_live] uid={uid} 在线人数折线图渲染失败，跳过")

    await notify_groups(UIDS.get(uid, []), messages, at_all=False)
    _save_state(force=True)
    nonebot.logger.info(f"[bilibili_live] uid={uid} ({uname}) 下播")


# ── 直播中：弹幕累积 + 每小时播报 ──────────────────────────────────────────────
async def update_session_danmaku(uid: str, room_id: int) -> None:
    """拉取最新弹幕，增量计入 counter 并记下原文供词云使用。"""
    session = live_session.get(uid)
    if not session:
        return
    seen = session["seen_danmaku"]
    counter = session["danmaku_counter"]
    texts = session.setdefault("danmaku_texts", [])
    for user, text in await fetch_danmaku_with_user(room_id):
        key = f"{user}:{text}"
        if user and key not in seen:
            seen.add(key)
            counter[user] += 1
            texts.append(text)


async def send_hourly_report(uid: str, info: dict[str, Any]) -> None:
    """直播中每小时播报：封面 + 已播时长 / 在线人数 + 最近 10 条弹幕，合成一张可爱卡片。"""
    session = live_session.get(uid)
    if not session:
        return

    uname = session.get("uname", uid)
    duration = fmt_duration(int((datetime.now() - session["start_time"]).total_seconds()))
    online = info.get("online", 0)
    room_id = session.get("room_id", "")
    live_url = f"https://live.bilibili.com/{room_id}"

    # 封面 + 最近弹幕；取不到也不影响播报（会退回文字版）
    cover_url = info.get("cover", "")
    cover_b64 = await fetch_cover_base64(cover_url) if cover_url else None
    danmaku: list[tuple[str, str]] = []
    if room_id:
        try:
            danmaku = await fetch_danmaku_with_user(int(room_id))
        except Exception as exc:  # noqa: BLE001 — 弹幕抓不到就只出封面
            nonebot.logger.warning(
                f"[bilibili_live] uid={uid} 播报卡抓弹幕失败（{type(exc).__name__} {exc}）"
            )

    card_jpeg = None
    try:
        card_jpeg = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: build_hourly_card(
                uname=uname,
                duration_text=duration,
                online_text=fmt_fans(online) if online else "",
                room_id=room_id,
                cover_bytes=base64.b64decode(cover_b64) if cover_b64 else None,
                danmaku=danmaku,
            ),
        )
    except Exception as exc:  # noqa: BLE001 — 出图失败不能影响播报本身
        nonebot.logger.warning(
            f"[bilibili_live] uid={uid} 播报卡片渲染异常（{type(exc).__name__} {exc}），退回文字版"
        )

    if card_jpeg:
        await notify_groups(
            UIDS.get(uid, []),
            [MessageSegment.image(f"base64://{base64.b64encode(card_jpeg).decode()}")],
            at_all=False,
        )
    else:
        text = (
            f"📡 {uname} 正在直播\n"
            f"⏱️ 目前已播 {duration}\n"
            f"👥 当前在线人数：{fmt_fans(online) if online else '未知'}\n"
            f"🔗 {live_url}"
        )
        await notify_groups(UIDS.get(uid, []), [MessageSegment.text(text)], at_all=False)

    session["last_hourly_notify"] = datetime.now()
    nonebot.logger.info(f"[bilibili_live] uid={uid} 每小时播报已发送")


# ── 定时轮询 ──────────────────────────────────────────────────────────────────
@scheduler.scheduled_job("interval", seconds=INTERVAL, id="bilibili_live_poll")
async def poll_live_status() -> None:
    """轮询所有监控中的 uid：识别开播/下播跳变，直播中累积弹幕并按时播报。"""
    if not UIDS:
        return

    uid_list = list(UIDS)
    nonebot.logger.debug(f"[bilibili_live] 开始轮询，共 {len(uid_list)} 个 uid")

    # 并行拉取，单个 uid 的接口异常不影响其他 uid
    results = await asyncio.gather(
        *(fetch_room_info(uid) for uid in uid_list), return_exceptions=True
    )

    for uid, info in zip(uid_list, results):
        if isinstance(info, Exception):
            nonebot.logger.warning(f"[bilibili_live] uid={uid} 接口异常: {info}")
            continue
        if info is None:
            nonebot.logger.warning(f"[bilibili_live] uid={uid} 接口请求失败，跳过")
            continue

        is_live = info.get("liveStatus") == 1
        was_live = live_status.get(uid, False)
        nonebot.logger.debug(
            f"[bilibili_live] uid={uid} liveStatus={info.get('liveStatus')} "
            f"is_live={is_live} was_live={was_live} title={info.get('title', '')[:20]}"
        )

        # 首次轮询：只静默识别当前状态，不推送开播通知
        if uid not in initialized:
            initialized.add(uid)
            live_status[uid] = is_live
            if is_live:
                uname, fans = await fetch_user_card(uid)
                _open_session(uid, info, uname, fans)
                nonebot.logger.info(
                    f"[bilibili_live] uid={uid} 初始化时已在直播，静默识别，1小时后播报"
                )
            else:
                nonebot.logger.info(f"[bilibili_live] uid={uid} 初始化完成，当前未直播")
            continue

        await _process_transition(uid, info, is_live)

    # 轮询结束后落盘一次：重启回来还能接着算这一场的时长与弹幕
    _save_state()
    nonebot.logger.debug("[bilibili_live] 本轮轮询结束")


async def _process_transition(uid: str, info: dict[str, Any], is_live: bool) -> None:
    """按本轮状态处理开播/下播跳变：通知、下播去抖、在线采样、每小时播报。

    单独抽出来是为了能直接驱动它做行为验证（不用真等一轮轮询）。
    """
    was_live = live_status.get(uid, False)

    if is_live and not was_live:
        nonebot.logger.info(f"[bilibili_live] uid={uid} 检测到开播，触发开播通知")
        offline_streak.pop(uid, None)
        live_status[uid] = True
        await on_live_start(uid, info)
        return

    if not is_live and was_live:
        # 接口偶发抖动会让 liveStatus 闪一下 0；连续 OFFLINE_CONFIRM_POLLS 轮都没在播
        # 才认定真下播，否则会把一场直播切碎，时长与弹幕榜单都要被切两半
        offline_streak[uid] = offline_streak.get(uid, 0) + 1
        if offline_streak[uid] >= OFFLINE_CONFIRM_POLLS:
            nonebot.logger.info(
                f"[bilibili_live] uid={uid} 连续 {offline_streak[uid]} 轮未在播，触发下播通知"
            )
            offline_streak[uid] = 0
            live_status[uid] = False
            await on_live_end(uid, info)
        else:
            nonebot.logger.info(
                f"[bilibili_live] uid={uid} 本轮未在播（第 {offline_streak[uid]} 轮），"
                f"等下一轮确认，先不下播"
            )
        return

    if is_live and uid in live_session:
        session = live_session[uid]
        if offline_streak.pop(uid, None):
            nonebot.logger.info(f"[bilibili_live] uid={uid} 抖动结束，仍在直播，会话保持不变")

        _record_online(session, info.get("online"))

        room_id = session.get("room_id")
        if room_id:
            before = sum(session["danmaku_counter"].values())
            await update_session_danmaku(uid, int(room_id))
            after = sum(session["danmaku_counter"].values())
            if after > before:
                nonebot.logger.debug(
                    f"[bilibili_live] uid={uid} 新增弹幕 {after - before} 条，累计 {after} 条"
                )

        last = session.get("last_hourly_notify")
        if last and (datetime.now() - last).total_seconds() >= HOURLY_REPORT_SECONDS:
            nonebot.logger.info(f"[bilibili_live] uid={uid} 触发每小时播报")
            await send_hourly_report(uid, info)


# ── 群聊「开播」关键词：手动查一遍本群在播的 UP 主 ──────────────────────────────
def _has_openlive_keyword(event: MessageEvent) -> bool:
    """群聊消息的纯文本里含「开播」两个字就触发；私聊不触发。"""
    return isinstance(event, GroupMessageEvent) and OPENLIVE_KEYWORD in event.get_plaintext()


def uids_of_group(group_id: str) -> list[str]:
    """反查某个群绑定了哪些 uid。

    每次调用都重读 .env，所以改监控名单不用重启机器人（与轮询用的是同一份配置）。
    """
    uids_map = load_config()["bilibili_live_uids"]
    return sorted(
        uid
        for uid, groups in uids_map.items()
        if any(str(group.get("groupId", "")) == group_id for group in groups)
    )


openlive_cmd = on_message(rule=Rule(_has_openlive_keyword), priority=OPENLIVE_PRIORITY, block=False)


async def _fetch_room_for_manual_query(uid: str) -> Optional[dict[str, Any]]:
    """手动查询专用的取数：失败重试 OPENLIVE_RETRIES 次。

    定时轮询每 60 秒会自己重来一次，不需要重试；手动查询只打一次，
    不重试的话偶发握手失败会表现为「一个在播的都没查出来」。
    """
    for attempt in range(1, OPENLIVE_RETRIES + 1):
        info = await fetch_room_info(uid)
        if info is not None:
            return info
        if attempt < OPENLIVE_RETRIES:
            await asyncio.sleep(1.5)
    nonebot.logger.warning(f"[bilibili_live] uid={uid} 重试 {OPENLIVE_RETRIES} 次仍取不到直播状态")
    return None


@openlive_cmd.handle()
async def handle_openlive_keyword(bot: Bot, event: GroupMessageEvent) -> None:
    """群里出现「开播」：先回一条 td，再把本群在播的 UP 主按开播通知发出来。

    这里不发 @全体（手动查询不该炸群），与下播通知、每小时播报的取法一致。
    """
    group_id = str(event.group_id)
    try:
        await openlive_cmd.send(OPENLIVE_REPLY)
    except Exception as exc:  # noqa: BLE001 — 发不出去就没必要继续查
        nonebot.logger.warning(f"[bilibili_live] 群 {group_id} 发送 {OPENLIVE_REPLY} 失败：{exc}")
        return

    uids = uids_of_group(group_id)
    if not uids:
        nonebot.logger.info(f"[bilibili_live] 群 {group_id} 没有配置开播监控")
        if OPENLIVE_NOTIFY_WHEN_EMPTY:
            await openlive_cmd.send("本群还没有配置 B 站开播监控")
        return

    nonebot.logger.info(f"[bilibili_live] 群 {group_id} 触发「开播」查询，监控 uid {uids}")

    results = await asyncio.gather(*(_fetch_room_for_manual_query(uid) for uid in uids))
    live_infos = [
        (uid, info)
        for uid, info in zip(uids, results)
        if isinstance(info, dict) and info.get("liveStatus") == 1
    ]
    failed = [uid for uid, info in zip(uids, results) if info is None]
    if failed:
        nonebot.logger.warning(f"[bilibili_live] 群 {group_id} 这些 uid 没查到直播状态：{failed}")
    if not live_infos:
        nonebot.logger.info(
            f"[bilibili_live] 群 {group_id} 配置的 {len(uids)} 个 uid 当前都没在播"
            + (f"（其中 {len(failed)} 个查询失败）" if failed else "")
        )
        if OPENLIVE_NOTIFY_WHEN_EMPTY:
            await openlive_cmd.send("本群监控的 UP 主当前都没有开播")
        return

    for uid, info in live_infos:
        uname, _ = await fetch_user_card(uid)
        try:
            await bot.send_group_msg(
                group_id=int(group_id),
                message=_compose(await build_live_notice(uid, info, uname), False),
            )
            await asyncio.sleep(SEND_GAP)
        except Exception as exc:  # noqa: BLE001 — 单个 UP 主发失败不影响其他
            nonebot.logger.warning(f"[bilibili_live] 群 {group_id} 发送 uid={uid} 开播通知失败：{exc}")
    nonebot.logger.info(
        f"[bilibili_live] 群 {group_id} 已发出 {len(live_infos)} 个在播 UP 主的开播通知"
    )


nonebot.logger.info(
    f"[bilibili_live] 插件已加载，监控 {len(UIDS)} 个 uid，轮询间隔 {INTERVAL}s，"
    f"群聊消息含「{OPENLIVE_KEYWORD}」时回 {OPENLIVE_REPLY} 并查一遍在播"
)
