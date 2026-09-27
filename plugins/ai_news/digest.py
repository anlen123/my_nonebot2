"""
ai_news 早报生成流程：并发抓取 → 历史去重 → 轮流取条 → 渲染表格

对外主入口是 build_digest（返回 PNG 字节 + 条目列表 + 备注）。此外这个模块还管
data/ai_news 下的三样东西：seen.json（历史去重表）、plugin.log（可事后查看的日志）
和 run_now 标记文件（配合插件启动时立即推送）。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import nonebot

from .render import render_error, render_table
from .sources import NewsItem, fetch_source, merge_items

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "ai_news"
SEEN_FILE = DATA_DIR / "seen.json"
LOG_FILE = DATA_DIR / "plugin.log"
# 存在这个文件时，机器人启动后会立即推一条（推完自动删除）
RUN_NOW_FLAG = DATA_DIR / "run_now"
# 记录最近一次「立即推送」的时间，避免多个进程重复推
RUN_NOW_STAMP = DATA_DIR / "run_now.stamp"

# 历史去重表最多留这么多天，写入时顺手清掉更早的记录
SEEN_RETENTION_DAYS = 15


def ensure_data_dir() -> None:
    """确保 data/ai_news 存在（日志、历史表、图片存档都落在这里）"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _item_key(item: NewsItem) -> str:
    """去重用的键：优先链接，没有链接才退回标题"""
    return item.url or item.title


# ── 历史去重（避免连续几天推一样的新闻） ──────────────────────────────────────
def load_seen() -> Dict[str, str]:
    """读 {去重键: YYYY-MM-DD}；文件不存在或内容坏掉都按「没有历史」处理"""
    if not SEEN_FILE.exists():
        return {}
    try:
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # ValueError 覆盖 JSONDecodeError 与 UnicodeDecodeError
        nonebot.logger.warning(f"[ai_news] 历史记录不可用，按无历史处理：{exc}")
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items()}


def save_seen(seen: Dict[str, str]) -> None:
    """回写历史去重表；写不进去只告警，不影响本次推送"""
    try:
        ensure_data_dir()
        SEEN_FILE.write_text(
            json.dumps(seen, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except OSError as exc:
        nonebot.logger.warning(f"[ai_news] 历史记录写入失败：{exc}")


def filter_seen(items: List[NewsItem], days: int) -> Tuple[List[NewsItem], List[NewsItem]]:
    """返回 (最近没发过的, 最近发过的)；days <= 0 表示不做去重"""
    if days <= 0:
        return items, []
    seen = load_seen()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    fresh, old = [], []
    for item in items:
        when = seen.get(_item_key(item))
        if when and when >= cutoff:
            old.append(item)
        else:
            fresh.append(item)
    return fresh, old


def mark_seen(items: List[NewsItem]) -> None:
    """把这次发出去的条目记进历史表，并清掉超过保留期的旧记录"""
    seen = load_seen()
    today = datetime.now().strftime("%Y-%m-%d")
    cutoff = (datetime.now() - timedelta(days=SEEN_RETENTION_DAYS)).strftime("%Y-%m-%d")
    seen = {key: value for key, value in seen.items() if value >= cutoff}
    for item in items:
        seen[_item_key(item)] = today
    save_seen(seen)


def save_digest_png(png: bytes, moment: Optional[datetime] = None) -> Optional[Path]:
    """把生成的表格图存一份到 data/ai_news，方便事后查看"""
    try:
        ensure_data_dir()
        stamp = (moment or datetime.now()).strftime("%Y-%m-%d_%H%M")
        path = DATA_DIR / f"digest_{stamp}.png"
        path.write_bytes(png)
        return path
    except OSError as exc:
        nonebot.logger.warning(f"[ai_news] 图片存档失败：{exc}")
        return None


# ── 抓取 ──────────────────────────────────────────────────────────────────────
def _clients(proxy: str) -> List[httpx.AsyncClient]:
    """候选 client 列表：先代理后直连，代理挂了也能出结果"""
    clients: List[httpx.AsyncClient] = []
    if proxy:
        clients.append(httpx.AsyncClient(proxy=proxy, http2=False, trust_env=False))
    clients.append(httpx.AsyncClient(trust_env=False))
    return clients


async def fetch_all(cfg: Dict[str, Any]) -> Tuple[List[List[NewsItem]], List[str]]:
    """并发抓取全部源，返回 (各源条目, 抓空的源的名字)

    先让所有源都走第一个 client（通常是代理），空手的源再换直连 client 重试一轮，
    这样代理不可用时也能出结果。
    """
    specs = cfg["sources"]
    timeout = cfg["timeout"]
    clients = _clients(cfg["proxy"])
    groups: List[Optional[List[NewsItem]]] = [None] * len(specs)

    async def _one(index: int, spec: Dict[str, Any], client: httpx.AsyncClient) -> None:
        try:
            groups[index] = await fetch_source(client, spec, timeout)
        except Exception as exc:  # noqa: BLE001 —— 单源失败不影响整体，换 client 还能再试
            nonebot.logger.debug(
                f"[ai_news] {spec['name']} 抓取失败({type(exc).__name__})：{exc}"
            )
            groups[index] = []

    try:
        await asyncio.gather(*(_one(i, spec, clients[0]) for i, spec in enumerate(specs)))

        # 空结果的源换直连客户端再试一轮
        retry = [i for i, group in enumerate(groups) if not group]
        if retry and len(clients) > 1:
            await asyncio.gather(*(_one(i, specs[i], clients[1]) for i in retry))
    finally:
        for client in clients:
            await client.aclose()

    errors = [spec["name"] for i, spec in enumerate(specs) if not groups[i]]
    return [group or [] for group in groups], errors


def _fill_missing(
    old_groups: List[List[NewsItem]], picked: List[NewsItem], need: int
) -> List[NewsItem]:
    """从「最近发过的」里再挑 need 条补齐，跳过已经在 picked 里的"""
    chosen = {_item_key(item) for item in picked}
    remaining = [
        [item for item in group if _item_key(item) not in chosen] for group in old_groups
    ]
    return merge_items(remaining, need)


async def build_digest(
    cfg: Dict[str, Any], use_history: bool = True
) -> Tuple[Optional[bytes], List[NewsItem], str]:
    """跑完整流程，返回 (PNG 字节 或 None, 条目列表, 备注/错误文本)

    use_history=True 时按 AI_NEWS_HISTORY_DAYS 去重，并用旧条目补足条数，
    供定时推送使用；手动命令走 use_history=False（每次都看最新的）。
    """
    max_items = max(1, int(cfg["max_items"]))
    groups, failed = await fetch_all(cfg)

    # 交替取之前先去重历史
    fresh_groups: List[List[NewsItem]] = []
    old_groups: List[List[NewsItem]] = []
    for group in groups:
        fresh, old = filter_seen(group, cfg["history_days"]) if use_history else (group, [])
        fresh_groups.append(fresh)
        old_groups.append(old)

    items = merge_items(fresh_groups, max_items)
    if len(items) < max_items:
        # 新内容不够就用旧条目补齐，保证每天都凑够条数
        items.extend(_fill_missing(old_groups, items, max_items - len(items)))

    if not items:
        detail = "；".join(failed) if failed else ""
        text = "所有资讯源都没有抓到内容"
        if detail:
            text += f"\n抓取失败的源：{detail}"
        return None, [], text

    now = datetime.now()
    label = cfg.get("cron_label") or ""
    schedule_text = f"每日 {label} 自动更新" if label else "定时自动更新"
    subtitle = f"共 {len(items)} 条 · " + " / ".join(sorted({item.source for item in items}))
    if len(subtitle) > 74:
        # 来源太多会撑破标题栏，这时只留条数与更新频率
        subtitle = f"共 {len(items)} 条 · {schedule_text}"

    try:
        png = await asyncio.to_thread(
            render_table, items, now, subtitle, bool(cfg["show_links"])
        )
    except Exception as exc:  # noqa: BLE001 —— 渲染失败也要把原因交回调用方
        nonebot.logger.warning(f"[ai_news] 渲染失败：{exc}")
        return None, items, f"表格渲染失败：{type(exc).__name__}: {exc}"

    note = ""
    if failed:
        note = "未取到内容的源：" + "、".join(failed)
    return png, items, note


def render_failure_png(text: str) -> bytes:
    """渲染失败提示图；连兜底图都画不出来时返回空字节（调用方据此只发文字）"""
    try:
        return render_error(text)
    except Exception as exc:  # noqa: BLE001 —— 兜底图失败不该盖掉真正的抓取错误
        nonebot.logger.warning(f"[ai_news] 失败提示图渲染失败：{exc}")
        return b""
