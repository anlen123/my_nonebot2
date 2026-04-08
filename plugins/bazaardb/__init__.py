"""
bazaardb —— BazaarDB 物品查询插件

用法：bz <关键词>
示例：bz 光纤

爬取 bazaardb.gg 搜索结果，渲染为卡片图片后发送到 QQ。
"""

import asyncio
import base64
import json
import os
import re
import tempfile
from urllib.parse import quote

import nonebot
from nonebot.plugin import on_regex
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment

bz = on_regex(pattern=r"^bz ")


@bz.handle()
async def bz_rev(bot: Bot, event: Event):
    keyword = str(event.message).strip()[3:].strip()
    if not keyword:
        await bot.send(event=event, message=MessageSegment.text("请输入关键词，例如：bz 光纤"))
        return

    await bot.send(event=event, message=MessageSegment.text(f"🔍 正在查询「{keyword}」，请稍候..."))

    try:
        img_bytes = await asyncio.get_event_loop().run_in_executor(
            None, _query_sync, keyword
        )
    except Exception as e:
        nonebot.logger.warning(f"[bazaardb] 查询失败 keyword={keyword}: {e}")
        await bot.send(event=event, message=MessageSegment.text(f"查询失败：{e}"))
        return

    if img_bytes is None:
        await bot.send(event=event, message=MessageSegment.text(f"未找到「{keyword}」相关物品"))
        return

    img_b64 = base64.b64encode(img_bytes).decode()
    await bot.send(event=event, message=MessageSegment.image(f"base64://{img_b64}"))


# ── 词条颜色映射 ──────────────────────────────────────────────────────────────
ENCHANT_THEME = {
    "黄金": "enc-golden", "沉重": "enc-slow",  "寒冰": "enc-freeze",
    "疾速": "enc-haste",  "护盾": "enc-shield", "回复": "enc-heal",
    "毒素": "enc-poison", "炽焰": "enc-burn",   "闪亮": "enc-charge",
    "致命": "enc-crit",   "辉耀": "enc-radiant", "长青": "enc-evergreen",
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


def _build_html(items: list) -> str:
    cards_html = ""
    for item in items:
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
            if "+" in tag:
                type_tags_html += f'<span class="tag-tier">{tag}</span>'
            else:
                type_tags_html += f'<span class="tag-type">{tag}</span>'

        desc_html = _highlight(item["description"]) if item["description"] else '<span style="color:#555">—</span>'

        cards_html += f"""
  <div class="card">
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
      </div>
      <div class="item-desc">{desc_html}</div>
    </div>
    <div class="enchantments-grid">{enc_cells}
    </div>
  </div>"""

    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
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
    background: #252218; border: 1px solid #3a3520;
    border-radius: 10px; overflow: hidden;
    width: 980px; margin-bottom: 20px;
  }}
  .card:last-child {{ margin-bottom: 0; }}
  .card-header {{
    display: flex; align-items: center;
    background: #2a2518; border-bottom: 1px solid #3a3520;
    padding: 14px 18px; gap: 16px;
  }}
  .item-image-wrap {{
    flex-shrink: 0; width: 72px; height: 72px;
    border-radius: 6px;
    background: linear-gradient(135deg, #5a4a1a 0%, #3a2e10 100%);
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
  .tag-tier {{
    background: #3a2e08; color: #d4a820;
    border: 1px solid #8a6a10; border-radius: 4px;
    font-size: 12px; font-weight: 600; padding: 2px 9px;
  }}
  .tag-type {{
    background: #1a2e3a; color: #4ab8e0;
    border: 1px solid #2a6a8a; border-radius: 4px;
    font-size: 12px; font-weight: 600; padding: 2px 9px;
  }}
  .item-desc {{ flex: 1; font-size: 14px; color: #c8bfa0; line-height: 1.6; padding-left: 4px; }}
  .enchantments-grid {{ display: grid; grid-template-columns: repeat(6, 1fr); }}
  .enchant-cell {{
    padding: 10px 12px; border-right: 1px solid #3a3520;
    border-bottom: 1px solid #3a3520; min-height: 78px;
  }}
  .enchant-cell:nth-child(6n)        {{ border-right: none; }}
  .enchant-cell:nth-last-child(-n+6) {{ border-bottom: none; }}
  .enc-golden   {{ background: rgba(200,168,40,0.08); }}
  .enc-slow     {{ background: rgba(100,80,180,0.10); }}
  .enc-freeze   {{ background: rgba(60,160,220,0.10); }}
  .enc-haste    {{ background: rgba(60,200,120,0.10); }}
  .enc-shield   {{ background: rgba(80,140,220,0.10); }}
  .enc-heal     {{ background: rgba(60,200,100,0.10); }}
  .enc-poison   {{ background: rgba(100,200,60,0.10); }}
  .enc-burn     {{ background: rgba(220,100,40,0.10); }}
  .enc-charge   {{ background: rgba(220,180,40,0.10); }}
  .enc-crit     {{ background: rgba(220,60,60,0.10); }}
  .enc-radiant  {{ background: rgba(220,200,80,0.10); }}
  .enc-evergreen{{ background: rgba(60,180,80,0.10); }}
  .enc-default  {{ background: rgba(100,100,100,0.08); }}
  .enchant-name {{ font-size: 12px; font-weight: 700; margin-bottom: 5px; }}
  .enc-golden   .enchant-name {{ color: #d4a820; }}
  .enc-slow     .enchant-name {{ color: #9070d0; }}
  .enc-freeze   .enchant-name {{ color: #40b0e0; }}
  .enc-haste    .enchant-name {{ color: #40d080; }}
  .enc-shield   .enchant-name {{ color: #5090e0; }}
  .enc-heal     .enchant-name {{ color: #40c870; }}
  .enc-poison   .enchant-name {{ color: #80c840; }}
  .enc-burn     .enchant-name {{ color: #e06030; }}
  .enc-charge   .enchant-name {{ color: #e0b830; }}
  .enc-crit     .enchant-name {{ color: #e04040; }}
  .enc-radiant  .enchant-name {{ color: #e0c840; }}
  .enc-evergreen .enchant-name {{ color: #40c060; }}
  .enc-default  .enchant-name {{ color: #a0a0a0; }}
  .enchant-desc {{ font-size: 12px; color: #a09880; line-height: 1.55; }}
  .kw-slow   {{ color: #9070d0; font-weight: 600; }}
  .kw-freeze {{ color: #40b0e0; font-weight: 600; }}
  .kw-haste  {{ color: #40d080; font-weight: 600; }}
  .kw-shield {{ color: #5090e0; font-weight: 600; }}
  .kw-heal   {{ color: #40c870; font-weight: 600; }}
  .kw-poison {{ color: #80c840; font-weight: 600; }}
  .kw-burn   {{ color: #e06030; font-weight: 600; }}
  .kw-charge {{ color: #e0b830; font-weight: 600; }}
  .kw-crit   {{ color: #e04040; font-weight: 600; }}
  .kw-dmg    {{ color: #e04040; font-weight: 600; }}
  .kw-life   {{ color: #40c060; font-weight: 600; }}
  .kw-val    {{ color: #f0c040; font-weight: 700; }}
</style>
</head>
<body>
{cards_html}
</body>
</html>"""


def _query_sync(keyword: str) -> bytes | None:
    """同步执行爬取+渲染，在线程池中调用以避免阻塞事件循环"""
    from playwright.sync_api import sync_playwright

    url = f"https://bazaardb.gg/search?c=items&q={quote(keyword)}"

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="zh-CN",
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

        try:
            page.wait_for_selector("._ak", timeout=20000)
        except Exception:
            browser.close()
            return None

        items = page.evaluate("""
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
                    const descContainer = block.querySelector("._bT._bV ._cL");
                    const description = descContainer ? descContainer.innerText.trim() : "";
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
                    results.push({ name, hero_tag: heroTag, type_tags: typeTags, description, enchantments, img_blur: imgBlur, img_src: imgSrc });
                }
                return results;
            }
        """)

        if not items:
            browser.close()
            return None

        nonebot.logger.info(f"[bazaardb] 关键词={keyword} 找到 {len(items)} 个物品")

        # 生成 HTML 并截图（写临时文件，截图后删除）
        html_content = _build_html(items)
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
            os.unlink(html_path)

        browser.close()

    return img_bytes
