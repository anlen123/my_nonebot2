"""bazaar-cards.com 数据客户端。

站点是纯前端应用，数据由公开接口下发，这里直接读接口、不碰 DOM：

  GET /library-data/v2?hover-tooltips=1   全量卡牌（约 2.3 MB）
  GET /library-data/translations/zh-CN    官方简体中文词条（约 560 KB）
  GET /library-data/cards/<id>            单卡补充信息：冷却、买卖价、附魔

两处关键设计：
  * 卡牌正文是「模板 + 各档位数值」的形式。中文用官方词条按占位符替换，
    数值则把模板与英文正文逐段对齐后精确取出，所以译文和数值都能对上；
  * 官方词条没覆盖的地方一律回落英文原文，绝不臆造译文。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import httpx

logger = logging.getLogger("bazaardb")

SITE = "https://bazaar-cards.com"
CARDS_URL = f"{SITE}/library-data/v2?hover-tooltips=1"
LOCALE_URL = f"{SITE}/library-data/translations/zh-CN"
DETAIL_URL = f"{SITE}/library-data/cards/{{card_id}}"

DATA_TTL = 6 * 3600          # 卡牌库保鲜时长（秒）
RETRY_AFTER = 300            # 刷新失败后的重试间隔（秒）
CACHE_DIR = Path(__file__).parent / "cache"
CACHE_FILE = CACHE_DIR / "library.json"

TIER_ORDER = ("Bronze", "Silver", "Gold", "Diamond", "Legendary")
KIND_LABELS = {"Active": "主动", "Passive": "被动"}
# 站点从 bazaardb.gg 继承来的内部标记（如 "Multicast: 3"），不是给玩家看的效果
_INTERNAL_KIND_PREFIX = "bzdbgg."

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_PLACEHOLDER = re.compile(r"\{[^{}]+\}")


# ── 数据结构 ──────────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Effect:
    """一条技能描述，按档位给出正文。"""

    kind: str
    lines: Tuple[Tuple[str, str], ...]  # ((档位名, 正文), ...)

    @property
    def label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind or "效果")

    @property
    def uniform(self) -> bool:
        """各档位正文是否完全一致 —— 一致就不必逐档位罗列。"""
        return len({text for _, text in self.lines}) <= 1


@dataclass(frozen=True, slots=True)
class Card:
    """一张卡牌。所有面向展示的字段都已翻成中文（无译文时等于英文原文）。"""

    id: str
    name: str
    name_zh: str
    heroes: Tuple[str, ...]
    size: str
    tiers: Tuple[str, ...]        # 原始档位键，用于排序与取值
    tier_labels: Tuple[str, ...]  # 已翻译的档位名
    tags: Tuple[str, ...]
    effects: Tuple[Effect, ...]
    image_url: str

    @property
    def title(self) -> str:
        return self.name if self.name_zh == self.name else f"{self.name_zh}（{self.name}）"

    @property
    def search_terms(self) -> Tuple[str, ...]:
        return self.name.casefold(), self.name_zh.casefold()

# ── 译文与数值 ────────────────────────────────────────────────────────────────
def _placeholder_values(template: str, rendered: str) -> Dict[str, str]:
    """从「模板 + 代入后的正文」里精确取出每个占位符对应的数值。

    rendered 是服务端把 template 的占位符替换成数值后的结果，因此按模板的字面量
    切分、再逐段在 rendered 中定位，就能把数值一一对上。任一段对不上就返回空字典，
    调用方回落到英文原文 —— 宁可显示英文，也不猜一个错的数值。
    """
    literals = _PLACEHOLDER.split(template)
    values: Dict[str, str] = {}
    cursor = 0
    for index, placeholder in enumerate(_PLACEHOLDER.findall(template)):
        literal = literals[index]
        if literal:
            found = rendered.find(literal, cursor)
            if found < 0:
                return {}
            cursor = found + len(literal)
        following = literals[index + 1]
        if not following:
            values[placeholder] = rendered[cursor:].strip()
            break
        stop = rendered.find(following, cursor)
        if stop < 0:
            return {}
        values[placeholder] = rendered[cursor:stop].strip()
        cursor = stop
    return values


def _translate(tooltip: Mapping[str, Any], strings: Mapping[str, str]) -> Dict[str, str]:
    """把一条技能的各档位正文翻成中文，返回 {档位: 正文}。"""
    template = str(tooltip.get("text") or "")
    resolved = tooltip.get("resolved")
    if not isinstance(resolved, Mapping):
        return {}

    translated = strings.get(template)
    texts: Dict[str, str] = {}
    for tier, rendered in resolved.items():
        if not isinstance(rendered, str):
            continue
        text = rendered
        if translated:
            placed = _placeholder_values(template, rendered)
            if placed:
                text = translated
                for placeholder, value in placed.items():
                    text = text.replace(placeholder, value)
            elif not _PLACEHOLDER.search(template):
                text = translated  # 模板本身就没有占位符，直接整句替换
        texts[str(tier)] = text
    return texts


def _label(strings: Mapping[str, str], value: Any) -> str:
    text = str(value or "")
    return strings.get(text, text)


# ── 构建卡牌 ──────────────────────────────────────────────────────────────────
def _build_card(raw: Mapping[str, Any], strings: Mapping[str, str]) -> Optional[Card]:
    card_id = str(raw.get("id") or "")
    name = str(raw.get("name") or "")
    if not card_id or not name:
        return None

    tiers = tuple(t for t in TIER_ORDER if t in (raw.get("availableTiers") or ()))
    if not tiers:  # 少数卡没有档位列表，退回到起始档
        start = raw.get("startingTier")
        tiers = tuple(t for t in TIER_ORDER if t == start) or TIER_ORDER[:1]

    effects: List[Effect] = []
    for tooltip in raw.get("tooltips") or ():
        if not isinstance(tooltip, Mapping):
            continue
        kind = str(tooltip.get("type") or "")
        if kind.startswith(_INTERNAL_KIND_PREFIX):
            continue
        texts = _translate(tooltip, strings)
        order = tiers or tuple(texts)
        lines = tuple((_label(strings, t), texts[t]) for t in order if t in texts)
        if lines:
            effects.append(Effect(kind=kind, lines=lines))

    image_path = str(raw.get("imagePath") or "")
    image_url = f"{SITE}{image_path}" if image_path.startswith("/") else image_path

    return Card(
        id=card_id,
        name=name,
        name_zh=_label(strings, name),
        heroes=tuple(_label(strings, h) for h in (raw.get("heroes") or ())),
        size=_label(strings, raw.get("size")),
        tiers=tiers,
        tier_labels=tuple(_label(strings, t) for t in tiers),
        tags=tuple(_label(strings, t) for t in (raw.get("tags") or ())),
        effects=tuple(effects),
        image_url=image_url,
    )


# ── 数据获取 ──────────────────────────────────────────────────────────────────
def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(connect=8.0, read=90.0, write=20.0, pool=8.0),
        follow_redirects=True,
        trust_env=False,
        headers={"User-Agent": _UA, "Accept": "application/json"},
    )


async def _download() -> Tuple[List[Mapping[str, Any]], Mapping[str, str]]:
    async with _client() as client:
        cards_response, locale_response = await asyncio.gather(
            client.get(CARDS_URL), client.get(LOCALE_URL)
        )
    cards_response.raise_for_status()
    locale_response.raise_for_status()
    cards = (cards_response.json() or {}).get("cards") or []
    strings = (locale_response.json() or {}).get("strings") or {}
    if not cards:
        raise RuntimeError("接口没有返回任何卡牌")
    logger.info(f"[bazaardb] 已拉取卡牌库：{len(cards)} 张卡、{len(strings)} 条中文词条")
    return cards, strings


def _read_cache(max_age: Optional[float]) -> Optional[Tuple[List[Mapping[str, Any]], Mapping[str, str]]]:
    if not CACHE_FILE.exists():
        return None
    if max_age is not None and time.time() - CACHE_FILE.stat().st_mtime > max_age:
        return None
    try:
        saved = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        return saved["cards"], saved["strings"]
    except Exception as exc:  # noqa: BLE001 — 缓存坏了不该拖垮插件，丢掉重拉即可
        logger.warning(f"[bazaardb] 本地卡牌库缓存不可用，将重新拉取：{exc}")
        return None


def _write_cache(cards: List[Mapping[str, Any]], strings: Mapping[str, str]) -> None:
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        CACHE_FILE.write_text(
            json.dumps({"cards": cards, "strings": strings}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001 — 写缓存失败不影响查询
        logger.warning(f"[bazaardb] 写入卡牌库缓存失败：{exc}")


async def fetch_image(url: str) -> Optional[bytes]:
    """下载站点上的图片（卡牌原图）；失败返回 None，调用方最多少一张图。"""
    for attempt in (1, 2):  # 这台机器偶发连接抖动，给一次重试
        try:
            async with _client() as client:
                response = await client.get(url)
                response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"[bazaardb] 取图片失败（第 {attempt} 次）{url}：{type(exc).__name__} {exc}"
            )
            continue
        return response.content or None
    return None


# ── 卡牌库 ────────────────────────────────────────────────────────────────────
class Library:
    """全量卡牌库：内存索引 + 磁盘缓存，过期自动刷新。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._cards: Tuple[Card, ...] = ()
        self._by_id: Dict[str, Card] = {}
        self._expires_at = 0.0

    async def ensure(self) -> None:
        """确保卡牌库可用且不过期。"""
        if self._cards and time.time() < self._expires_at:
            return
        async with self._lock:
            if self._cards and time.time() < self._expires_at:
                return
            try:
                cards, strings = await self._load()
            except Exception as exc:  # noqa: BLE001
                if not self._cards:
                    raise
                # 已有旧数据：先用着，过几分钟再试，别让一次网络抖动清空功能
                logger.warning(f"[bazaardb] 刷新卡牌库失败，继续用旧数据：{exc}")
                self._expires_at = time.time() + RETRY_AFTER
                return
            self._cards = tuple(
                sorted(
                    (card for card in (_build_card(raw, strings) for raw in cards) if card),
                    key=lambda card: card.name.casefold(),
                )
            )
            self._by_id = {card.id: card for card in self._cards}
            self._expires_at = time.time() + DATA_TTL
            logger.info(f"[bazaardb] 卡牌库就绪：{len(self._cards)} 张卡")

    async def _load(self) -> Tuple[List[Mapping[str, Any]], Mapping[str, str]]:
        cached = _read_cache(DATA_TTL)
        if cached is not None:
            logger.info("[bazaardb] 使用本地缓存的卡牌库")
            return cached
        try:
            cards, strings = await _download()
        except Exception as exc:  # noqa: BLE001
            stale = _read_cache(None)
            if stale is None:
                raise
            logger.warning(f"[bazaardb] 联网刷新失败，改用过期的本地缓存：{exc}")
            return stale
        _write_cache(cards, strings)
        return cards, strings

    def search(self, keyword: str, limit: int = 8) -> List[Card]:
        """按卡名（中/英）优先、其次标签与英雄做匹配。"""
        key = keyword.strip().casefold()
        if not key:
            return []
        buckets: Dict[int, List[Card]] = {0: [], 1: [], 2: [], 3: []}
        for card in self._cards:
            names = card.search_terms
            if key in names:
                buckets[0].append(card)
            elif any(name.startswith(key) for name in names):
                buckets[1].append(card)
            elif any(key in name for name in names):
                buckets[2].append(card)
            elif any(key in term for term in card.tags + card.heroes):
                buckets[3].append(card)
        found: List[Card] = []
        for rank in sorted(buckets):
            found.extend(buckets[rank])
            if len(found) >= limit:
                break
        return found[:limit]

    async def detail(self, card_id: str) -> Mapping[str, Any]:
        """取单卡补充信息（冷却、买卖价、附魔）；失败即返回空字典，不影响主流程。"""
        try:
            async with _client() as client:
                response = await client.get(DETAIL_URL.format(card_id=card_id))
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[bazaardb] 取卡片详情失败 id={card_id}：{exc}")
            return {}
        return (payload or {}).get("card") or {}

    def card(self, card_id: str) -> Optional[Card]:
        return self._by_id.get(card_id)


library = Library()
