"""auto_message —— 定时自动发消息插件

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

from __future__ import annotations

from typing import Any, Dict, List, Mapping

import nonebot
from nonebot import require
from nonebot.adapters.onebot.v11 import Bot, MessageSegment

from .config import load_config

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402 —— 必须在 require 之后导入

TASKS: List[Dict[str, Any]] = load_config()["auto_message_tasks"]

# 每条任务下一条该发下标几的消息：task_index -> 消息下标
_msg_cursor: Dict[int, int] = {}


def _register_task(task_index: int, task: Mapping[str, Any]) -> None:
    """给单条任务挂一个 interval 定时器；任务配置有问题时只记日志，不打断插件加载。"""
    target = str(task["target"])
    task_type = task.get("type", "private")  # "private" | "group"
    interval = int(task.get("interval", 1200))
    messages: List[str] = task.get("messages", [])

    if not messages:
        nonebot.logger.warning(f"[auto_message] task[{task_index}] messages 为空，跳过")
        return

    async def _send() -> None:
        try:
            bot: Bot = nonebot.get_bot()
        except Exception as exc:  # noqa: BLE001 —— 当前没有在线 bot，这一轮直接跳过
            nonebot.logger.warning(f"[auto_message] task[{task_index}] no bot available：{exc}")
            return

        # 先推进下标再发送：发送失败也算用掉这一条，与既有行为一致
        index = _msg_cursor.get(task_index, 0)
        text = messages[index % len(messages)]
        _msg_cursor[task_index] = (index + 1) % len(messages)

        try:
            if task_type == "group":
                await bot.send_group_msg(group_id=int(target), message=MessageSegment.text(text))
                nonebot.logger.info(f"[auto_message] task[{task_index}] -> 群 {target}：{text[:30]}")
            else:
                await bot.send_private_msg(user_id=int(target), message=MessageSegment.text(text))
                nonebot.logger.info(f"[auto_message] task[{task_index}] -> 私聊 {target}：{text[:30]}")
        except Exception as exc:  # noqa: BLE001 —— 单条任务发失败不影响其它任务
            nonebot.logger.warning(f"[auto_message] task[{task_index}] 发送失败：{exc}")

    scheduler.add_job(
        _send,
        trigger="interval",
        seconds=interval,
        id=f"auto_message_{task_index}",
        replace_existing=True,
    )
    nonebot.logger.info(
        f"[auto_message] task[{task_index}] 已注册：{task_type} {target}，间隔 {interval}s，"
        f"共 {len(messages)} 条消息"
    )


if not TASKS:
    nonebot.logger.info("[auto_message] 未配置任何任务（AUTO_MESSAGE_TASKS 为空）")

for _task_index, _task in enumerate(TASKS):
    _register_task(_task_index, _task)
