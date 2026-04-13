"""
repeater —— 复读机插件

当群内同一条文字消息连续出现 REPEATER_THRESHOLD 次时，机器人跟着复读一次。
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

# ── 运行时状态：每个群记录上一条消息和连续出现次数 ───────────────────────────
# { group_id: {"text": str, "count": int} }
_state: Dict[str, dict] = defaultdict(lambda: {"text": "", "count": 0})

repeater = on_message(priority=99, block=False)


@repeater.handle()
async def repeater_handle(bot: Bot, event: GroupMessageEvent):
    group_id = str(event.group_id)
    # 只处理纯文字消息
    text = event.get_plaintext().strip()
    if not text:
        return

    state = _state[group_id]

    if text == state["text"]:
        state["count"] += 1
    else:
        state["text"]  = text
        state["count"] = 1

    if state["count"] == THRESHOLD:
        # 触发复读，重置计数防止重复触发
        state["count"] = 0
        nonebot.logger.info(f"[repeater] group={group_id} 触发复读：{text[:30]}")
        await bot.send_group_msg(group_id=int(group_id), message=MessageSegment.text(text))


nonebot.logger.info(f"[repeater] 插件已加载，触发阈值 {THRESHOLD} 次")
