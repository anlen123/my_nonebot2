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
        # 去掉行尾注释（# 之后的内容），但要保留字符串内部的 #
        value = value.strip()
        if "#" in value and not (value.startswith('"') or value.startswith("'")):
            value = value[:value.index("#")].strip()
        result[key] = value
    return result


def load_config() -> Dict:
    env_path = _find_env_file()
    raw = _parse_env_file(env_path)

    # BILIBILI_LIVE_UIDS={"uid": ["group1", "group2"], ...}
    uids_raw = raw.get("BILIBILI_LIVE_UIDS", os.environ.get("BILIBILI_LIVE_UIDS", "{}"))
    try:
        uids: Dict[str, List[str]] = json.loads(uids_raw)
    except (json.JSONDecodeError, TypeError):
        uids = {}

    # BILIBILI_LIVE_INTERVAL=60
    interval_raw = raw.get("BILIBILI_LIVE_INTERVAL", os.environ.get("BILIBILI_LIVE_INTERVAL", "60"))
    try:
        interval: int = int(interval_raw)
    except (ValueError, TypeError):
        interval = 60

    return {"bilibili_live_uids": uids, "bilibili_live_interval": interval}
