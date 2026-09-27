"""deepseek_balance 的配置读取（不依赖 nonebot，可单独 import 测试）

读取顺序沿用插件原有约定：先看仓库根目录的 .env 文件，再看进程环境变量。

配置（.env.dev / .env.prod）：
  DEEPSEEK_API_KEY=sk-xxx            # DeepSeek API Key
  DEEPSEEK_BALANCE_INTERVAL=7200     # 检查间隔（秒），默认 2 小时
  DEEPSEEK_BALANCE_TARGET=1761512493 # 接收余额通知的 QQ 号
  DEEPSEEK_BALANCE_LOW=10            # 余额预警线（低于该值卡片变色），默认 10

  # 站外供应商的余额查询密钥（/ai余额 一次性查询用）
  AIHUB_API_KEY=sk-xxx               # Aihub（https://aihub.top）
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional

# 站外供应商密钥的 .env 变量名（需与 providers.PROVIDERS 里的 env 字段一致）
BALANCE_KEY_NAMES = ["AIHUB_API_KEY"]

_DEFAULT_INTERVAL = "7200"
_DEFAULT_TARGET = "1761512493"
_DEFAULT_LOW = "10"
_LOW_FALLBACK = 10.0


def _find_env_file() -> Path:
    """定位仓库根目录下的 .env 文件；一个都没有时返回默认的 .env.dev 路径"""
    root = Path(__file__).parent.parent.parent
    for name in (".env.dev", ".env.prod", ".env"):
        path = root / name
        if path.exists():
            return path
    return root / ".env.dev"


def _parse_env_file(path: Path) -> Dict[str, str]:
    """解析 key=value 格式的 env 文件；文件不存在返回空表"""
    result: Dict[str, str] = {}
    if not path.exists():
        return result

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip()
    return result


def _from_env_file(raw: Dict[str, str], name: str, default: str) -> str:
    """.env 优先、环境变量其次（本插件主配置的既定顺序，勿改成 load_balance_keys 那种）"""
    return raw.get(name, os.environ.get(name, default))


def load_balance_keys(raw: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """读取站外供应商的密钥：环境变量优先，其次 .env 文件"""
    raw = raw if raw is not None else _parse_env_file(_find_env_file())
    return {
        name: (os.environ.get(name) or raw.get(name, "")).strip()
        for name in BALANCE_KEY_NAMES
    }


def load_config() -> Dict[str, Any]:
    """汇总插件需要的全部配置项，键名即调用方使用的名字"""
    raw = _parse_env_file(_find_env_file())

    try:
        low = float(_from_env_file(raw, "DEEPSEEK_BALANCE_LOW", _DEFAULT_LOW))
    except (TypeError, ValueError):
        # .env 里写了个非数字（如 "10 元"）时不报错，退回默认预警线
        low = _LOW_FALLBACK

    return {
        "balance_keys": load_balance_keys(raw),
        "deepseek_api_key": _from_env_file(raw, "DEEPSEEK_API_KEY", ""),
        "deepseek_balance_interval": int(
            _from_env_file(raw, "DEEPSEEK_BALANCE_INTERVAL", _DEFAULT_INTERVAL)
        ),
        "deepseek_balance_target": _from_env_file(
            raw, "DEEPSEEK_BALANCE_TARGET", _DEFAULT_TARGET
        ),
        "deepseek_balance_low": low,
    }
