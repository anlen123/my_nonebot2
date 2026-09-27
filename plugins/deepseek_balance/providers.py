"""AI 供应商余额查询（DeepSeek 之外的站外供应商）

统一内部结构（供 render.render_multi 使用）：
    {
        "title":  名称,
        "balance": 数值 或 None,
        "unit":   "RMB" / "USD",
        "status": "ok" | "warning" | "error",
        "detail": 一句说明（错误时就是失败原因）,
        "extra":  {"used": ..., "total": ..., "granted": ..., "topped_up": ...}
    }

设计要点：
  * 各家互不影响：并发查询，某家超时/报错只在它自己那一行显示原因
  * 拒绝重定向（httpx follow_redirects=False），避免 Authorization 被转发到别的地址
  * 单次超时 6 秒（与原始脚本一致）；默认直连，遇到 403 / 超时用本机代理再试一次
  * 密钥从 .env 的 AIHUB_API_KEY 读取，代码里不硬编码
"""

from __future__ import annotations

import asyncio
import math
import os
from typing import Any, Dict, List, Optional, Tuple

import httpx
import nonebot

logger = nonebot.logger

TIMEOUT = 6.0                 # 直连超时（秒）
RETRY_TIMEOUT = 3.0           # 走本机代理重试的超时：代理就在本机，不用等太久
DEFAULT_PROXY = "http://127.0.0.1:7892"

# ── 供应商定义 ────────────────────────────────────────────────────────────────
PROVIDERS: List[Dict[str, Any]] = [
    {
        "title": "Aihub",
        "env": "AIHUB_API_KEY",
        "url": "https://aihub.top/v1/usage",
        "kind": "usage",
        "unit": "USD",          # 该站接口返回 unit=USD，实际币种以接口为准（见 _unit_of）
    },
]


# ── 小工具 ────────────────────────────────────────────────────────────────────
def entry(
    title: str,
    balance: Optional[float],
    unit: str,
    status: str,
    detail: str,
    **extra: Any,
) -> Dict[str, Any]:
    """构造统一结构的一行记录；值为 None 的 extra 字段直接丢掉（渲染层按缺失处理）"""
    return {
        "title": title,
        "balance": balance,
        "unit": unit,
        "status": status,
        "detail": detail,
        "extra": {k: v for k, v in extra.items() if v is not None},
    }


def _error_entry(spec: Dict[str, Any], reason: str) -> Dict[str, Any]:
    """把某家供应商整体标成失败行（名称与币种沿用它的配置）"""
    return entry(spec["title"], None, spec["unit"], "error", reason)


def _client(timeout: float, proxy: Optional[str] = None) -> httpx.AsyncClient:
    """统一构造客户端：不读环境里的代理，代理只在需要时显式传入"""
    return httpx.AsyncClient(
        proxy=proxy, trust_env=False, timeout=timeout, follow_redirects=False
    )


def _num(value: Any) -> Optional[float]:
    """转成有限浮点数；bool / None / 非数字 / NaN / inf 一律返回 None"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, str):
        try:
            number = float(value.replace(",", "").strip())
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return None


def _first(*values: Any) -> Any:
    """取第一个非 None 的值（各家接口的字段名不同，按优先级往下试）"""
    return next((v for v in values if v is not None), None)


def _dig(body: Any, *path: str) -> Any:
    """按路径取嵌套字典里的值，中途不是字典就当取不到"""
    cur = body
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


# 只认这几个币种：部分中转站的 unit 字段不可靠，乱填的值宁可忽略
_UNIT_ALIASES = {"USD": "USD", "CNY": "RMB", "RMB": "RMB"}


def _unit_of(spec: Dict[str, Any], body: Dict[str, Any]) -> str:
    """接口明确给出可识别币种时优先采用，否则退回供应商配置里的 unit"""
    raw = body.get("unit")
    if isinstance(raw, str):
        name = _UNIT_ALIASES.get(raw.strip().upper())
        if name:
            return name
    return spec["unit"]


def _proxy_candidate() -> Optional[str]:
    """直连失败时使用的本机代理：环境变量可以关掉，未配置则用默认值"""
    for name in ("BALANCE_NOTE_PROXY", "CLOUDSHOT_PROXY"):
        value = (os.environ.get(name) or "").strip()
        if value and value.lower() not in {"direct", "none", "off", "0"}:
            return value
    return DEFAULT_PROXY


def _http_detail(code: int) -> str:
    """把常见的 HTTP 错误码翻成用户能看懂的一句话"""
    if code == 401:
        return "密钥无效或已失效（HTTP 401）"
    if code == 403:
        return "服务器拒绝请求（HTTP 403，可能是出口 IP 或权限限制）"
    if code == 404:
        return "接口地址不存在（HTTP 404）"
    if code == 429:
        return "请求过于频繁（HTTP 429）"
    return f"接口请求失败（HTTP {code}）"


# ── 解析各家返回 ──────────────────────────────────────────────────────────────
def _parse_usage(spec: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    """解析 /v1/usage 一类返回：余额可能在 remaining / quota.remaining / balance"""
    title = spec["title"]
    unit = _unit_of(spec, body)
    quota = body.get("quota")
    quota = quota if isinstance(quota, dict) else {}

    remaining = _first(body.get("remaining"), quota.get("remaining"), body.get("balance"))
    valid = _first(body.get("is_active"), body.get("isValid"), True)
    number = _num(remaining)
    # 累计消耗：aihub 等站的 /v1/usage 会给出 usage.total.actual_cost
    used = _num(_dig(body, "usage", "total", "actual_cost"))

    if remaining is None or number is None or isinstance(remaining, (dict, list, bool)):
        # missing_is_error 交给供应商自己决定「没返回余额」算警告还是算故障
        status = "error" if spec.get("missing_is_error") else "warning"
        return entry(title, None, unit, status, "接口未返回余额", used=used)
    if not valid:
        return entry(title, number, unit, "warning", "账户无效", used=used)
    return entry(title, number, unit, "ok", "余额已更新", used=used)


def _parse_credit(spec: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    """解析授信类返回：余额在 total_available，另附已用与总额"""
    title = spec["title"]
    unit = body.get("unit") or spec["unit"]
    number = _num(body.get("total_available"))
    used = _num(body.get("total_used"))
    total = _num(body.get("total_granted"))
    if number is None:
        return entry(title, None, unit, "warning", "接口未返回余额", used=used, total=total)
    return entry(title, number, unit, "ok", "余额已更新", used=used, total=total)


# ── 单家查询 ──────────────────────────────────────────────────────────────────
async def _get(
    client: httpx.AsyncClient, spec: Dict[str, Any], key: str, timeout: float = TIMEOUT
) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[str]]:
    """返回 (状态码, JSON body 或 None, 错误说明 或 None)"""
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    if spec.get("user_agent"):
        headers["User-Agent"] = spec["user_agent"]
    try:
        resp = await client.get(spec["url"], headers=headers)
    except httpx.TimeoutException:
        return None, None, f"请求超时（{timeout:g} 秒）"
    except httpx.HTTPError as exc:
        return None, None, f"连接失败（{type(exc).__name__}）"

    if 300 <= resp.status_code < 400:
        return resp.status_code, None, "接口返回重定向，已拒绝"
    if resp.status_code != 200:
        return resp.status_code, None, _http_detail(resp.status_code)
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001 —— 被网关挡住时常返回 HTML 错误页，统一按「不是 JSON」处理
        return resp.status_code, None, "返回内容不是合法 JSON"
    if not isinstance(body, dict):
        return resp.status_code, None, "返回 JSON 顶层不是对象"
    return resp.status_code, body, None


async def fetch_one(
    client: httpx.AsyncClient, spec: Dict[str, Any], key: Optional[str] = None
) -> Dict[str, Any]:
    """查询一家供应商；任何失败都转成一条 error 行，不向外抛异常"""
    title, unit = spec["title"], spec["unit"]
    key = (key or os.environ.get(spec["env"], "")).strip()
    if not key:
        return entry(title, None, unit, "error", f"未配置 {spec['env']}")

    status, body, failure = await _get(client, spec, key)

    # 403（出口 IP 被限制）或直连超时（可能被墙）→ 换本机代理再试一次；
    # 重试用短超时，失败仍保留直连时报出的原因
    if failure or status == 403:
        proxy = _proxy_candidate()
        if proxy:
            retry_timeout = TIMEOUT if status == 403 else RETRY_TIMEOUT
            try:
                async with _client(retry_timeout, proxy) as alt:
                    status2, body2, failure2 = await _get(alt, spec, key, retry_timeout)
                if not failure2 and status2 == 200:
                    status, body, failure = status2, body2, failure2
                    logger.info(f"[ai_balance] {title} 经代理查询成功")
            except Exception as exc:  # noqa: BLE001 —— 代理只是兜底，失败就沿用直连的原因
                logger.debug(f"[ai_balance] {title} 代理重试失败：{exc}")

    if failure:
        return entry(title, None, unit, "error", failure)

    try:
        if spec["kind"] == "credit":
            return _parse_credit(spec, body)
        return _parse_usage(spec, body)
    except Exception as exc:  # noqa: BLE001 —— 解析炸了也只让这一行变红
        return _error_entry(spec, f"解析失败（{type(exc).__name__}）")


async def fetch_all(keys: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """并发查询全部站外供应商，返回统一结构列表（顺序与 PROVIDERS 一致）"""
    keys = keys or {}
    try:
        async with _client(TIMEOUT) as client:
            return list(
                await asyncio.gather(
                    *(fetch_one(client, spec, keys.get(spec["env"])) for spec in PROVIDERS)
                )
            )
    except Exception as exc:  # noqa: BLE001 —— 兜底：整批挂掉也要给出可渲染的行
        logger.warning(f"[ai_balance] 批量查询异常：{type(exc).__name__} {exc}")
        return [_error_entry(spec, f"查询异常（{type(exc).__name__}）") for spec in PROVIDERS]


# ── DeepSeek 转成同一结构（多供应商卡片用） ───────────────────────────────────
def deepseek_entry(data: Dict[str, Any], *, low_threshold: float = 10.0) -> Dict[str, Any]:
    """把 DeepSeek /user/balance 的响应转成统一结构"""
    infos = data.get("balance_infos") or []
    available = bool(data.get("is_available", False))

    total = data.get("total_balance")
    unit = "RMB"
    granted = topped = None
    if infos:
        info = infos[0] or {}
        total = info.get("total_balance", total)
        currency = str(info.get("currency", "CNY")).upper()
        unit = "USD" if currency == "USD" else "RMB"
        granted = _num(info.get("granted_balance"))
        topped = _num(info.get("topped_up_balance"))

    number = _num(total)
    extra = {"granted": granted, "topped_up": topped}

    if not available:
        return entry("DeepSeek", number, unit, "error", "账户不可用", **extra)
    if number is None:
        return entry("DeepSeek", None, unit, "warning", "接口未返回余额", **extra)
    if number < low_threshold:
        return entry("DeepSeek", number, unit, "warning", f"低于预警线 {low_threshold:g}", **extra)
    return entry("DeepSeek", number, unit, "ok", "余额正常", **extra)
