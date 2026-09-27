"""bilibili_video 插件配置：从 .env.dev/.env.prod/.env 或进程环境里读 BILIBILI_* 项。

项目其余插件走 pydantic 配置，这里保持手写解析（历史行为：环境变量优先于文件，
且 UIDS 支持 JSON 字符串这一种写法），因此不改用 BaseSettings。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

# .env 查找顺序：按环境区分，最后回落到通用 .env
_ENV_FILENAMES = (".env.dev", ".env.prod", ".env")

_DEFAULT_INTERVAL = 300  # 轮询间隔默认值（秒）


def _find_env_file() -> Path:
    """按 _ENV_FILENAMES 顺序返回第一个存在的 .env 路径，都没有则返回 .env.dev。"""
    root = Path(__file__).parent.parent.parent
    for name in _ENV_FILENAMES:
        path = root / name
        if path.exists():
            return path
    return root / _ENV_FILENAMES[0]


def _parse_env_file(path: Path) -> Dict[str, str]:
    """极简 .env 解析：忽略注释/空行，值为非引号包裹时裁掉行尾 # 注释。"""
    result: Dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # 未加引号的值里 # 之后是注释；加了引号说明 # 是值本身的一部分
        if "#" in value and not (value.startswith('"') or value.startswith("'")):
            value = value[: value.index("#")].strip()
        result[key] = value
    return result


def _normalize_uids(raw_uids: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """把 uid -> 群配置统一成 [{"groupId": str, "isAtAll": bool}, ...] 形式。

    配置里群既可以写成纯字符串（等价于不 @全体），也可以写成字典。
    """
    result: Dict[str, List[Dict[str, Any]]] = {}
    for uid, groups in raw_uids.items():
        normalized: List[Dict[str, Any]] = []
        for group in groups:
            if isinstance(group, str):
                normalized.append({"groupId": group, "isAtAll": False})
            elif isinstance(group, dict):
                normalized.append({
                    "groupId": str(group.get("groupId", "")),
                    "isAtAll": bool(group.get("isAtAll", False)),
                })
        result[uid] = normalized
    return result


def _env_or(raw: Dict[str, str], key: str, default: str) -> str:
    """进程环境变量优先于 .env 文件，两者都没有时用 default。"""
    return raw.get(key, os.environ.get(key, default))


def load_config() -> Dict[str, Any]:
    """读取插件所需的全部配置，返回 load_config 键名 -> 值的字典。"""
    env_path = _find_env_file()
    raw = _parse_env_file(env_path)

    # BILIBILI_VIDEO_UIDS={"uid": [{"groupId": "xxx", "isAtAll": true}], ...}
    uids_raw = _env_or(raw, "BILIBILI_VIDEO_UIDS", "{}")
    try:
        uids = _normalize_uids(json.loads(uids_raw))
    except (json.JSONDecodeError, TypeError):
        uids = {}

    interval_raw = _env_or(raw, "BILIBILI_VIDEO_INTERVAL", str(_DEFAULT_INTERVAL))
    try:
        interval = int(interval_raw)
    except (ValueError, TypeError):
        interval = _DEFAULT_INTERVAL

    # B站登录 Cookie（SESSDATA），用于访问视频列表接口
    sessdata = _env_or(raw, "BILIBILI_SESSDATA", "")

    return {
        "bilibili_video_uids": uids,
        "bilibili_video_interval": interval,
        "bilibili_sessdata": sessdata,
    }

