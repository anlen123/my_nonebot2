"""
BazaarDB 爬虫 + 卡片生成器（物品 & 怪物）
用法: python bazaardb_scraper.py [关键词]
      python bazaardb_scraper.py 光纤
      python bazaardb_scraper.py 多尔王
依赖: pip install playwright && playwright install chromium
输出: bazaardb_{key}.json / bazaardb_{key}.html / bazaardb_{key}.png

数据获取策略（无 DOM 爬取）：
  - 用 Playwright 打开任意页面，让 Cloudflare 验证通过
  - 之后用 page.evaluate 发 fetch 请求，带 RSC:1 请求头
  - 服务端直接在响应体中内嵌完整 JSON（Next.js RSC 数据流）
  - 从 RSC 文本中提取 initialData.pageCards，无需解析任何 DOM
  - 物品和怪物分别搜索，合并后生成卡片
"""

import asyncio
import json
import os
import re
import sys
from typing import Optional
from urllib.parse import quote
from playwright.async_api import async_playwright


# ─────────────────────────────────────────────
# 词条颜色映射
# ─────────────────────────────────────────────
ENCHANT_THEME = {
    "黄金":   "enc-golden",
    "沉重":   "enc-slow",
    "寒冰":   "enc-freeze",
    "疾速":   "enc-haste",
    "护盾":   "enc-shield",
    "回复":   "enc-heal",
    "毒素":   "enc-poison",
    "炽焰":   "enc-burn",
    "闪亮":   "enc-charge",
    "致命":   "enc-crit",
    "辉耀":   "enc-radiant",
    "长青":   "enc-evergreen",
}

HIGHLIGHT_RULES = [
    (r"(减速)",       "kw-slow"),
    (r"(冻结)",       "kw-freeze"),
    (r"(加速)",       "kw-haste"),
    (r"(护盾)",       "kw-shield"),
    (r"(治疗)",       "kw-heal"),
    (r"(剧毒)",       "kw-poison"),
    (r"(灼烧)",       "kw-burn"),
    (r"(充能)",       "kw-charge"),
    (r"(暴击率)",     "kw-crit"),
    (r"(伤害)",       "kw-dmg"),
    (r"(生命再生量)", "kw-life"),
    (r"(\d+(?:\.\d+)?%?)", "kw-val"),
]


def highlight(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for pattern, cls in HIGHLIGHT_RULES:
        text = re.sub(pattern, rf'<span class="{cls}">\1</span>', text)
    return text


def enchant_theme(name: str) -> str:
    for key, cls in ENCHANT_THEME.items():
        if name.startswith(key):
            return cls
    return "enc-default"


# ─────────────────────────────────────────────
# RSC 数据提取
# ─────────────────────────────────────────────

# 词条英文名 → 中文名映射
ENCHANT_NAME_MAP = {
    "Golden":      "黄金",
    "Heavy":       "沉重",
    "Icy":         "寒冰",
    "Turbo":       "疾速",
    "Shielded":    "护盾",
    "Restorative": "回复",
    "Toxic":       "毒素",
    "Fiery":       "炽焰",
    "Shiny":       "闪亮",
    "Deadly":      "致命",
    "Radiant":     "辉耀",
    "Obsidian":    "黑曜",
    "Mossy":       "长青",
}

# 英雄英文名 → 中文名映射
HERO_NAME_MAP = {
    "Dooley":    "杜利",
    "Pygmalien": "皮格马利翁",
    "Stelle":    "斯黛尔",
    "Jules":     "朱尔斯",
    "Mak":       "马克",
    "Vanessa":   "凡妮莎",
    "Common":    "",
}

# 品质档位英文 → 中文
TIER_NAME_MAP = {
    "Bronze":  "青铜",
    "Silver":  "白银",
    "Gold":    "黄金",
    "Diamond": "钻石",
    "Legendary": "传奇",
}


def resolve_tooltip_value(replacement_spec: dict, tier: Optional[str] = None) -> str:
    """
    将 TooltipReplacements 中的值解析为字符串。
    spec 可能是 {"Fixed": 1} 或 {"Gold": 1, "Diamond": 2} 等。
    """
    if not replacement_spec:
        return "?"
    if "Fixed" in replacement_spec:
        v = replacement_spec["Fixed"]
        # 毫秒转秒
        if isinstance(v, (int, float)) and v >= 1000 and isinstance(v, int):
            return str(v // 1000)
        return str(v)
    # 按档位取值，优先用传入的 tier，否则取所有档位
    if tier and tier in replacement_spec:
        v = replacement_spec[tier]
        return str(v)
    # 返回所有档位的值，格式：青铜1/白银2/黄金3/钻石4
    parts = []
    for t in ["Bronze", "Silver", "Gold", "Diamond"]:
        if t in replacement_spec:
            parts.append(str(replacement_spec[t]))
    return " » ".join(parts) if parts else "?"


def render_tooltip_text(text: str, replacements: dict, tier: Optional[str] = None) -> str:
    """将 tooltip 模板文本中的占位符替换为实际数值。"""
    for placeholder, spec in replacements.items():
        # 跳过 .targets 类型的占位符（目标数量，通常不显示在描述里）
        if ".targets" in placeholder:
            val = resolve_tooltip_value(spec, tier)
            text = text.replace(placeholder, val)
        else:
            val = resolve_tooltip_value(spec, tier)
            text = text.replace(placeholder, val)
    return text


def parse_item_card(raw: dict) -> dict:  # noqa
    """
    将 RSC 数据流中的原始物品 JSON 转换为渲染所需的结构。
    尽量兼容旧版 scraper 的输出格式。
    """
    name = raw.get("Title", {}).get("Text", "")
    heroes = raw.get("Heroes", [])
    hero_tag = HERO_NAME_MAP.get(heroes[0], heroes[0]) if heroes else ""

    # 档位标签（BaseTier 以上的所有档位）
    base_tier = raw.get("BaseTier", "Silver")
    tier_order = ["Bronze", "Silver", "Gold", "Diamond", "Legendary"]
    base_idx = tier_order.index(base_tier) if base_tier in tier_order else 1
    available_tiers = tier_order[base_idx:]
    type_tags = []
    for t in available_tiers:
        cn = TIER_NAME_MAP.get(t, t)
        type_tags.append(cn + "+")
    # 加上 DisplayTags
    for dt in raw.get("DisplayTags", []):
        type_tags.append(dt)

    # 冷却时间（毫秒 → 秒，按档位）
    base_attrs = raw.get("BaseAttributes", {})
    tiers_data = raw.get("Tiers", {})
    cooldowns = []
    base_cd = base_attrs.get("CooldownMax")
    if base_cd is not None:
        # 收集各档位冷却时间
        cd_vals = []
        for t in available_tiers:
            override = tiers_data.get(t, {}).get("OverrideAttributes", {})
            cd = override.get("CooldownMax", base_cd)
            cd_vals.append(str(cd // 1000))
        # 去重，保留变化
        seen = []
        for v in cd_vals:
            if not seen or seen[-1] != v:
                seen.append(v)
        cooldowns = seen

    # 技能描述（Tooltips）
    tooltips = raw.get("Tooltips", [])
    replacements = raw.get("TooltipReplacements", {})
    if not isinstance(replacements, dict):
        replacements = {}
    active_skills = []
    passive_skills = []
    for tip in tooltips:
        tt = tip.get("TooltipType", "")
        if tt in ("bzdbgg.HiddenSearchable",):
            continue
        text = tip.get("Content", {}).get("Text", "")
        if not text:
            continue
        rendered = render_tooltip_text(text, replacements)
        if tt == "Active":
            active_skills.append(rendered)
        elif tt == "Passive":
            passive_skills.append(rendered)

    # 词条
    enchantments = []
    _enc_raw = raw.get("Enchantments", {})
    if not isinstance(_enc_raw, dict):
        _enc_raw = {}
    for enc_key, enc_data in _enc_raw.items():
        enc_name_cn = ENCHANT_NAME_MAP.get(enc_key, enc_key)
        enc_tips = enc_data.get("Localization", {}).get("Tooltips", [])
        enc_replacements = enc_data.get("TooltipReplacements", {})
        if not isinstance(enc_replacements, dict):
            enc_replacements = {}
        enc_descs = []
        for et in enc_tips:
            et_text = et.get("Content", {}).get("Text", "")
            if et_text:
                enc_descs.append(render_tooltip_text(et_text, enc_replacements))
        enc_desc = " ".join(enc_descs)
        if enc_desc:
            enchantments.append({"name": enc_name_cn, "description": enc_desc})

    img_src = raw.get("Art", "")
    img_blur = raw.get("ArtBlur", "")
    uri = raw.get("Uri", "")
    url = f"https://bazaardb.gg{uri}" if uri else ""

    return {
        "type": "item",
        "name": name,
        "hero_tag": hero_tag,
        "type_tags": type_tags,
        "description": passive_skills[0] if passive_skills else "",
        "enchantments": enchantments,
        "cooldowns": cooldowns,
        "active_skills": active_skills,
        "passive_skills": passive_skills,
        "url": url,
        "img_src": img_src,
        "img_blur": img_blur,
    }


def parse_monster_card(raw: dict) -> dict:
    """
    将 RSC 数据流中的原始怪物 JSON 转换为渲染所需的结构。
    怪物数据在 MonsterMetadata 字段中。
    """
    name = raw.get("Title", {}).get("Text", "")
    meta = raw.get("MonsterMetadata") or {}

    hp = str(meta.get("health", ""))
    available_day = meta.get("available", "")
    day = meta.get("day", "")

    # 品质（tier）从 Tags 中推断
    tags = raw.get("Tags", [])
    tier = ""
    for t in ["Legendary", "Diamond", "Gold", "Silver", "Bronze"]:
        if t in tags:
            tier = TIER_NAME_MAP.get(t, t)
            break

    # level_text 模拟旧格式
    level_text = f"Day {day}+\n{available_day}" if day else available_day

    # 携带物品
    items = []
    for board_item in meta.get("board", []):
        items.append({
            "name": board_item.get("title", ""),
            "img_src": board_item.get("art", ""),
            "img_blur": board_item.get("artBlur", ""),
            "url": f"https://bazaardb.gg{board_item.get('url', '')}",
        })

    img_src = raw.get("Art", "")
    img_blur = raw.get("ArtBlur", "")
    uri = raw.get("Uri", "")
    url = f"https://bazaardb.gg{uri}" if uri else ""

    return {
        "type": "monster",
        "name": name,
        "tier": tier,
        "level_text": level_text,
        "hp": hp,
        "url": url,
        "img_src": img_src,
        "img_blur": img_blur,
        "items": items,
    }


def _extract_json_array(text: str, key: str) -> list[dict]:
    """
    在 text 中找到 '"<key>":[' 并用括号匹配提取完整 JSON 数组。
    """
    search = f'"{key}":['  
    pc_idx = text.find(search)
    if pc_idx < 0:
        return []

    start = text.index("[", pc_idx)
    depth = 0
    end = start
    for i, ch in enumerate(text[start:], start):
        if ch in ("[", "{"):
            depth += 1
        elif ch in ("]", "}"):
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError:
        return []


def extract_page_cards_from_html(html: str, category: str = "items") -> list[dict]:
    """
    从 page.content() 返回的 HTML 中提取卡片数组。

    Next.js App Router 把 Flight 数据放在 <script> 标签里：
        self.__next_f.push([1,"...JSON 转义字符串..."])
    page.content() 返回的 HTML 中，这些字符串里的引号被双重转义为 \\"。
    策略：提取所有 __next_f.push([1,"..."]) 块，逐一解码，
    在解码后的文本里搜索对应字段。

    物品搜索页（category=items）：用 "pageCards" 字段
    怪物搜索页（category=monsters）：用 "cards" 字段（"cards":[{"Id":...）
    注意：怪物搜索页同时包含 pageCards（相关物品）和 cards（怪物），
    必须按 category 选择正确的字段。
    """
    # 提取所有 __next_f.push([1,"..."]) 块的原始字符串
    raw_chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.DOTALL)

    # 按 category 决定搜索顺序
    # "cards" 需要精确匹配 "cards":[{"Id": 避免匹配到 UI 渲染数据里的 tag 描述
    if category == "monsters":
        field_candidates = [
            ("cards",     '"cards":[{"Id":'),
            ("pageCards", '"pageCards":'),
        ]
    else:
        field_candidates = [
            ("pageCards", '"pageCards":'),
            ("cards",     '"cards":[{"Id":'),
        ]

    for field_key, search_pattern in field_candidates:
        for raw_str in raw_chunks:
            try:
                decoded = json.loads('"' + raw_str + '"')
            except json.JSONDecodeError:
                continue

            if search_pattern not in decoded:
                continue

            result = _extract_json_array(decoded, field_key)
            if result:
                return result

    return []


# ─────────────────────────────────────────────
# HTML 构建：物品卡片
# ─────────────────────────────────────────────
def build_item_card(item: dict) -> str:
    enc_cells = ""
    for enc in item["enchantments"]:
        theme = enchant_theme(enc["name"])
        enc_cells += f"""
        <div class="enchant-cell {theme}">
          <div class="enchant-name">{enc['name']}</div>
          <div class="enchant-desc">{highlight(enc['description'])}</div>
        </div>"""

    type_tags_html = ""
    for tag in item["type_tags"]:
        cls = "tag-tier" if "+" in tag else "tag-type"
        type_tags_html += f'<span class="{cls}">{tag}</span>'

    # ── 冷却时间 ──
    cooldowns = item.get("cooldowns", [])
    cd_html = ""
    if cooldowns:
        cd_parts = " » ".join(cooldowns)
        cd_html = f'<div class="item-cooldown"><span class="cd-icon">⏱</span><span class="cd-val">{cd_parts}</span><span class="cd-unit">秒</span></div>'

    # ── 主动技能描述行 ──
    active_skills = item.get("active_skills", [])
    active_html = ""
    for skill in active_skills:
        active_html += f'<div class="skill-line skill-active">{highlight(skill)}</div>'

    # ── 被动技能描述行 ──
    passive_skills = item.get("passive_skills", [])
    passive_html = ""
    for skill in passive_skills:
        passive_html += f'<div class="skill-line skill-passive">{highlight(skill)}</div>'

    skills_html = active_html + passive_html
    if not skills_html:
        desc = item.get("description", "")
        if desc:
            skills_html = f'<div class="skill-line skill-passive">{highlight(desc)}</div>'

    return f"""
  <div class="card item-card">
    <div class="card-header">
      <div class="item-image-wrap">
        <img class="img-blur" src="{item['img_blur']}" alt="">
        <img class="img-main" src="{item['img_src']}" alt="{item['name']}">
      </div>
      <div class="item-meta">
        <div class="item-name-row">
          <span class="item-name">{item['name']}</span>
          {'<span class="hero-tag">' + item['hero_tag'] + '</span>' if item['hero_tag'] else ''}
        </div>
        <div class="type-tags">{type_tags_html}</div>
        {cd_html}
      </div>
      <div class="item-skills">{skills_html}</div>
    </div>
    <div class="enchantments-grid">{enc_cells}
    </div>
  </div>"""


# ─────────────────────────────────────────────
# HTML 构建：怪物卡片
# ─────────────────────────────────────────────
def build_monster_card(monster: dict) -> str:
    level_text = monster.get("level_text", "")
    level_line = ""
    gold_line = ""
    xp_line = ""
    for line in level_text.splitlines():
        line = line.strip()
        if "Level" in line or "Day" in line:
            level_line = line
        elif "Gold" in line:
            gold_line = line
        elif "XP" in line:
            xp_line = line

    tier = monster.get("tier", "")
    tier_color_map = {"黄金": "#d4a820", "白银": "#a0b8c8", "青铜": "#c87840", "钻石": "#60d0f0", "传奇": "#e060e0"}
    tier_color = tier_color_map.get(tier, "#a0a0a0")

    items_html = ""
    for it in monster.get("items", []):
        items_html += f"""
        <div class="monster-item-slot">
          <div class="monster-item-img-wrap">
            <img class="img-blur" src="{it['img_blur']}" alt="">
            <img class="img-main" src="{it['img_src']}" alt="{it['name']}">
          </div>
          <div class="monster-item-name">{it['name']}</div>
        </div>"""

    return f"""
  <div class="card monster-card">
    <div class="monster-header">
      <div class="monster-left">
        <div class="monster-portrait-wrap">
          <img class="img-blur" src="{monster['img_blur']}" alt="">
          <img class="img-main" src="{monster['img_src']}" alt="{monster['name']}">
        </div>
        <div class="monster-meta">
          <div class="monster-name-row">
            <span class="monster-name">{monster['name']}</span>
            {'<span class="tier-tag" style="color:' + tier_color + ';border-color:' + tier_color + '">' + tier + '</span>' if tier else ''}
          </div>
          <div class="monster-level">{level_line}</div>
          <div class="monster-rewards">
            {('<span class="reward-gold">⬡ ' + gold_line + '</span>') if gold_line else ''}
            {('<span class="reward-xp">◆ ' + xp_line + '</span>') if xp_line else ''}
          </div>
        </div>
      </div>
      <div class="monster-right">
        <div class="monster-hp">
          <span class="hp-icon">♥</span>
          <span class="hp-value">{monster['hp']}</span>
        </div>
        <div class="monster-items-row">{items_html}
        </div>
      </div>
    </div>
  </div>"""


# ─────────────────────────────────────────────
# 完整 HTML 页面
# ─────────────────────────────────────────────
def build_html(records: list[dict]) -> str:
    cards_html = ""
    for r in records:
        if r["type"] == "item":
            cards_html += build_item_card(r)
        elif r["type"] == "monster":
            cards_html += build_monster_card(r)

    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<title>BazaarDB 卡片</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #1a1a1a;
    font-family: 'PingFang SC', 'Noto Sans SC', 'Microsoft YaHei', sans-serif;
    padding: 20px;
    display: inline-block;
    min-width: 980px;
  }}

  /* ══ 通用卡片 ══ */
  .card {{
    border-radius: 10px;
    overflow: hidden;
    width: 980px;
    margin-bottom: 20px;
  }}
  .card:last-child {{ margin-bottom: 0; }}

  /* ══ 物品卡片 ══ */
  .item-card {{
    background: #252218;
    border: 1px solid #3a3520;
  }}
  .card-header {{
    display: flex; align-items: center;
    background: #2a2518;
    border-bottom: 1px solid #3a3520;
    padding: 14px 18px; gap: 16px;
  }}
  .item-image-wrap {{
    flex-shrink: 0; width: 72px; height: 72px;
    border-radius: 6px;
    background: linear-gradient(135deg,#5a4a1a,#3a2e10);
    border: 2px solid #c8a84b;
    position: relative; overflow: hidden;
  }}
  .item-image-wrap .img-blur {{
    position: absolute; inset: 0;
    width: 100%; height: 100%;
    object-fit: cover; filter: blur(8px); transform: scale(1.1);
  }}
  .item-image-wrap .img-main {{
    position: relative; z-index: 10;
    width: 100%; height: 100%; object-fit: contain;
  }}
  .item-meta {{ display: flex; flex-direction: column; gap: 7px; min-width: 130px; }}
  .item-name-row {{ display: flex; align-items: center; gap: 10px; }}
  .item-name {{ font-size: 22px; font-weight: 700; color: #f0e6c0; letter-spacing: 0.5px; }}
  .hero-tag {{
    background: #3d2e5a; color: #b89de0;
    border: 1px solid #6a4fa0; border-radius: 4px;
    font-size: 11px; font-weight: 700; padding: 2px 7px; letter-spacing: 1px;
  }}
  .type-tags {{ display: flex; gap: 6px; flex-wrap: wrap; }}
  .tag-tier {{ background:#3a2e08; color:#d4a820; border:1px solid #8a6a10; border-radius:4px; font-size:12px; font-weight:600; padding:2px 9px; }}
  .tag-type {{ background:#1a2e3a; color:#4ab8e0; border:1px solid #2a6a8a; border-radius:4px; font-size:12px; font-weight:600; padding:2px 9px; }}
  .item-desc {{ flex:1; font-size:14px; color:#c8bfa0; line-height:1.6; padding-left:4px; }}

  /* 冷却时间 */
  .item-cooldown {{
    display: inline-flex; align-items: center; gap: 5px;
    background: rgba(60,160,220,0.12);
    border: 1px solid rgba(60,160,220,0.30);
    border-radius: 5px; padding: 3px 10px;
    margin-top: 4px; align-self: flex-start;
  }}
  .cd-icon {{ font-size: 13px; color: #60b8e8; }}
  .cd-val  {{ font-size: 14px; font-weight: 700; color: #80d0f8; letter-spacing: 0.5px; }}
  .cd-unit {{ font-size: 12px; color: #60a0c0; }}

  /* 技能描述区 */
  .item-skills {{
    flex: 1; display: flex; flex-direction: column; gap: 5px;
    padding-left: 4px; justify-content: center;
  }}
  .skill-line {{
    font-size: 13px; line-height: 1.55;
    padding: 4px 10px; border-radius: 5px;
  }}
  .skill-active {{
    background: rgba(220,160,40,0.10);
    border-left: 3px solid #d4a820;
    color: #e0d0a0;
  }}
  .skill-passive {{
    background: rgba(100,160,220,0.08);
    border-left: 3px solid #5090d0;
    color: #c0cce0;
  }}

  /* 词条网格 */
  .enchantments-grid {{ display:grid; grid-template-columns:repeat(6,1fr); }}
  .enchant-cell {{ padding:10px 12px; border-right:1px solid #3a3520; border-bottom:1px solid #3a3520; min-height:78px; }}
  .enchant-cell:nth-child(6n) {{ border-right:none; }}
  .enchant-cell:nth-last-child(-n+6) {{ border-bottom:none; }}
  .enc-golden   {{ background:rgba(200,168,40,.08); }}
  .enc-slow     {{ background:rgba(100,80,180,.10); }}
  .enc-freeze   {{ background:rgba(60,160,220,.10); }}
  .enc-haste    {{ background:rgba(60,200,120,.10); }}
  .enc-shield   {{ background:rgba(80,140,220,.10); }}
  .enc-heal     {{ background:rgba(60,200,100,.10); }}
  .enc-poison   {{ background:rgba(100,200,60,.10); }}
  .enc-burn     {{ background:rgba(220,100,40,.10); }}
  .enc-charge   {{ background:rgba(220,180,40,.10); }}
  .enc-crit     {{ background:rgba(220,60,60,.10); }}
  .enc-radiant  {{ background:rgba(220,200,80,.10); }}
  .enc-obsidian {{ background:rgba(80,60,120,.10); }}
  .enc-evergreen{{ background:rgba(60,180,80,.10); }}
  .enc-default  {{ background:rgba(100,100,100,.08); }}
  .enchant-name {{ font-size:12px; font-weight:700; margin-bottom:5px; }}
  .enc-golden   .enchant-name {{ color:#d4a820; }}
  .enc-slow     .enchant-name {{ color:#9070d0; }}
  .enc-freeze   .enchant-name {{ color:#40b0e0; }}
  .enc-haste    .enchant-name {{ color:#40d080; }}
  .enc-shield   .enchant-name {{ color:#5090e0; }}
  .enc-heal     .enchant-name {{ color:#40c870; }}
  .enc-poison   .enchant-name {{ color:#80c840; }}
  .enc-burn     .enchant-name {{ color:#e06030; }}
  .enc-charge   .enchant-name {{ color:#e0b830; }}
  .enc-crit     .enchant-name {{ color:#e04040; }}
  .enc-radiant  .enchant-name {{ color:#e0c840; }}
  .enc-obsidian .enchant-name {{ color:#a080e0; }}
  .enc-evergreen .enchant-name {{ color:#40c060; }}
  .enc-default  .enchant-name {{ color:#a0a0a0; }}
  .enchant-desc {{ font-size:12px; color:#a09880; line-height:1.55; }}
  .kw-slow     {{ color:#9070d0; font-weight:600; }}
  .kw-freeze   {{ color:#40b0e0; font-weight:600; }}
  .kw-haste    {{ color:#40d080; font-weight:600; }}
  .kw-shield   {{ color:#5090e0; font-weight:600; }}
  .kw-heal     {{ color:#40c870; font-weight:600; }}
  .kw-poison   {{ color:#80c840; font-weight:600; }}
  .kw-burn     {{ color:#e06030; font-weight:600; }}
  .kw-charge   {{ color:#e0b830; font-weight:600; }}
  .kw-crit     {{ color:#e04040; font-weight:600; }}
  .kw-dmg      {{ color:#e04040; font-weight:600; }}
  .kw-life     {{ color:#40c060; font-weight:600; }}
  .kw-val      {{ color:#f0c040; font-weight:700; }}

  /* ══ 怪物卡片 ══ */
  .monster-card {{
    background: #1e2018;
    border: 1px solid #3a4020;
  }}
  .monster-header {{
    display: flex; align-items: center;
    padding: 14px 18px; gap: 20px;
  }}

  .monster-left {{
    display: flex; align-items: center; gap: 14px;
    flex-shrink: 0; min-width: 220px;
  }}
  .monster-portrait-wrap {{
    width: 80px; height: 80px;
    border-radius: 8px;
    border: 2px solid #c8a84b;
    position: relative; overflow: hidden;
    background: #2a2a18;
    flex-shrink: 0;
  }}
  .monster-portrait-wrap .img-blur {{
    position: absolute; inset: 0;
    width: 100%; height: 100%;
    object-fit: cover; filter: blur(6px); transform: scale(1.15);
  }}
  .monster-portrait-wrap .img-main {{
    position: relative; z-index: 10;
    width: 100%; height: 100%; object-fit: cover;
  }}
  .monster-meta {{ display: flex; flex-direction: column; gap: 5px; }}
  .monster-name-row {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
  .monster-name {{ font-size: 20px; font-weight: 700; color: #f0e6c0; }}
  .tier-tag {{
    font-size: 11px; font-weight: 700; padding: 2px 8px;
    border-radius: 4px; border: 1px solid; background: rgba(255,255,255,0.05);
  }}
  .monster-level {{ font-size: 12px; color: #888; }}
  .monster-rewards {{ display: flex; gap: 12px; margin-top: 2px; }}
  .reward-gold {{ font-size: 12px; font-weight: 700; color: #d4a820; }}
  .reward-xp   {{ font-size: 12px; font-weight: 700; color: #60a8e0; }}

  .monster-right {{
    flex: 1; display: flex; flex-direction: column; gap: 10px; justify-content: center;
  }}
  .monster-hp {{
    display: inline-flex; align-items: center; gap: 6px;
    background: rgba(60,200,100,0.12);
    border: 1px solid rgba(60,200,100,0.25);
    border-radius: 6px; padding: 4px 14px;
    align-self: flex-start;
  }}
  .hp-icon {{ color: #40c870; font-size: 14px; }}
  .hp-value {{ font-size: 16px; font-weight: 700; color: #40c870; }}

  .monster-items-row {{
    display: flex; gap: 10px; flex-wrap: wrap; align-items: flex-start;
  }}
  .monster-item-slot {{
    display: flex; flex-direction: column; align-items: center; gap: 4px;
    width: 64px;
  }}
  .monster-item-img-wrap {{
    width: 56px; height: 56px;
    border-radius: 6px;
    border: 2px solid #c8a84b;
    position: relative; overflow: hidden;
    background: #2a2a18;
  }}
  .monster-item-img-wrap .img-blur {{
    position: absolute; inset: 0;
    width: 100%; height: 100%;
    object-fit: cover; filter: blur(6px); transform: scale(1.1);
  }}
  .monster-item-img-wrap .img-main {{
    position: relative; z-index: 10;
    width: 100%; height: 100%; object-fit: contain;
  }}
  .monster-item-name {{
    font-size: 10px; color: #999; text-align: center;
    line-height: 1.3; max-width: 64px;
    overflow: hidden; display: -webkit-box;
    -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  }}
</style>
</head>
<body>
{cards_html}
</body>
</html>"""


# ─────────────────────────────────────────────
# 核心：拦截初始 HTML 响应获取数据
# ─────────────────────────────────────────────
async def fetch_via_page_content(context, category: str, query: str) -> list[dict]:
    """
    新建一个页面，导航到搜索 URL，等待 domcontentloaded 后用 page.content() 获取 HTML。
    Next.js App Router 会将完整的 pageCards JSON 内嵌在 <script> 标签（Flight 数据）里，
    page.content() 可以直接读取，无需解析 DOM，也不需要等待 JS 渲染。
    Cloudflare 对浏览器发出的页面请求放行。
    """
    encoded_q = quote(query)
    url = f"https://bazaardb.gg/search?c={category}&q={encoded_q}"
    label = "物品" if category == "items" else "怪物"
    print(f"  → 搜索{label}: {url}")

    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        # 等待 Next.js Flight 数据注入完成（通常在 domcontentloaded 后很快）
        # 如果 pageCards 还没出现，稍等一下
        html = await page.content()
        if "pageCards" not in html:
            await page.wait_for_timeout(1500)
            html = await page.content()
    except Exception:
        html = await page.content()
    finally:
        await page.close()

    cards_raw = extract_page_cards_from_html(html, category)
    print(f"    ✓ 找到 {len(cards_raw)} 个{label}原始记录")
    return cards_raw


# ─────────────────────────────────────────────
# 爬取 + 导出
# ─────────────────────────────────────────────
async def scrape_and_export(key: str, out_dir: str = "."):
    safe_key = key.replace("/", "_").replace("\\", "_")
    json_path = os.path.join(out_dir, f"bazaardb_{safe_key}.json")
    html_path = os.path.join(out_dir, f"bazaardb_{safe_key}.html")
    png_path  = os.path.join(out_dir, f"bazaardb_{safe_key}.png")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="zh-CN",
            viewport={"width": 1280, "height": 900},
        )

        # ── 步骤 1 & 2：并行获取物品和怪物数据 ──
        # 数据内嵌在 Next.js Flight <script> 标签里，page.content() 直接读取
        print("[1/3] 获取数据（物品 + 怪物并行）...")
        items_task    = asyncio.create_task(fetch_via_page_content(context, "items",    key))
        monsters_task = asyncio.create_task(fetch_via_page_content(context, "monsters", key))
        items_raw, monsters_raw = await asyncio.gather(items_task, monsters_task)

        # ── 步骤 3：转换数据结构 ──
        all_records = []

        MONSTER_TYPES = {"Monster", "CombatEncounter"}

        for raw in items_raw:
            card_type = raw.get("Type", "")
            monster_meta = raw.get("MonsterMetadata")
            if card_type in MONSTER_TYPES or (monster_meta and monster_meta != "$undefined"):
                parsed = parse_monster_card(raw)
            else:
                parsed = parse_item_card(raw)
                # 过滤掉没有词条也没有描述的空物品
                if not parsed["enchantments"] and not parsed["description"] and not parsed["active_skills"]:
                    continue
            all_records.append(parsed)

        for raw in monsters_raw:
            card_type = raw.get("Type", "")
            monster_meta = raw.get("MonsterMetadata")
            if card_type in MONSTER_TYPES or (monster_meta and monster_meta != "$undefined"):
                parsed = parse_monster_card(raw)
                all_records.append(parsed)

        if not all_records:
            print("⚠ 未找到任何结果。")
            await browser.close()
            return

        # ── 步骤 4：保存 JSON ──
        print(f"\n[2/3] 生成 JSON → {json_path}")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(all_records, f, ensure_ascii=False, indent=2)

        # ── 步骤 5：生成 HTML + 截图 ──
        print(f"[3/3] 生成 HTML + PNG → {html_path} / {png_path}")
        html_content = build_html(all_records)
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        card_page = await context.new_page()
        await card_page.goto(f"file://{os.path.abspath(html_path)}", wait_until="domcontentloaded")
        try:
            await card_page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:
            pass
        await card_page.wait_for_timeout(800)

        size = await card_page.evaluate("({w: document.body.scrollWidth, h: document.body.scrollHeight})")
        await card_page.set_viewport_size({"width": size["w"] + 40, "height": size["h"] + 40})
        await card_page.screenshot(path=png_path, full_page=True)
        await card_page.close()
        await browser.close()

    n_items    = len([r for r in all_records if r["type"] == "item"])
    n_monsters = len([r for r in all_records if r["type"] == "monster"])
    print(f"\n✅ 完成！")
    print(f"   JSON : {json_path}")
    print(f"   HTML : {html_path}")
    print(f"   PNG  : {png_path}")
    print(f"   共 {n_items} 个物品，{n_monsters} 个怪物")


async def main():
    key = sys.argv[1] if len(sys.argv) > 1 else "光纤"
    out_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(os.path.abspath(__file__))
    print(f"搜索关键词: {key}")
    await scrape_and_export(key, out_dir)


if __name__ == "__main__":
    asyncio.run(main())
