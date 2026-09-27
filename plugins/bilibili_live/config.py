"""bilibili_live 的配置读取。

没走 pydantic 配置模型，而是自己读 .env：插件要在注册轮询任务之前拿到 uid 列表，
而这两个键一直放在 .env 里，直接复用同一份文件最省事。

读取的键（.env 里没有则回落到同名环境变量，再没有用默认值）：
  BILIBILI_LIVE_UIDS={"uid": [{"groupId": "群号", "isAtAll": true}]}   # 也兼容 ["群号", ...] 旧写法
  BILIBILI_LIVE_INTERVAL=60
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import nonebot

# 配置文件按优先级从项目根目录往下找
ENV_FILES = (".env.dev", ".env.prod", ".env")


def _find_env_file() -> Path:
    """从项目根目录找 .env.dev / .env.prod，优先 .env.dev。"""
    root = Path(__file__).parent.parent.parent
    for name in ENV_FILES:
        candidate = root / name
        if candidate.exists():
            return candidate
    return root / ".env.dev"


def _parse_env_file(path: Path) -> dict[str, str]:
    """解析 key=value，忽略注释与空行；值末尾的行内注释也去掉（引号包裹的值除外）。"""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if "#" in value and not value.startswith(('"', "'")):
            value = value[: value.index("#")].strip()
        values[key.strip()] = value
    return values


def _normalize_uids(raw_uids: object) -> dict[str, list[dict[str, Any]]]:
    """把两种配置写法统一成 {"uid": [{"groupId": "群号", "isAtAll": bool}]}。

    旧写法：{"uid": ["群号"]}
    新写法：{"uid": [{"groupId": "群号", "isAtAll": true}]}
    """
    if not isinstance(raw_uids, dict):
        nonebot.logger.warning("[bilibili_live] BILIBILI_LIVE_UIDS 不是 JSON 对象，按空配置处理")
        return {}

    normalized: dict[str, list[dict[str, Any]]] = {}
    for uid, groups in raw_uids.items():
        result: list[dict[str, Any]] = []
        for group in groups:
            if isinstance(group, str):
                result.append({"groupId": group, "isAtAll": False})
            elif isinstance(group, dict):
                result.append(
                    {
                        "groupId": str(group.get("groupId", "")),
                        "isAtAll": bool(group.get("isAtAll", False)),
                    }
                )
        normalized[uid] = result
    return normalized


def load_config() -> dict[str, Any]:
    """读配置，返回 {"bilibili_live_uids": {...}, "bilibili_live_interval": 60}。"""
    raw = _parse_env_file(_find_env_file())

    # BILIBILI_LIVE_UIDS：.env 优先，其次系统环境变量
    uids_raw = raw.get("BILIBILI_LIVE_UIDS", os.environ.get("BILIBILI_LIVE_UIDS", "{}"))
    try:
        uids = _normalize_uids(json.loads(uids_raw))
    except (json.JSONDecodeError, TypeError) as exc:
        nonebot.logger.warning(f"[bilibili_live] BILIBILI_LIVE_UIDS 解析失败，按空配置处理：{exc}")
        uids = {}

    # BILIBILI_LIVE_INTERVAL=60
    interval_raw = raw.get("BILIBILI_LIVE_INTERVAL", os.environ.get("BILIBILI_LIVE_INTERVAL", "60"))
    try:
        interval = int(interval_raw)
    except (ValueError, TypeError) as exc:
        nonebot.logger.warning(f"[bilibili_live] BILIBILI_LIVE_INTERVAL 非法，回落 60 秒：{exc}")
        interval = 60

    return {"bilibili_live_uids": uids, "bilibili_live_interval": interval}
