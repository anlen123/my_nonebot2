"""把卡牌渲染成 QQ 里好读的中文文案。"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

from .cards import KIND_LABELS, Card

_KIND_ICONS = {"Active": "⚡", "Passive": "🛡️"}


def _kinds(card: Card) -> List[str]:
    """卡牌实际出现的技能类别，常见顺序优先，其余按出现顺序补在后面。"""
    present = list(dict.fromkeys(effect.kind for effect in card.effects))
    return [kind for kind in ("Active", "Passive") if kind in present] + [
        kind for kind in present if kind not in ("Active", "Passive")
    ]


def _effect_lines(card: Card, kind: str) -> List[str]:
    """同类效果按档位合并，同一档位的多条正文用「・」连接。"""
    per_tier: Dict[str, List[str]] = {}
    for effect in card.effects:
        if effect.kind != kind:
            continue
        for label, text in effect.lines:
            per_tier.setdefault(label, []).append(text)
    if not per_tier:
        return []

    distinct = {tuple(texts) for texts in per_tier.values()}
    if len(distinct) == 1:  # 各档位完全一致，收成一行免得刷屏
        return ["　" + "・".join(next(iter(distinct)))]

    order = [label for label in card.tier_labels if label in per_tier] or list(per_tier)
    return [f"　{label}｜" + "・".join(per_tier[label]) for label in order]


def _economy(card: Card, detail: Mapping[str, Any]) -> str:
    """冷却与买卖价（取自单卡详情接口）；缺哪项就不显示哪项。"""
    tiers = detail.get("tiers") or {}
    attributes = [
        tiers[tier].get("attributes") or {}
        for tier in card.tiers
        if isinstance(tiers.get(tier), Mapping)
    ]
    if not attributes:
        return ""

    def series(key: str) -> List[Any]:
        return [attribute.get(key) for attribute in attributes]

    def collapse(values: List[Any]) -> str:
        shown = ["?" if value is None else f"{value:g}" for value in values]
        return "→".join(dict.fromkeys(shown))  # 各档位相同就只留一个值

    parts: List[str] = []
    cooldowns = [value for value in series("cooldownMax") if value]
    if cooldowns and len(set(cooldowns)) == 1:
        parts.append(f"⏱️ 冷却 {cooldowns[0] / 1000:g} 秒")
    for key, icon, name in (("buyPrice", "💰", "买"), ("sellPrice", "🪙", "卖")):
        values = series(key)
        if any(value is not None for value in values):
            parts.append(f"{icon} {name} {collapse(values)}")
    return "　".join(parts)


def format_card(card: Card, detail: Mapping[str, Any] | None = None) -> str:
    """单卡详情文案。"""
    lines = [f"🃏 {card.title}"]

    meta = []
    if card.heroes:
        meta.append("🎭 " + "／".join(card.heroes))
    if card.size:
        meta.append("📦 " + card.size)
    if meta:
        lines.append("　".join(meta))
    if card.tier_labels:
        lines.append("🎖️ " + " → ".join(card.tier_labels))
    if card.tags:
        lines.append("🏷️ " + "・".join(card.tags))

    economy = _economy(card, detail or {})
    if economy:
        lines.append(economy)

    for kind in _kinds(card):
        body = _effect_lines(card, kind)
        if body:
            lines.append(f"{_KIND_ICONS.get(kind, '▫️')} {KIND_LABELS.get(kind, kind or '效果')}")
            lines.extend(body)

    return "\n".join(lines)
