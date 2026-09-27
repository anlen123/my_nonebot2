"""auto_message 的配置读取：从项目根目录的 .env 文件或环境变量里取定时任务列表。

取值优先级：.env.dev → .env.prod → .env → 环境变量 → 空列表。

为什么要自己读文件：nonebot 自带的配置系统只能收标量字段，而这里的值是一整段 JSON，
还允许跨多行书写，所以本插件单独解析 .env。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

# 候选配置文件，按优先级从高到低
_ENV_FILENAMES = (".env.dev", ".env.prod", ".env")
# 仓库根目录：本文件位于 <root>/plugins/auto_message/config.py
_ROOT = Path(__file__).parent.parent.parent

TASKS_KEY = "AUTO_MESSAGE_TASKS"


def _find_env_file() -> Path:
    """返回第一个存在的配置文件；一个都不存在时返回默认的 .env.dev 路径。"""
    for name in _ENV_FILENAMES:
        candidate = _ROOT / name
        if candidate.exists():
            return candidate
    return _ROOT / _ENV_FILENAMES[0]


def _unclosed(text: str) -> bool:
    """值的 [ { 比 ] } 多，说明这个值还没写完，得接着读下一行。"""
    return text.count("[") + text.count("{") > text.count("]") + text.count("}")


def _parse_env_file(path: Path) -> Dict[str, str]:
    """解析 key=value 文本；值支持跨多行（括号没配平时继续拼接，注释行跳过）。"""
    if not path.exists():
        return {}

    result: Dict[str, str] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        index += 1
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, _, value = line.partition("=")
        value = value.strip()
        while _unclosed(value) and index < len(lines):
            continuation = lines[index].strip()
            index += 1
            if continuation.startswith("#"):
                continue
            value += continuation

        result[key.strip()] = value
    return result


def load_config() -> Dict[str, List[Dict[str, Any]]]:
    """读取定时任务列表。

    配置格式（.env.dev / .env.prod）：

    AUTO_MESSAGE_TASKS=[
      {"target": "123456789", "type": "private", "interval": 1200, "messages": ["你好", "在吗"]},
      {"target": "987654321", "type": "group",   "interval": 600,  "messages": ["群通知内容"]}
    ]

      target   - QQ 号（私聊）或 群号（群聊）
      type     - "private" 私聊 | "group" 群聊
      interval - 发送间隔（秒）
      messages - 消息列表，多条时按顺序轮流发送，单条则每次发同一条
    """
    raw = _parse_env_file(_find_env_file())
    tasks_raw = raw.get(TASKS_KEY, os.environ.get(TASKS_KEY, "[]"))
    try:
        tasks: List[Dict[str, Any]] = json.loads(tasks_raw)
    except (json.JSONDecodeError, TypeError):
        # 值不是合法 JSON 时按「没配置」处理，免得整个插件加载失败
        tasks = []
    return {"auto_message_tasks": tasks}
