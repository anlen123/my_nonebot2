"""repeater —— 复读机插件

当群内同一条消息（文字/图片/表情包）连续出现 REPEATER_THRESHOLD 次时，机器人跟着复读一次。
复读后重置计数，避免无限复读。

配置（.env.dev / .env.prod）：
  REPEATER_THRESHOLD=3   # 触发复读所需的连续重复次数，默认 3
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import nonebot
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message
from nonebot.plugin import on_message

# 候选配置文件，按优先级从高到低
_ENV_FILENAMES = (".env.dev", ".env.prod", ".env")
# 仓库根目录：本文件位于 <root>/plugins/repeater/__init__.py
_ROOT = Path(__file__).parent.parent.parent

THRESHOLD_KEY = "REPEATER_THRESHOLD"
DEFAULT_THRESHOLD = 3


def _read_threshold(path: Path) -> Optional[int]:
    """从单个配置文件里读阈值；没有这个键或值不是整数时返回 None。"""
    if not path.exists():
        return None

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith(THRESHOLD_KEY):
            continue
        _, _, value = line.partition("=")
        try:
            return int(value.strip())
        except ValueError:
            # 这一行写坏了就当没写，继续看下一行/下一个文件
            continue
    return None


def _load_threshold() -> int:
    """按 .env.dev → .env.prod → .env → 环境变量 → 默认值 的顺序取阈值。"""
    for name in _ENV_FILENAMES:
        threshold = _read_threshold(_ROOT / name)
        if threshold is not None:
            return threshold

    try:
        return int(os.environ.get(THRESHOLD_KEY, str(DEFAULT_THRESHOLD)))
    except ValueError:
        return DEFAULT_THRESHOLD


THRESHOLD: int = _load_threshold()


@dataclass
class _GroupState:
    """某个群「最近一条消息」的现场，用于判断有没有连续重复。"""

    key: str = ""
    count: int = 0
    message: Optional[Message] = None


# 运行时状态：{ 群号: 该群最近一条消息的记录 }
_state: Dict[str, _GroupState] = {}

repeater = on_message(priority=99, block=False)


def _msg_key(event: GroupMessageEvent) -> str:
    """生成消息指纹，用来判断是不是同一条消息。

    - 纯文字：取文字内容
    - 图片：取 file/url 字段（同一张图 file 相同）
    - 表情：取 id
    - 混合：拼接各段
    """
    parts = []
    for seg in event.message:
        if seg.type == "text":
            text = seg.data.get("text", "").strip()
            if text:
                parts.append(f"text:{text}")
        elif seg.type == "image":
            # 优先用 file（md5），没有再用 url
            source = seg.data.get("file") or seg.data.get("url") or ""
            parts.append(f"image:{source}")
        elif seg.type == "face":
            parts.append(f"face:{seg.data.get('id', '')}")
        elif seg.type == "mface":
            # 魔法表情/表情包
            parts.append(f"mface:{seg.data.get('emoji_id', '')}{seg.data.get('key', '')}")
        else:
            parts.append(f"{seg.type}:{str(seg.data)[:50]}")
    return "|".join(parts)


@repeater.handle()
async def repeater_handle(bot: Bot, event: GroupMessageEvent) -> None:
    # 忽略机器人自己发的消息，避免自我触发循环
    if str(event.user_id) == str(bot.self_id):
        return

    key = _msg_key(event)
    if not key:
        return

    group_id = str(event.group_id)
    state = _state.setdefault(group_id, _GroupState())

    if key == state.key:
        state.count += 1
    else:
        state.key = key
        state.count = 1
        state.message = event.message

    if state.count != THRESHOLD:
        return

    state.count = 0
    nonebot.logger.info(f"[repeater] group={group_id} 触发复读：{key[:50]}")
    await bot.send_group_msg(group_id=int(group_id), message=state.message)


nonebot.logger.info(f"[repeater] 插件已加载，触发阈值 {THRESHOLD} 次")
