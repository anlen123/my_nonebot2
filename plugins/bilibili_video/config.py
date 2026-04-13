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
    result = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if "#" in value and not (value.startswith('"') or value.startswith("'")):
            value = value[:value.index("#")].strip()
        result[key] = value
    return result


def _normalize_uids(raw_uids: dict) -> Dict[str, List[dict]]:
    result = {}
    for uid, groups in raw_uids.items():
        normalized = []
        for g in groups:
            if isinstance(g, str):
                normalized.append({"groupId": g, "isAtAll": False})
            elif isinstance(g, dict):
                normalized.append({
                    "groupId": str(g.get("groupId", "")),
                    "isAtAll": bool(g.get("isAtAll", False)),
                })
        result[uid] = normalized
    return result


def load_config() -> Dict:
    env_path = _find_env_file()
    raw = _parse_env_file(env_path)

    # BILIBILI_VIDEO_UIDS={"uid": [{"groupId": "xxx", "isAtAll": true}], ...}
    uids_raw = raw.get("BILIBILI_VIDEO_UIDS", os.environ.get("BILIBILI_VIDEO_UIDS", "{}"))
    try:
        uids = _normalize_uids(json.loads(uids_raw))
    except (json.JSONDecodeError, TypeError):
        uids = {}

    # 轮询间隔（秒），默认 300 秒（5 分钟）
    interval_raw = raw.get("BILIBILI_VIDEO_INTERVAL", os.environ.get("BILIBILI_VIDEO_INTERVAL", "300"))
    try:
        interval: int = int(interval_raw)
    except (ValueError, TypeError):
        interval = 300

    # B站登录 Cookie（SESSDATA），用于访问视频列表接口
    sessdata = raw.get("BILIBILI_SESSDATA", os.environ.get("BILIBILI_SESSDATA", ""))

    return {
        "bilibili_video_uids": uids,
        "bilibili_video_interval": interval,
        "bilibili_sessdata": sessdata,
    }
