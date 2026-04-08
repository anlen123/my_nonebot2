"""
bazaardb -- BazaarDB 物品 & 怪物查询插件

用法：bz <关键词>
示例：bz 光纤
      bz 多尔王

爬取 bazaardb.gg 搜索结果（物品 + 怪物双搜索），渲染为卡片图片后发送到 QQ。
"""

import asyncio
import base64
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import quote

import nonebot
from nonebot.plugin import on_regex
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment

# 缓存目录：插件同级的 cache/ 文件夹
CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

bz = on_regex(pattern=r"^bz ")


def _cache_path(keyword: str) -> Path:
    safe = keyword.replace("/", "_").replace("\\", "_").replace(" ", "_")
    return CACHE_DIR / f"{safe}.png"


@bz.handle()
async def bz_rev(bot: Bot, event: Event):
    keyword = str(event.message).strip()[3:].strip()
    if not keyword:
        await bot.send(event=event, message=MessageSegment.text("请输入关键词，例如：bz 光纤"))
        return

    # 命中缓存直接发送
    cache_file = _cache_path(keyword)
    if cache_file.exists():
        nonebot.logger.info(f"[bazaardb] 命中缓存 keyword={keyword}")
        img_b64 = base64.b64encode(cache_file.read_bytes()).decode()
        await bot.send(event=event, message=MessageSegment.image(f"base64://{img_b64}"))
        return

    await bot.send(event=event, message=MessageSegment.text(f"正在查询「{keyword}」，请稍候..."))

    try:
        img_bytes = await asyncio.get_event_loop().run_in_executor(
            None, _query_sync, keyword
        )
    except Exception as e:
        nonebot.logger.warning(f"[bazaardb] 查询失败 keyword={keyword}: {e}")
        await bot.send(event=event, message=MessageSegment.text(f"查询失败：{e}"))
        return

    if img_bytes is None:
        await bot.send(event=event, message=MessageSegment.text(f"未找到「{keyword}」相关物品或怪物"))
        return

    # 写入缓存
    cache_file.write_bytes(img_bytes)
    nonebot.logger.info(f"[bazaardb] 已缓存 keyword={keyword} -> {cache_file}")

    img_b64 = base64.b64encode(img_bytes).decode()
    await bot.send(event=event, message=MessageSegment.image(f"base64://{img_b64}"))


# ── 词条颜色映射 ──────────────────────────────────────────────────────────────
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


def _highlight(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for pattern, cls in HIGHLIGHT_RULES:
        text = re.sub(pattern, rf'<span class="{cls}">\1</span>', text)
    return text


def _enchant_theme(name: str) -> str:
    for key, cls in ENCHANT_THEME.items():
        if name.startswith(key):
            return cls
    return "enc-default"

# ── JS 提取逻辑 ───────────────────────────────────────────────────────────────
JS_EXTRACT_ITEMS = """
() => {
    const results = [];
    const previewBlocks = document.querySelectorAll("._ag");
    for (const block of previewBlocks) {
        const cardLink = block.querySelector("a[href*='/card/']");
        if (!cardLink) continue;

        const nameEl = block.querySelector("._ak span");
        const name = nameEl ? nameEl.textContent.trim() : "";

        const heroTagEl = block.querySelector("a[href*='t%3A'] div span, a[href*='t%3A'] div");
        const heroTag = heroTagEl ? heroTagEl.textContent.trim() : "";

        const typeTagEls = block.querySelectorAll("._di, ._ds span");
        const typeTags = Array.from(typeTagEls)
            .map(el => el.textContent.trim())
            .filter(t => t && t !== heroTag);

        const activeBs = block.querySelector("._bS");
        let cooldowns = [];
        let active_skills = [];
        if (activeBs) {
            const cdEls = activeBs.querySelectorAll("._bW ._cb");
            cooldowns = Array.from(cdEls).map(el => el.textContent.trim()).filter(Boolean);

            const activeBtEls = activeBs.querySelectorAll("._bT:not(._bV)");
            for (const bt of activeBtEls) {
                const chEls = bt.querySelectorAll("._ch");
                for (const ch of chEls) {
                    const clEl = ch.querySelector("._cL");
                    if (clEl) {
                        const txt = clEl.innerText.trim();
                        if (txt) active_skills.push(txt);
                    }
                }
            }
        }

        const passiveBtEls = block.querySelectorAll("._bT._bV");
        let passive_skills = [];
        for (const bt of passiveBtEls) {
            const chEls = bt.querySelectorAll("._ch");
            for (const ch of chEls) {
                const clEl = ch.querySelector("._cL");
                if (clEl) {
                    const txt = clEl.innerText.trim();
                    if (txt) passive_skills.push(txt);
                }
            }
            if (passive_skills.length === 0) {
                const clEl = bt.querySelector("._cL");
                if (clEl) {
                    const txt = clEl.innerText.trim();
                    if (txt) passive_skills.push(txt);
                }
            }
        }

        const description = passive_skills[0] || "";

        const enchantBlocks = block.querySelectorAll("._bT._bU");
        const enchantments = [];
        for (const enc of enchantBlocks) {
            const encNameEl = enc.querySelector("._bW span");
            const encDescEl = enc.querySelector("._cL");
            if (encNameEl && encDescEl) {
                enchantments.push({
                    name: encNameEl.textContent.trim(),
                    description: encDescEl.innerText.trim()
                });
            }
        }

        const imgs = block.querySelectorAll('img');
        const imgBlur = imgs[0] ? imgs[0].src : '';
        const imgSrc  = imgs[1] ? imgs[1].src : (imgs[0] ? imgs[0].src : '');

        results.push({
            type: "item",
            name, hero_tag: heroTag, type_tags: typeTags,
            description, enchantments,
            cooldowns, active_skills, passive_skills,
            url: cardLink.href, img_blur: imgBlur, img_src: imgSrc
        });
    }
    return results;
}
"""

JS_EXTRACT_MONSTERS = """
() => {
    const results = [];
    const monsterCards = document.querySelectorAll("._bD._bE");
    for (const card of monsterCards) {
        const cardLink = card.querySelector("a[href*='/card/']");
        if (!cardLink) continue;

        const nameEl = card.querySelector("._ak span");
        const name = nameEl ? nameEl.textContent.trim() : "";

        const crEl = card.querySelector("._cr");
        const levelText = crEl ? crEl.innerText.trim() : "";

        const hpEl = card.querySelector("._ai ._a");
        const hp = hpEl ? hpEl.innerText.trim().replace(/[^0-9]/g, "") : "";

        const tierEl = card.querySelector("._di");
        const tier = tierEl ? tierEl.textContent.trim() : "";

        const mainImgEl = card.querySelector("._ac");
        const imgSrc = mainImgEl ? mainImgEl.src : "";
        const blurImgEl = card.querySelector("._ab img");
        const blurBgEl  = card.querySelector("._ab[style*='background-image']");
        let imgBlur = "";
        if (blurImgEl) {
            imgBlur = blurImgEl.src;
        } else if (blurBgEl) {
            const bg = blurBgEl.style.backgroundImage;
            imgBlur = bg.replace(/^url\(["'"]?/, "").replace(/["'"]?\)$/, "");
        }

        const itemLinks = card.querySelectorAll("._ai a[href*='/card/']");
        const items = [];
        for (const a of itemLinks) {
            const label = a.getAttribute("aria-label") || "";
            const itemName = label.replace("See details for ", "");
            const itemImgs = a.querySelectorAll("img");
            const itemBlur = itemImgs[0] ? itemImgs[0].src : "";
            const itemSrc  = itemImgs[1] ? itemImgs[1].src : (itemImgs[0] ? itemImgs[0].src : "");
            items.push({ name: itemName, img_src: itemSrc, img_blur: itemBlur, url: a.href });
        }

        results.push({
            type: "monster",
            name, tier, level_text: levelText, hp,
            url: cardLink.href, img_src: imgSrc, img_blur: imgBlur,
            items
        });
    }
    return results;
}
"""

# ── HTML 构建：物品卡片 ──────────────────────────────────────────────────────
def _build_item_card(item: dict) -> str:
    enc_cells = ""
    for enc in item["enchantments"]:
        theme = _enchant_theme(enc["name"])
        enc_cells += f"""
        <div class="enchant-cell {theme}">
          <div class="enchant-name">{enc['name']}</div>
          <div class="enchant-desc">{_highlight(enc['description'])}</div>
        </div>"""

    type_tags_html = ""
    for tag in item["type_tags"]:
        cls = "tag-tier" if "+" in tag else "tag-type"
        type_tags_html += f'<span class="{cls}">{tag}</span>'

    cooldowns = item.get("cooldowns", [])
    cd_html = ""
    if cooldowns:
        cd_parts = " » ".join(cooldowns)
        cd_html = f'<div class="item-cooldown"><span class="cd-icon">&#x23F1;</span><span class="cd-val">{cd_parts}</span><span class="cd-unit">秒</span></div>'

    active_skills = item.get("active_skills", [])
    active_html = ""
    for skill in active_skills:
        active_html += f'<div class="skill-line skill-active">{_highlight(skill)}</div>'

    passive_skills = item.get("passive_skills", [])
    passive_html = ""
    for skill in passive_skills:
        passive_html += f'<div class="skill-line skill-passive">{_highlight(skill)}</div>'

    skills_html = active_html + passive_html
    if not skills_html:
        desc = item.get("description", "")
        if desc:
            skills_html = f'<div class="skill-line skill-passive">{_highlight(desc)}</div>'

    hero_tag_html = f'<span class="hero-tag">{item["hero_tag"]}</span>' if item["hero_tag"] else ""

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
          {hero_tag_html}
        </div>
        <div class="type-tags">{type_tags_html}</div>
        {cd_html}
      </div>
      <div class="item-skills">{skills_html}</div>
    </div>
    <div class="enchantments-grid">{enc_cells}
    </div>
  </div>"""


# ── HTML 构建：怪物卡片 ──────────────────────────────────────────────────────
def _build_monster_card(monster: dict) -> str:
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
    tier_color_map = {
        "黄金": "#d4a820", "白银": "#a0b8c8", "青铜": "#c87840",
        "钻石": "#60d0f0", "传奇": "#e060e0",
    }
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

    tier_tag_html = (
        f'<span class="tier-tag" style="color:{tier_color};border-color:{tier_color}">{tier}</span>'
        if tier else ""
    )
    gold_html = f'<span class="reward-gold">&#x2B21; {gold_line}</span>' if gold_line else ""
    xp_html   = f'<span class="reward-xp">&#x25C6; {xp_line}</span>' if xp_line else ""

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
            {tier_tag_html}
          </div>
          <div class="monster-level">{level_line}</div>
          <div class="monster-rewards">{gold_html}{xp_html}</div>
        </div>
      </div>
      <div class="monster-right">
        <div class="monster-hp">
          <span class="hp-icon">&#x2665;</span>
          <span class="hp-value">{monster['hp']}</span>
        </div>
        <div class="monster-items-row">{items_html}
        </div>
      </div>
    </div>
  </div>"""

# ── 完整 HTML 页面 ────────────────────────────────────────────────────────────
def _build_html(records: list) -> str:
    cards_html = ""
    for r in records:
        if r["type"] == "item":
            cards_html += _build_item_card(r)
        elif r["type"] == "monster":
            cards_html += _build_monster_card(r)

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
  .card {{
    border-radius: 10px; overflow: hidden;
    width: 980px; margin-bottom: 20px;
  }}
  .card:last-child {{ margin-bottom: 0; }}

  /* 物品卡片 */
  .item-card {{ background: #252218; border: 1px solid #3a3520; }}
  .card-header {{
    display: flex; align-items: center;
    background: #2a2518; border-bottom: 1px solid #3a3520;
    padding: 14px 18px; gap: 16px;
  }}
  .item-image-wrap {{
    flex-shrink: 0; width: 72px; height: 72px;
    border-radius: 6px;
    background: linear-gradient(135deg,#5a4a1a,#3a2e10);
    border: 2px solid #c8a84b; position: relative; overflow: hidden;
  }}
  .item-image-wrap .img-blur {{
    position: absolute; inset: 0; width: 100%; height: 100%;
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
  .item-cooldown {{
    display: inline-flex; align-items: center; gap: 5px;
    background: rgba(60,160,220,0.12); border: 1px solid rgba(60,160,220,0.30);
    border-radius: 5px; padding: 3px 10px; margin-top: 4px; align-self: flex-start;
  }}
  .cd-icon {{ font-size: 13px; color: #60b8e8; }}
  .cd-val  {{ font-size: 14px; font-weight: 700; color: #80d0f8; letter-spacing: 0.5px; }}
  .cd-unit {{ font-size: 12px; color: #60a0c0; }}
  .item-skills {{
    flex: 1; display: flex; flex-direction: column; gap: 5px;
    padding-left: 4px; justify-content: center;
  }}
  .skill-line {{ font-size: 13px; line-height: 1.55; padding: 4px 10px; border-radius: 5px; }}
  .skill-active {{ background: rgba(220,160,40,0.10); border-left: 3px solid #d4a820; color: #e0d0a0; }}
  .skill-passive {{ background: rgba(100,160,220,0.08); border-left: 3px solid #5090d0; color: #c0cce0; }}

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
  .kw-slow   {{ color:#9070d0; font-weight:600; }}
  .kw-freeze {{ color:#40b0e0; font-weight:600; }}
  .kw-haste  {{ color:#40d080; font-weight:600; }}
  .kw-shield {{ color:#5090e0; font-weight:600; }}
  .kw-heal   {{ color:#40c870; font-weight:600; }}
  .kw-poison {{ color:#80c840; font-weight:600; }}
  .kw-burn   {{ color:#e06030; font-weight:600; }}
  .kw-charge {{ color:#e0b830; font-weight:600; }}
  .kw-crit   {{ color:#e04040; font-weight:600; }}
  .kw-dmg    {{ color:#e04040; font-weight:600; }}
  .kw-life   {{ color:#40c060; font-weight:600; }}
  .kw-val    {{ color:#f0c040; font-weight:700; }}

  /* 怪物卡片 */
  .monster-card {{ background: #1e2018; border: 1px solid #3a4020; }}
  .monster-header {{ display: flex; align-items: center; padding: 14px 18px; gap: 20px; }}
  .monster-left {{ display: flex; align-items: center; gap: 14px; flex-shrink: 0; min-width: 220px; }}
  .monster-portrait-wrap {{
    width: 80px; height: 80px; border-radius: 8px; border: 2px solid #c8a84b;
    position: relative; overflow: hidden; background: #2a2a18; flex-shrink: 0;
  }}
  .monster-portrait-wrap .img-blur {{
    position: absolute; inset: 0; width: 100%; height: 100%;
    object-fit: cover; filter: blur(6px); transform: scale(1.15);
  }}
  .monster-portrait-wrap .img-main {{
    position: relative; z-index: 10; width: 100%; height: 100%; object-fit: cover;
  }}
  .monster-meta {{ display: flex; flex-direction: column; gap: 5px; }}
  .monster-name-row {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
  .monster-name {{ font-size: 20px; font-weight: 700; color: #f0e6c0; }}
  .tier-tag {{ font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 4px; border: 1px solid; background: rgba(255,255,255,0.05); }}
  .monster-level {{ font-size: 12px; color: #888; }}
  .monster-rewards {{ display: flex; gap: 12px; margin-top: 2px; }}
  .reward-gold {{ font-size: 12px; font-weight: 700; color: #d4a820; }}
  .reward-xp   {{ font-size: 12px; font-weight: 700; color: #60a8e0; }}
  .monster-right {{ flex: 1; display: flex; flex-direction: column; gap: 10px; justify-content: center; }}
  .monster-hp {{
    display: inline-flex; align-items: center; gap: 6px;
    background: rgba(60,200,100,0.12); border: 1px solid rgba(60,200,100,0.25);
    border-radius: 6px; padding: 4px 14px; align-self: flex-start;
  }}
  .hp-icon {{ color: #40c870; font-size: 14px; }}
  .hp-value {{ font-size: 16px; font-weight: 700; color: #40c870; }}
  .monster-items-row {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: flex-start; }}
  .monster-item-slot {{ display: flex; flex-direction: column; align-items: center; gap: 4px; width: 64px; }}
  .monster-item-img-wrap {{
    width: 56px; height: 56px; border-radius: 6px; border: 2px solid #c8a84b;
    position: relative; overflow: hidden; background: #2a2a18;
  }}
  .monster-item-img-wrap .img-blur {{
    position: absolute; inset: 0; width: 100%; height: 100%;
    object-fit: cover; filter: blur(6px); transform: scale(1.1);
  }}
  .monster-item-img-wrap .img-main {{
    position: relative; z-index: 10; width: 100%; height: 100%; object-fit: contain;
  }}
  .monster-item-name {{
    font-size: 10px; color: #999; text-align: center; line-height: 1.3; max-width: 64px;
    overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  }}
</style>
</head>
<body>
{cards_html}
</body>
</html>"""

# ── 同步爬取核心（在线程池中执行） ───────────────────────────────────────────
def _query_sync(keyword: str) -> bytes | None:
    """同步执行爬取+渲染，在线程池中调用以避免阻塞事件循环。
    顺序搜索 items 和 monsters 两个类别，合并结果后截图返回 PNG bytes。
    """
    from playwright.sync_api import sync_playwright

    items_url    = f"https://bazaardb.gg/search?c=items&q={quote(keyword)}"
    monsters_url = f"https://bazaardb.gg/search?c=monsters&q={quote(keyword)}"

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="zh-CN",
            viewport={"width": 1280, "height": 900},
        )

        def _fetch_page(url: str, js: str, label: str) -> list:
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            try:
                page.wait_for_selector("._ak", timeout=15000)
            except Exception:
                nonebot.logger.info(f"[bazaardb] 无{label}结果 keyword={keyword}")
                page.close()
                return []
            records = page.evaluate(js)
            nonebot.logger.info(f"[bazaardb] 找到 {len(records)} 个{label} keyword={keyword}")
            page.close()
            return records

        # 顺序抓取物品和怪物（同步环境不可并行）
        items_result    = _fetch_page(items_url,    JS_EXTRACT_ITEMS,    "物品")
        monsters_result = _fetch_page(monsters_url, JS_EXTRACT_MONSTERS, "怪物")

        # 过滤掉没有词条也没有描述的空物品
        items_result = [
            r for r in items_result
            if r.get("enchantments") or r.get("description")
        ]

        all_records = items_result + monsters_result

        if not all_records:
            browser.close()
            return None

        nonebot.logger.info(
            f"[bazaardb] 合计 {len(items_result)} 物品 + {len(monsters_result)} 怪物 keyword={keyword}"
        )

        # 生成 HTML 写临时文件用于截图
        html_content = _build_html(all_records)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False, encoding="utf-8") as f:
            html_path = f.name
            f.write(html_content)

        try:
            card_page = context.new_page()
            card_page.goto(f"file://{html_path}", wait_until="domcontentloaded")
            try:
                card_page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            card_page.wait_for_timeout(800)
            size = card_page.evaluate("({w: document.body.scrollWidth, h: document.body.scrollHeight})")
            card_page.set_viewport_size({"width": size["w"] + 40, "height": size["h"] + 40})
            img_bytes = card_page.screenshot(full_page=True, type="png")
            card_page.close()
        finally:
            os.unlink(html_path)  # 截图后删除临时 HTML，PNG 由调用方缓存

        browser.close()

    return img_bytes
