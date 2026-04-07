"""
auto_message —— 定时自动发消息插件

配置（.env.dev / .env.prod）：

AUTO_MESSAGE_TASKS=[
  {"target": "123456789", "type": "private", "interval": 1200, "messages": ["你好", "在吗"]},
  {"target": "987654321", "type": "group",   "interval": 600,  "messages": ["群通知内容"]}
]

  target   - QQ 号（私聊）或 群号（群聊）
  type     - "private" 私聊 | "group" 群聊
  interval - 发送间隔（秒）
  messages - 消息列表，多条时按顺序轮流发送，单条则每次发同一条
"""

import nonebot
from nonebot import require
from nonebot.adapters.onebot.v11 import Bot, MessageSegment

from .config import load_config

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

# ── 加载配置 ──────────────────────────────────────────────────────────────────
_cfg = load_config()
TASKS = _cfg["auto_message_tasks"]

# 每个任务的消息轮转索引：task_index -> current_msg_index
_msg_cursor: dict = {}


# ── 为每个任务注册独立定时器 ──────────────────────────────────────────────────
def _make_job(task_index: int, task: dict):
    target   = str(task["target"])
    ttype    = task.get("type", "private")   # "private" | "group"
    interval = int(task.get("interval", 1200))
    messages = task.get("messages", [])

    if not messages:
        nonebot.logger.warning(f"[auto_message] task[{task_index}] messages 为空，跳过")
        return

    _msg_cursor[task_index] = 0

    async def _send():
        try:
            bot: Bot = nonebot.get_bot()
        except Exception:
            nonebot.logger.warning(f"[auto_message] task[{task_index}] no bot available")
            return

        idx  = _msg_cursor[task_index]
        text = messages[idx % len(messages)]
        _msg_cursor[task_index] = (idx + 1) % len(messages)

        try:
            if ttype == "group":
                await bot.send_group_msg(group_id=int(target), message=MessageSegment.text(text))
                nonebot.logger.info(f"[auto_message] task[{task_index}] -> 群 {target}：{text[:30]}")
            else:
                await bot.send_private_msg(user_id=int(target), message=MessageSegment.text(text))
                nonebot.logger.info(f"[auto_message] task[{task_index}] -> 私聊 {target}：{text[:30]}")
        except Exception as e:
            nonebot.logger.warning(f"[auto_message] task[{task_index}] 发送失败：{e}")

    scheduler.add_job(
        _send,
        trigger="interval",
        seconds=interval,
        id=f"auto_message_{task_index}",
        replace_existing=True,
    )
    nonebot.logger.info(
        f"[auto_message] task[{task_index}] 已注册：{ttype} {target}，间隔 {interval}s，"
        f"共 {len(messages)} 条消息"
    )


for i, t in enumerate(TASKS):
    _make_job(i, t)

if not TASKS:
    nonebot.logger.info("[auto_message] 未配置任何任务（AUTO_MESSAGE_TASKS 为空）")
