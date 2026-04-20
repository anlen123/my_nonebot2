"""
repeater —— 复读机插件

当群内同一条消息（文字/图片/表情包）连续出现 REPEATER_THRESHOLD 次时，机器人跟着复读一次。
复读后重置计数，避免无限复读。

配置（.env.dev / .env.prod）：
  REPEATER_THRESHOLD=3   # 触发复读所需的连续重复次数，默认 3
"""

import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Dict

import nonebot
from nonebot.plugin import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment


# ── 读取配置 ──────────────────────────────────────────────────────────────────

def _load_threshold() -> int:
    root = Path(__file__).parent.parent.parent
    for name in (".env.dev", ".env.prod", ".env"):
        p = root / name
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("REPEATER_THRESHOLD"):
                _, _, val = line.partition("=")
                try:
                    return int(val.strip())
                except ValueError:
                    pass
    raw = os.environ.get("REPEATER_THRESHOLD", "3")
    try:
        return int(raw)
    except ValueError:
        return 3


THRESHOLD: int = _load_threshold()

# ── 运行时状态：每个群记录上一条消息的 key 和连续次数 ────────────────────────
# { group_id: {"key": str, "count": int, "message": Message} }
_state: Dict[str, dict] = defaultdict(lambda: {"key": "", "count": 0, "message": None})

repeater = on_message(priority=99, block=False)


def _msg_key(event: GroupMessageEvent) -> str:
    """
    生成消息唯一 key，用于判断是否是同一条消息。
    - 纯文字：取文字内容
    - 图片：取 file/url 字段（同一张图 file 相同）
    - 表情：取 id
    - 混合：拼接各段
    """
    parts = []
    for seg in event.message:
        if seg.type == "text":
            t = seg.data.get("text", "").strip()
            if t:
                parts.append(f"text:{t}")
        elif seg.type == "image":
            # 优先用 file（md5），没有再用 url
            key = seg.data.get("file") or seg.data.get("url") or ""
            parts.append(f"image:{key}")
        elif seg.type == "face":
            parts.append(f"face:{seg.data.get('id', '')}")
        elif seg.type == "mface":
            # 魔法表情/表情包
            parts.append(f"mface:{seg.data.get('emoji_id', '')}{seg.data.get('key', '')}")
        else:
            parts.append(f"{seg.type}:{str(seg.data)[:50]}")
    return "|".join(parts)


@repeater.handle()
async def repeater_handle(bot: Bot, event: GroupMessageEvent):
    # 忽略机器人自己发的消息，避免自我触发循环
    if str(event.user_id) == str(bot.self_id):
        return

    group_id = str(event.group_id)
    key = _msg_key(event)
    if not key:
        return

    state = _state[group_id]

    if key == state["key"]:
        state["count"] += 1
    else:
        state["key"]     = key
        state["count"]   = 1
        state["message"] = event.message

    if state["count"] == THRESHOLD:
        state["count"] = 0
        nonebot.logger.info(f"[repeater] group={group_id} 触发复读：{key[:50]}")
        await bot.send_group_msg(group_id=int(group_id), message=state["message"])


nonebot.logger.info(f"[repeater] 插件已加载，触发阈值 {THRESHOLD} 次")
