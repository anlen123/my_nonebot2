"""deepseek_balance 的余额快照 —— 支撑「与上次查询做对比」

快照文件默认 <项目根>/data/deepseek_balance/balance_history.json（运行数据，不入版本库），
路径可用 .env 的 DEEPSEEK_BALANCE_HISTORY 覆盖。

文件结构：
    {
      "updated_at": 1759000000.0,        # 上次写入快照的时间，即「上次查询」
      "source": "定时推送",               # 上次查询的来源
      "accounts": {
        "DeepSeek|RMB": {
          "title": "DeepSeek", "unit": "RMB",
          "balance": 12.34, "at": 1759000000.0
        }
      }
    }

设计要点：
  * 每个账户按「名称|币种」记录：接口增减账户、供应商改名都不会串行
  * 本轮没取到数值（查询失败 / 接口不给余额）时保留旧记录，下次成功仍拿得到基准
  * 写入用「临时文件 + 原子替换」，中断也不会留下半个 JSON
  * 读取出任何问题都当成「没有历史」，对比功能降级，绝不影响余额查询本身
"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import nonebot

logger = nonebot.logger


# ── 快照文件读写 ──────────────────────────────────────────────────────────────
def account_key(item: Dict[str, Any]) -> str:
    """一行的身份键：名称 + 币种（同名但不同币种的两个账户不会互相覆盖）"""
    return f"{item.get('title', '?')}|{item.get('unit', '?')}"


def _number(value: Any) -> Optional[float]:
    """转成有限浮点数；bool / None / 非数字 / NaN / inf 一律返回 None"""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def load_snapshot(path: Path) -> Dict[str, Any]:
    """读取上次快照；文件不存在或内容坏了都返回空表（相当于首次查询）"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001 —— 坏 JSON / 权限 / 编码问题统一按「没有历史」处理
        logger.warning(
            f"[ai_balance] 余额快照读取失败（{type(exc).__name__} {exc}），本次不做对比"
        )
        return {}
    if not isinstance(raw, dict) or not isinstance(raw.get("accounts"), dict):
        return {}
    return raw


def save_snapshot(path: Path, snapshot: Dict[str, Any]) -> None:
    """原子写入快照：先写同目录的临时文件再替换，避免中断留下半截 JSON"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ── 对比与记录 ────────────────────────────────────────────────────────────────
def annotate(entries: List[Dict[str, Any]], snapshot: Dict[str, Any]) -> None:
    """把「与上次余额的差值」写进每行的 extra（原地修改，卡片层据此画增减）

    只有「本轮有数值 + 上轮同一账户也有数值」才算得出差值；其余情况（首次查询、
    本轮失败、上轮没取到）都不打标记，卡片上显示为「首次查询」。

    写入的键：
      prev_balance  上次余额
      prev_at       上次该数值的采集时间（比整卡基准旧时，卡片会写明用的是哪一次）
      delta         差值（正=变多，负=变少）
      delta_pct     变化百分比（上次余额为 0 时为空，避免除以零）
    """
    accounts = (snapshot or {}).get("accounts") or {}
    for item in entries:
        if item.get("status") == "error":
            continue
        current = _number(item.get("balance"))
        if current is None:
            continue
        previous = accounts.get(account_key(item))
        if not isinstance(previous, dict):
            continue
        before = _number(previous.get("balance"))
        if before is None:
            continue

        delta = current - before
        extra = item.setdefault("extra", {})
        extra["prev_balance"] = before
        at = _number(previous.get("at"))
        if at is not None:
            extra["prev_at"] = at
        extra["delta"] = delta
        extra["delta_pct"] = (delta / before * 100.0) if before > 0 else None


def make_snapshot(
    entries: List[Dict[str, Any]],
    snapshot: Dict[str, Any],
    *,
    source: str,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """生成要落盘的新快照：本轮取到数值的行覆盖旧记录，没取到的沿用旧记录"""
    stamp = time.time() if now is None else now
    old = (snapshot or {}).get("accounts")
    accounts: Dict[str, Any] = dict(old) if isinstance(old, dict) else {}

    for item in entries:
        number = _number(item.get("balance"))
        if number is None:
            # 查询失败/无余额的行不覆盖基准，保留上一次的记录供下轮对比
            continue
        accounts[account_key(item)] = {
            "title": item.get("title"),
            "unit": item.get("unit"),
            "balance": round(number, 4),
            "at": stamp,
        }
    return {"updated_at": stamp, "source": source, "accounts": accounts}
