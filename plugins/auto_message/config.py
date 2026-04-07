import json
import os
from pathlib import Path
from typing import Dict, List


def _find_env_file() -> Path:
    root = Path(__file__).parent.parent.parent
    for name in (".env.dev", ".env.prod", ".env"):
        p = root / name
        if p.exists():
            return p
    return root / ".env.dev"


def _parse_env_file(path: Path) -> Dict[str, str]:
    """解析 key=value 格式，支持值跨多行（遇到 [ 未闭合时持续拼接直到 ] 出现）"""
    result = {}
    if not path.exists():
        return result

    lines = path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        # 如果值包含未闭合的 [ 或 {，继续拼接后续行直到括号平衡
        open_brackets = value.count("[") + value.count("{")
        close_brackets = value.count("]") + value.count("}")
        while open_brackets > close_brackets and i < len(lines):
            next_line = lines[i].strip()
            i += 1
            if next_line.startswith("#"):
                continue
            value += next_line
            open_brackets += next_line.count("[") + next_line.count("{")
            close_brackets += next_line.count("]") + next_line.count("}")

        result[key] = value
    return result


def load_config() -> Dict:
    """
    配置格式（.env.dev / .env.prod）：

    AUTO_MESSAGE_TASKS=[
      {"target": "123456789", "type": "private", "interval": 1200, "messages": ["你好", "在吗"]},
      {"target": "987654321", "type": "group",   "interval": 600,  "messages": ["群通知内容"]}
    ]

    字段说明：
      target   - QQ 号（私聊）或 群号（群聊）
      type     - "private" 私聊 | "group" 群聊
      interval - 发送间隔（秒）
      messages - 消息列表，每次按顺序轮流发送（只有一条则每次发同一条）
    """
    env_path = _find_env_file()
    raw = _parse_env_file(env_path)

    tasks_raw = raw.get("AUTO_MESSAGE_TASKS", os.environ.get("AUTO_MESSAGE_TASKS", "[]"))
    try:
        tasks: List[Dict] = json.loads(tasks_raw)
    except (json.JSONDecodeError, TypeError):
        tasks = []

    return {"auto_message_tasks": tasks}
