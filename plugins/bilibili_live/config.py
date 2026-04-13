import json
import os
from pathlib import Path
from typing import Dict, List


def _find_env_file() -> Path:
    """从项目根目录找 .env.dev 或 .env.prod，优先 .env.dev"""
    root = Path(__file__).parent.parent.parent
    for name in (".env.dev", ".env.prod", ".env"):
        p = root / name
        if p.exists():
            return p
    return root / ".env.dev"


def _parse_env_file(path: Path) -> Dict[str, str]:
    """简单解析 key=value 格式，忽略注释和空行"""
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
    """
    兼容两种配置格式，统一转为 {uid: [{"groupId": "xxx", "isAtAll": bool}]}
      旧格式：{"uid": ["group1", "group2"]}
      新格式：{"uid": [{"groupId": "group1", "isAtAll": true}]}
    """
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

    # BILIBILI_LIVE_UIDS={"uid": [{"groupId": "xxx", "isAtAll": true}], ...}
    uids_raw = raw.get("BILIBILI_LIVE_UIDS", os.environ.get("BILIBILI_LIVE_UIDS", "{}"))
    try:
        uids = _normalize_uids(json.loads(uids_raw))
    except (json.JSONDecodeError, TypeError):
        uids = {}

    # BILIBILI_LIVE_INTERVAL=60
    interval_raw = raw.get("BILIBILI_LIVE_INTERVAL", os.environ.get("BILIBILI_LIVE_INTERVAL", "60"))
    try:
        interval: int = int(interval_raw)
    except (ValueError, TypeError):
        interval = 60

    return {"bilibili_live_uids": uids, "bilibili_live_interval": interval}
