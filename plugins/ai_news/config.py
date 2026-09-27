"""
ai_news —— AI 资讯早报插件配置读取

配置项（写进 .env.dev / .env.prod，都可有默认值）：

  AI_NEWS_ENABLED=true                      # 总开关
  AI_NEWS_CRON=30 9 * * *                   # 分钟 小时 日 月 星期（默认每天 09:30）
  AI_NEWS_TIMEZONE=Asia/Shanghai            # 留空则用本机时区
  AI_NEWS_TARGET_TYPE=private               # private 私聊 / group 群聊
  AI_NEWS_TARGET=1761512493                 # QQ 号（私聊）或群号（群聊）
  AI_NEWS_MAX_ITEMS=15                      # 每天发送的条数
  AI_NEWS_PROXY=http://127.0.0.1:7892       # 代理，留空或 direct 表示直连（失败会自动回退直连）
  AI_NEWS_TIMEOUT=30                        # 单源超时（秒）
  AI_NEWS_SOURCES=["zhidx","leiphone",...]  # 启用的资讯源 key 列表
  AI_NEWS_EXTRA_RSS=[{"name":"站点名","url":"https://..."}]   # 追加自定义 RSS 源
  AI_NEWS_HISTORY_DAYS=2                    # 去重：N 天内已发过的新闻不再发（0 关闭）
  AI_NEWS_SHOW_LINKS=true                   # 表格里是否显示原文链接

所有值都先从 .env 文件读，读到空再回落到真实环境变量，最后才是这里的默认值。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .sources import DEFAULT_SOURCES, SOURCES

# AI_NEWS_CRON 解析失败时的兜底：每天 09:30
_FALLBACK_CRON: Dict[str, str] = {
    "minute": "30",
    "hour": "9",
    "day": "*",
    "month": "*",
    "day_of_week": "*",
}


def _find_env_file() -> Path:
    """按 .env.dev → .env.prod → .env 的顺序找第一个存在的；都没有就给 .env.dev 这个路径"""
    root = Path(__file__).parent.parent.parent
    for name in (".env.dev", ".env.prod", ".env"):
        path = root / name
        if path.exists():
            return path
    return root / ".env.dev"


def _parse_env_file(path: Path) -> Dict[str, str]:
    """解析 key=value；值里有没闭合的 [ 或 { 就把后续行拼上来（支持多行 JSON）"""
    result: Dict[str, str] = {}
    if not path.exists():
        return result

    lines = path.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        index += 1
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        open_brackets = value.count("[") + value.count("{")
        close_brackets = value.count("]") + value.count("}")
        while open_brackets > close_brackets and index < len(lines):
            following = lines[index].strip()
            index += 1
            if following.startswith("#"):
                continue
            value += following
            open_brackets += following.count("[") + following.count("{")
            close_brackets += following.count("]") + following.count("}")
        result[key] = value
    return result


def _as_bool(value: Optional[str], default: bool) -> bool:
    """宽松地认 true/1/yes/on/y；留空或没写就用默认值"""
    if value is None or value == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on", "y")


def _as_json_list(raw: str) -> List[Any]:
    """把 JSON 数组配置项解析成列表；不是合法数组一律当空列表"""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return data if isinstance(data, list) else []


def _parse_cron(expr: str) -> Dict[str, str]:
    """把 '30 9 * * *' 解析成 apscheduler 的 cron 参数，解析失败回退 09:30"""
    if not expr:
        return dict(_FALLBACK_CRON)

    parts = expr.split()
    if len(parts) != 5:
        return dict(_FALLBACK_CRON)

    def _field(value: str) -> str:
        """单个字段规范化：? 当 *，*/n 与 a,b 里的数字都过一遍 int 校验"""
        value = value.strip()
        if value in ("*", "?"):
            return "*"
        if value.startswith("*/"):
            return "*/" + str(int(value[2:]))
        return ",".join(str(int(x)) for x in value.split(","))

    try:
        fields = [_field(part) for part in parts]
    except (ValueError, TypeError):
        return dict(_FALLBACK_CRON)

    return {
        "minute": fields[0],
        "hour": fields[1],
        "day": fields[2],
        "month": fields[3],
        "day_of_week": fields[4],
    }


def _build_sources(keys: List[str], extra_rss: Optional[List[Any]]) -> List[Dict[str, Any]]:
    """按顺序返回启用源的定义列表；未知 key 跳过，但支持 "名称|https://url" 简写"""
    result: List[Dict[str, Any]] = []
    for raw_key in keys:
        key = str(raw_key).strip()
        spec = SOURCES.get(key)
        if spec is not None:
            result.append({"key": key, **spec})
        elif "|" in key:
            name, _, url = key.partition("|")
            result.append({"key": name, "name": name, "kind": "rss", "url": url, "limit": 3})

    for idx, item in enumerate(extra_rss or []):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "")).strip()
        if not url:
            continue
        name = str(item.get("name") or f"自定义{idx + 1}").strip()
        result.append(
            {
                "key": f"extra{idx}",
                "name": name,
                "kind": "rss",
                "url": url,
                "limit": int(item.get("limit", 3)),
            }
        )
    return result


def load_config() -> Dict[str, Any]:
    """读 .env（其次环境变量）并补全默认值，返回插件运行需要的整份配置"""
    env_file = _find_env_file()
    raw = _parse_env_file(env_file)

    def get(key: str, default: str = "") -> str:
        """先看 .env 里的值，空的话再看真实环境变量（方便临时覆盖）"""
        value = raw.get(key)
        if value is None or value == "":
            value = os.environ.get(key, default)
        return value

    keys = [str(k) for k in _as_json_list(get("AI_NEWS_SOURCES", ""))] or list(DEFAULT_SOURCES)
    extra_rss = _as_json_list(get("AI_NEWS_EXTRA_RSS", ""))

    proxy = get("AI_NEWS_PROXY", "http://127.0.0.1:7892").strip()
    if proxy.lower() in ("", "none", "direct", "off", "false"):
        proxy = ""  # 明确表示不走代理

    raw_cron = get("AI_NEWS_CRON", "30 9 * * *")
    cron = _parse_cron(raw_cron)
    cron_label = ""
    if (
        cron["day"] == "*"
        and cron["month"] == "*"
        and cron["day_of_week"] == "*"
        and cron["hour"].isdigit()
        and cron["minute"].isdigit()
    ):
        # 只有「每天固定时刻」的写法才能给图上的副标题提供 09:30 这种标签
        cron_label = f"{int(cron['hour']):02d}:{int(cron['minute']):02d}"

    return {
        "enabled": _as_bool(get("AI_NEWS_ENABLED", "true"), True),
        "cron": cron,
        "raw_cron": raw_cron,
        "cron_label": cron_label,
        "timezone": get("AI_NEWS_TIMEZONE", "Asia/Shanghai").strip() or None,
        "target_type": get("AI_NEWS_TARGET_TYPE", "private").strip().lower(),
        "target": get("AI_NEWS_TARGET", "1761512493").strip(),
        "max_items": int(get("AI_NEWS_MAX_ITEMS", "15") or 15),
        "proxy": proxy,
        "timeout": float(get("AI_NEWS_TIMEOUT", "30") or 30),
        "sources": _build_sources(keys, extra_rss),
        "history_days": int(get("AI_NEWS_HISTORY_DAYS", "2") or 0),
        "show_links": _as_bool(get("AI_NEWS_SHOW_LINKS", "true"), True),
        "env_file": str(env_file),
    }
