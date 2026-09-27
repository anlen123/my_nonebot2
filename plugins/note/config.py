"""note 插件配置加载

读取 .env.dev / .env.prod 中的 NOTE_* 配置项，全部有默认值，不配置也能跑。

  NOTE_PUSH_TARGET     接收备忘录推送的 QQ 号，默认 1761512493
  NOTE_PUSH_INTERVAL   推送间隔（秒），默认 1200（20 分钟）
  NOTE_PUSH_WHEN_EMPTY 无记录时是否也推送，默认 false（避免每 20 分钟刷屏）
  NOTE_STORAGE_PATH    存储文件路径，默认 <项目根>/data/note/notes.json
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

# 机器人根目录：本文件在 <根>/plugins/note/ 下，往上三层就是根
_PROJECT_ROOT = Path(__file__).parent.parent.parent
_ENV_FILES = (".env.dev", ".env.prod", ".env")

# 默认值集中在这里，和上面的文档一一对应
_DEFAULT_TARGET = 1761512493
_DEFAULT_INTERVAL = 1200
_DEFAULT_STORAGE = Path("data") / "note" / "notes.json"


def _find_env_file() -> Path:
    """按 .env.dev → .env.prod → .env 找第一个存在的，都没找到就按 .env.dev 处理。

    返回的路径可能并不存在 —— _parse_env_file 会把不存在的文件当空配置，
    所以这里不需要额外判断。
    """
    for name in _ENV_FILES:
        path = _PROJECT_ROOT / name
        if path.exists():
            return path
    return _PROJECT_ROOT / _ENV_FILES[0]


def _parse_env_file(path: Path) -> Dict[str, str]:
    """解析 key=value，支持值跨多行（遇到未闭合的 [ 或 { 时持续拼接）

    只切出 key/value 并去掉首尾空白，不切行尾的 # 注释 —— 值本身就可能含 #
    （例如密码），按注释切掉会拿到一段残缺的值。
    """
    result: Dict[str, str] = {}
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

        open_brackets = value.count("[") + value.count("{")
        close_brackets = value.count("]") + value.count("}")
        while open_brackets > close_brackets and i < len(lines):
            nxt = lines[i].strip()
            i += 1
            if nxt.startswith("#"):
                continue
            value += nxt
            open_brackets += nxt.count("[") + nxt.count("{")
            close_brackets += nxt.count("]") + nxt.count("}")

        result[key] = value
    return result


def _as_bool(raw: Optional[str], default: bool) -> bool:
    """判真：只有 1/true/yes/on/y 算开，空值按 default 处理。"""
    if not raw:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "y")


def _as_int(raw: Optional[str], default: int) -> int:
    """配置里的数字可能被写坏，坏值一律回落到默认值，不让插件加载失败。"""
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def load_config() -> Dict:
    """读取 NOTE_* 配置。

    真实环境变量优先于 .env 文件；非正数的推送间隔按默认值处理。
    """
    env_path = _find_env_file()
    raw = _parse_env_file(env_path)

    def get(key: str, default: str = "") -> str:
        # 真实环境变量优先于 .env 文件
        return os.environ.get(key) or raw.get(key, default)

    interval = _as_int(get("NOTE_PUSH_INTERVAL"), _DEFAULT_INTERVAL)
    if interval <= 0:
        interval = _DEFAULT_INTERVAL

    target = _as_int(get("NOTE_PUSH_TARGET"), _DEFAULT_TARGET)

    storage = get("NOTE_STORAGE_PATH", "")
    storage_path = Path(storage) if storage else _PROJECT_ROOT / _DEFAULT_STORAGE

    return {
        "env_path": env_path,
        "push_target": target,
        "push_interval": interval,
        "push_when_empty": _as_bool(get("NOTE_PUSH_WHEN_EMPTY", ""), False),
        "storage_path": storage_path,
    }
