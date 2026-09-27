"""
ai_news 表格图片渲染（Pillow，无浏览器依赖）

对外两个入口：
  render_table  —— 把一批 NewsItem 画成早报表格图
  render_error  —— 抓取失败时画一张简单的提示图

排版用像素常量写死（W / *_H / COL_* 等），字体优先微软雅黑，系统里都没有才退回
Pillow 内置字体。文字宽度一律用 textlength 量，不做字符数估算，中英混排才不会溢出。
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from .sources import NewsItem

# ── 字体 ──────────────────────────────────────────────────────────────────────
_BOT_ROOT = Path(__file__).parent.parent.parent
_BOLD_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    str(_BOT_ROOT / "simsun.ttc"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
_REGULAR_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    str(_BOT_ROOT / "simsun.ttc"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

_font_cache: Dict[Tuple[int, bool], ImageFont.FreeTypeFont] = {}


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """按字号取字体（带缓存）；候选字体全不可用时退回 Pillow 内置字体"""
    key = (size, bold)
    cached = _font_cache.get(key)
    if cached is not None:
        return cached
    for path in (_BOLD_CANDIDATES if bold else _REGULAR_CANDIDATES):
        try:
            font = ImageFont.truetype(path, size)
            _font_cache[key] = font
            return font
        except OSError:
            continue
    font = ImageFont.load_default()
    _font_cache[key] = font
    return font


def _probe() -> ImageDraw.ImageDraw:
    """只用来量文字宽度的临时画布（1×1 即可，不会真的画东西）"""
    return ImageDraw.Draw(Image.new("RGB", (1, 1)))


# ── 配色 ──────────────────────────────────────────────────────────────────────
C_BRAND = (37, 99, 235)
C_BRAND_SOFT = (239, 244, 255)
C_BG = (255, 255, 255)
C_ALT = (248, 250, 252)
C_TEXT = (31, 41, 55)
C_DIM = (105, 115, 132)
C_LINE = (229, 234, 240)
C_WHITE = (255, 255, 255)
C_ERROR = (220, 38, 38)

# ── 布局（像素） ──────────────────────────────────────────────────────────────
W = 1060
MARGIN = 24
CW = W - MARGIN * 2
TOP_H = 84
HDR_H = 46
FOOT_H = 46
COL_IDX = 46
COL_SRC = 112
COL_TIME = 112
COL_GAP = 14
COL_TITLE = CW - COL_IDX - COL_SRC - COL_TIME - COL_GAP * 3
# 标题区上下各留这么多空白
ROW_PAD = 10

FS_BRAND = 30
FS_BRAND_R = 19
FS_HEAD = 20
FS_TITLE = 20
FS_LINK = 15
FS_META = 18
FS_SUB = 15       # 副标题 / 页脚
FS_BODY = 18      # 失败提示图正文
FS_ERR = 22       # 失败提示图标题
LINE_H = 27
LINK_H = 20


_WORD_CHARS = set("+-._/&%$#@='\"")


def _tokenize(text: str) -> List[str]:
    """把文本切成 token：英文/数字串算一个词（避免把 Salesforce、$1.5B 从中间断开），中文每字一个"""
    tokens: List[str] = []
    buf = ""
    for ch in text:
        if ch.isascii() and (ch.isalnum() or ch in _WORD_CHARS):
            buf += ch
        else:
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
    if buf:
        tokens.append(buf)
    return tokens


def _glues(prev: str, nxt: str) -> bool:
    """两个 token 直接拼接会不会粘成一个词"""
    if not prev or not nxt:
        return False
    return (
        prev[-1].isascii()
        and prev[-1].isalnum()
        and nxt[0].isascii()
        and nxt[0].isalnum()
    )


def _concat(tokens: List[str]) -> str:
    """把 token 拼回字符串，需要时补上被吃掉的词间空格"""
    out = ""
    for token in tokens:
        if _glues(out, token):
            out += " "
        out += token
    return out


def _ellipsize(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> str:
    """截到 max_w 以内并在末尾加省略号；调用方已知这行放不下，所以总是带「…」"""
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def _balance_lines(
    draw: ImageDraw.ImageDraw, lines: List[str], font: ImageFont.FreeTypeFont, max_w: int
) -> List[str]:
    """两行时把长行的词匀给短行，避免第二行只剩一两个字

    搬动时按 token 整体搬（英文单词、词间空格都不会丢），只在断行处丢弃空白。
    """
    if len(lines) != 2:
        return lines

    first = _tokenize(lines[0])
    second = _tokenize(lines[1])
    if not first or not second:
        return lines

    while len(first) > 1:
        # 从行尾往前找到最后一个非空白 token
        pos = len(first) - 1
        while pos > 0 and not first[pos].strip():
            pos -= 1
        if pos == 0 or not first[pos].strip():
            break

        chunk = first[pos:]
        if not first[pos - 1].strip():
            chunk = [" "] + chunk  # 断行处的空格跟着走，句内空格不丢
        new_first = first[:pos]
        while new_first and not new_first[-1].strip():
            new_first = new_first[:-1]
        if not new_first:
            break

        new_second = chunk + second
        w_first = draw.textlength(_concat(new_first), font=font)
        w_second = draw.textlength(_concat(new_second).lstrip(), font=font)
        if w_second > max_w or w_first <= w_second:
            break
        first, second = new_first, new_second

    return [_concat(first).rstrip(), _concat(second).lstrip()]


def _wrap(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_w: int,
    max_lines: int = 2,
) -> List[str]:
    """按像素宽度折行（词感知），超出 max_lines 时在末尾加省略号"""
    tokens = _tokenize((text or "").strip())
    lines: List[str] = []
    cur = ""
    idx = 0
    truncated = False

    while idx < len(tokens):
        token = tokens[idx]
        cand = (cur + token) if cur else token.lstrip()
        if draw.textlength(cand, font=font) <= max_w:
            cur = cand
            idx += 1
            continue

        if cur:
            if len(lines) >= max_lines - 1:
                lines.append(_ellipsize(draw, cur, font, max_w))
                truncated = True
                break
            lines.append(cur)
            cur = ""
            continue

        # 单个词就超宽，硬切一段，剩下的下轮继续
        piece = ""
        for ch in token:
            if draw.textlength(piece + ch, font=font) <= max_w:
                piece += ch
            else:
                break
        if not piece:
            piece = token[:1]
        cur = piece
        tokens[idx] = token[len(piece):]

    if not truncated and cur:
        lines.append(cur)
    if not lines:
        lines = [""]
    return _balance_lines(draw, lines, font, max_w)


def _shorten(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> str:
    """放得下就原样返回，放不下才截断加省略号"""
    if draw.textlength(text, font=font) <= max_w:
        return text
    out = text
    while out and draw.textlength(out + "…", font=font) > max_w:
        out = out[:-1]
    return out + "…"


def _pretty_url(url: str) -> str:
    """去掉 scheme 与 utm/spm 之类的跟踪参数，让链接更短"""
    url = (url or "").strip()
    if "?" in url:
        base, _, query = url.partition("?")
        kept = [
            part
            for part in query.split("&")
            if part
            and not part.lower().startswith(("utm_", "spm", "from=", "ref=", "source="))
        ]
        url = base + (("?" + "&".join(kept)) if kept else "")
    for prefix in ("https://", "http://"):
        if url.startswith(prefix):
            url = url[len(prefix):]
    return url


def _fmt_time(dt: Optional[datetime], now: datetime) -> str:
    """时间列文案：今年只给月日时分，跨年给完整日期，没时间给破折号"""
    if not dt:
        return "—"
    if dt.year == now.year:
        return dt.strftime("%m-%d %H:%M")
    return dt.strftime("%Y-%m-%d")


def render_table(
    items: Sequence[NewsItem],
    now: Optional[datetime] = None,
    subtitle: str = "",
    show_links: bool = True,
) -> bytes:
    """把资讯列表渲染成表格 PNG，返回图片字节"""
    now = now or datetime.now()
    probe = _probe()
    font_brand = _font(FS_BRAND, bold=True)
    font_brand_r = _font(FS_BRAND_R, bold=True)
    font_head = _font(FS_HEAD, bold=True)
    font_title = _font(FS_TITLE, bold=True)
    font_link = _font(FS_LINK)
    font_meta = _font(FS_META)

    # ① 量出每行的标题折行与行高，顺带排好各行的 y
    rows: List[Tuple[List[str], bool, int]] = []
    offsets: List[int] = []
    cursor = TOP_H + HDR_H
    for item in items:
        title_lines = _wrap(probe, item.title, font_title, COL_TITLE, max_lines=2)
        has_link = bool(show_links and item.url)
        row_h = ROW_PAD + len(title_lines) * LINE_H + (LINK_H + 2 if has_link else 0) + ROW_PAD
        rows.append((title_lines, has_link, row_h))
        offsets.append(cursor)
        cursor += row_h
    body_bottom = cursor
    total_h = body_bottom + FOOT_H + 6

    img = Image.new("RGB", (W, total_h), C_BG)
    draw = ImageDraw.Draw(img)

    # ② 顶部品牌条
    draw.rectangle([0, 0, W, TOP_H], fill=C_BRAND)
    draw.text((MARGIN, 16), "AI 资讯速览", font=font_brand, fill=C_WHITE)
    stamp = now.strftime("%Y-%m-%d %H:%M")
    stamp_w = draw.textlength(stamp, font=font_brand_r)
    draw.text((W - MARGIN - stamp_w, 24), stamp, font=font_brand_r, fill=C_WHITE)
    sub = subtitle or f"共 {len(items)} 条 · 每日 09:30 自动更新"
    draw.text((MARGIN, 56), sub, font=_font(FS_SUB), fill=(219, 231, 255))

    # ③ 表头
    y = TOP_H
    draw.rectangle([0, y, W, y + HDR_H], fill=C_BRAND_SOFT)
    hdr_y = y + (HDR_H - FS_HEAD) // 2 - 2
    x_idx = MARGIN
    x_src = x_idx + COL_IDX + COL_GAP
    x_title = x_src + COL_SRC + COL_GAP
    x_time = x_title + COL_TITLE + COL_GAP
    draw.text((x_idx + 6, hdr_y), "#", font=font_head, fill=C_BRAND)
    draw.text((x_src, hdr_y), "来源", font=font_head, fill=C_BRAND)
    draw.text((x_title, hdr_y), "标题", font=font_head, fill=C_BRAND)
    draw.text((x_time, hdr_y), "时间", font=font_head, fill=C_BRAND)
    draw.line([(0, y + HDR_H), (W, y + HDR_H)], fill=C_LINE, width=1)

    # ④ 数据行（y 已在 ① 排好；分隔线最后画，避免被下一行底色盖住）
    for i, (item, (title_lines, has_link, row_h)) in enumerate(zip(items, rows)):
        ry = offsets[i]
        if i % 2 == 1:
            draw.rectangle([0, ry, W, ry + row_h], fill=C_ALT)

        draw.text((x_idx + 6, ry + 11), f"{i + 1:02d}", font=font_meta, fill=C_BRAND)
        draw.text(
            (x_src, ry + 11),
            _shorten(draw, item.source, font_meta, COL_SRC),
            font=font_meta,
            fill=C_TEXT,
        )

        ty = ry + 9
        for line in title_lines:
            draw.text((x_title, ty), line, font=font_title, fill=C_TEXT)
            ty += LINE_H
        if has_link:
            draw.text(
                (x_title, ty + 1),
                _shorten(draw, _pretty_url(item.url), font_link, COL_TITLE),
                font=font_link,
                fill=C_DIM,
            )
        time_text = _fmt_time(item.published, now)
        draw.text(
            (x_time + COL_TIME - draw.textlength(time_text, font=font_meta), ry + 11),
            time_text,
            font=font_meta,
            fill=C_DIM,
        )

    # 分隔线
    for ry in offsets[1:]:
        draw.line([(0, ry), (W, ry)], fill=C_LINE, width=1)
    draw.line([(0, body_bottom), (W, body_bottom)], fill=C_LINE, width=1)

    # ⑤ 页脚
    footer = "数据来源：" + " / ".join(dict.fromkeys(item.source for item in items))
    draw.text(
        (MARGIN, body_bottom + 14),
        _shorten(draw, footer, _font(FS_SUB), W - MARGIN * 2),
        font=_font(FS_SUB),
        fill=C_DIM,
    )

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_error(text: str) -> bytes:
    """抓取失败时渲染一张简单的提示图"""
    font = _font(FS_ERR, bold=True)
    body = _font(FS_BODY)
    lines = _wrap(_probe(), text, body, W - MARGIN * 2 - 40, max_lines=8)

    height = 120 + len(lines) * 28
    img = Image.new("RGB", (W, height), C_BG)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, W, 76], fill=C_ERROR)
    draw.text((MARGIN, 22), "AI 资讯抓取失败", font=font, fill=C_WHITE)
    y = 100
    for line in lines:
        draw.text((MARGIN, y), line, font=body, fill=C_TEXT)
        y += 28
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
