"""note 备忘录卡片渲染（Pillow，无浏览器依赖）

视觉沿用 plugins/deepseek_balance/render.py 的卡片语言：淡蓝渐变底 + 白色圆角
卡片 + 柔和投影 + 顶部品牌渐变条 + 右上角白底胶囊，保证两个插件出图风格一致。

绘图原语（_font / _vgradient / _dgradient / _rounded_mask / _shadow / _text /
_pill）刻意从 deepseek_balance 复制了一份而没有 import 复用：bot.py 里插件是
按行启用/注释的，让「备忘录」硬依赖另一个业务插件，一旦那个插件被注释掉，
本插件会直接 import 失败。宁可重复这点通用代码。

卡片结构：
  ┌ 渐变头：标题 + 条数 + 状态胶囊
  │ 记录行：序号徽章 + 正文（自动折行，最多 3 行后省略）+ 时间
  │ 记录之间细分隔线
  └ 页脚：推送间隔说明
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# 绘图原语签名里反复出现的形状，起个名字省得每处都展开写
_RGB = Tuple[int, int, int]   # (r, g, b)
_SIZE = Tuple[int, int]       # (宽, 高)

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
    """按候选顺序取第一个能加载的字体文件，结果缓存。

    每台机器装的字体都不一样，所以逐个候选试；全都不行时退回 Pillow 内置位图
    字体 —— 版面会难看，但至少卡片还能出图。
    """
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]
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


# ── 颜色（与 deepseek_balance 一致）───────────────────────────────────────────
C_BRAND_1 = (77, 107, 254)
C_BRAND_2 = (124, 92, 255)
C_CARD = (255, 255, 255)
C_BG_1 = (243, 246, 255)
C_BG_2 = (255, 255, 255)
C_TEXT = (23, 31, 47)
C_DIM = (92, 102, 122)
C_LINE = (229, 235, 245)
C_WHITE = (255, 255, 255)

# ── 布局 ──────────────────────────────────────────────────────────────────────
W = 880
MARGIN = 30
CARD_X = MARGIN
CARD_W = W - MARGIN * 2
RADIUS = 26
HEAD_H = 132
PAD_X = 34
CONTENT_X = CARD_X + PAD_X
CONTENT_R = CARD_X + CARD_W - PAD_X

FS_TITLE = 34
FS_SUB = 16
FS_PILL = 17
FS_NOTE = 26
FS_TIME = 15
FS_BADGE = 23
FS_FOOT = 15
FS_EMPTY = 22
FS_HINT = 17

BADGE = 46
BADGE_GAP = 18
ROW_GAP = 22
MAX_LINES = 3          # 单条记录最多显示行数，超出省略
MAX_ROWS = 30          # 单张卡片最多显示条数，超出折叠

# 垂直间距（高度计算与绘制必须用同一套常量，否则会重叠）
TOP_PAD = 30           # 渐变头 → 第一条记录
NOTICE_GAP = 26        # 最后一条记录 → 「还有 N 条未显示」
EMPTY_GAP = 16         # 空状态：主提示 → 操作提示
BOTTOM_PAD = 30        # 内容 → 卡片底（无页脚时）
FOOT_TOP_GAP = 34      # 内容 → 页脚分隔线
FOOT_LINE_GAP = 16     # 页脚分隔线 → 页脚文字
FOOT_BOTTOM = 28       # 页脚文字 → 卡片底


# ── 基础绘制 ──────────────────────────────────────────────────────────────────
def _vgradient(size: _SIZE, top: _RGB, bottom: _RGB) -> Image.Image:
    """竖向渐变：先在 1×h 上逐行取色，再横向拉伸。"""
    w, h = size
    img = Image.new("RGB", (1, h))
    px = img.load()
    for y in range(h):
        t = y / max(h - 1, 1)
        px[0, y] = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return img.resize((w, h), Image.BILINEAR)


def _dgradient(size: _SIZE, left: _RGB, right: _RGB) -> Image.Image:
    """横向渐变：先在 w×1 上逐列取色，再纵向拉伸。"""
    w, h = size
    img = Image.new("RGB", (w, 1))
    px = img.load()
    for x in range(w):
        t = x / max(w - 1, 1)
        px[x, 0] = tuple(int(left[i] + (right[i] - left[i]) * t) for i in range(3))
    return img.resize((w, h), Image.BILINEAR)


def _rounded_mask(size: _SIZE, radius: int) -> Image.Image:
    """圆角矩形遮罩，用来把渐变条裁成和卡片一致的圆角。"""
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=255
    )
    return mask


def _mask_top_rounded(size: _SIZE, radius: int) -> Image.Image:
    """只保留上方两个圆角的遮罩：把下面两角重新填实即可。"""
    mask = _rounded_mask(size, radius)
    ImageDraw.Draw(mask).rectangle([0, size[1] - radius, size[0], size[1]], fill=255)
    return mask


def _shadow(canvas: Image.Image, box: Tuple[int, int, int, int], radius: int) -> None:
    """卡片柔和投影：单独画一层再高斯模糊，最后按 alpha 贴回画布。"""
    x0, y0, x1, y1 = box
    pad = 30
    layer = Image.new("RGBA", (x1 - x0 + pad * 2, y1 - y0 + pad * 2), (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(
        [pad, pad, pad + (x1 - x0), pad + (y1 - y0)],
        radius=radius,
        fill=(60, 80, 140, 44),
    )
    layer = layer.filter(ImageFilter.GaussianBlur(13))
    canvas.paste(layer, (x0 - pad, y0 - pad + 8), layer)


def _text(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: _RGB,
    *,
    right: Optional[int] = None,
) -> None:
    """画一行文字；给 right 就按右边界贴齐（写在右上角的胶囊要用）。"""
    if right is not None:
        bbox = draw.textbbox((0, y), text, font=font)
        x = right - (bbox[2] - bbox[0])
    draw.text((x, y), text, font=font, fill=fill)


def _pill(
    draw: ImageDraw.ImageDraw,
    right: int,
    top: int,
    text: str,
    font: ImageFont.FreeTypeFont,
    text_color: _RGB,
    *,
    dot_color: Optional[_RGB] = None,
    bg: _RGB = C_WHITE,
) -> None:
    """右上角的圆角胶囊；带 dot_color 时在前面点一个小圆点。"""
    probe = draw.textbbox((0, 0), text, font=font)
    tw = probe[2] - probe[0]
    pad_x, h = 18, 38
    dot_d, dot_gap = 10, 9
    extra = (dot_d + dot_gap) if dot_color else 0
    w = int(tw + pad_x * 2 + extra)
    x = right - w
    draw.rounded_rectangle([x, top, right, top + h], radius=h // 2, fill=bg)
    tx = x + pad_x
    if dot_color:
        cy = top + h / 2
        draw.ellipse([tx, cy - dot_d / 2, tx + dot_d, cy + dot_d / 2], fill=dot_color)
        tx += dot_d + dot_gap
    draw.text((tx, top + (h - font.size) // 2 - 3), text, font=font, fill=text_color)


def _wrap(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_w: int,
    max_lines: int = MAX_LINES,
) -> List[str]:
    """按像素宽度逐字折行；超过 max_lines 时末行截断加省略号。

    逐字折行对中文是正确做法；英文单词会被从中间断开，但备忘录内容以中文
    短句为主，这个取舍换来实现简单、不会出现超宽溢出。
    """
    text = (text or "").replace("\n", " ").strip()
    if not text:
        return [""]

    lines: List[str] = []
    cur = ""
    for ch in text:
        if cur and draw.textlength(cur + ch, font=font) > max_w:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)

    if len(lines) <= max_lines:
        return lines

    lines = lines[:max_lines]
    last = lines[-1]
    while last and draw.textlength(last + "…", font=font) > max_w:
        last = last[:-1]
    lines[-1] = (last + "…") if last else "…"
    return lines


def _badge(draw: ImageDraw.ImageDraw, x: int, y: int, number: str) -> None:
    """左侧序号徽章：品牌蓝圆角方块 + 白色数字"""
    draw.rounded_rectangle(
        [x, y, x + BADGE, y + BADGE], radius=14, fill=C_BRAND_1
    )
    # 序号位数多时缩小字号，免得数字顶出徽章
    font = _font(FS_BADGE if len(number) <= 2 else FS_BADGE - 6, bold=True)
    bbox = draw.textbbox((0, 0), number, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        (x + (BADGE - tw) / 2 - bbox[0], y + (BADGE - th) / 2 - bbox[1]),
        number,
        font=font,
        fill=C_WHITE,
    )


def _fmt_time(raw: Optional[str]) -> str:
    """'2026-09-14 17:30:00' -> '09-14 17:30'"""
    if not raw:
        return ""
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").strftime("%m-%d %H:%M")
    except (ValueError, TypeError):
        return raw[:16]


# ── 主渲染 ────────────────────────────────────────────────────────────────────
def render_notes(
    notes: Sequence[dict],
    *,
    interval_text: str = "",
    now: Optional[datetime] = None,
) -> bytes:
    """把备忘录列表渲染成 PNG 字节流

    notes: [{"id": 1, "text": "...", "created_at": "..."}]，按显示顺序传入
    interval_text: 页脚显示的推送间隔，如 "20 分钟"；留空则页脚只显示用法
    """
    now = now or datetime.now()
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    shown = list(notes)[:MAX_ROWS]
    hidden = len(notes) - len(shown)
    text_max_w = CONTENT_R - (CONTENT_X + BADGE + BADGE_GAP)

    # ── 第一遍：测量 ──
    # 高度必须与下方绘制用同一套常量算，否则页脚会和内容重叠
    font_note = _font(FS_NOTE)
    font_time = _font(FS_TIME)
    font_empty = _font(FS_EMPTY)
    font_hint = _font(FS_HINT)
    line_h = probe.textbbox((0, 0), "Ag", font=font_note)[3] + 11
    time_h = probe.textbbox((0, 0), "Ag", font=font_time)[3] + 6

    rows: List[Tuple[str, List[str], str]] = []
    for n in shown:
        wrapped = _wrap(probe, str(n.get("text", "")), font_note, text_max_w)
        rows.append((str(n.get("id", "")), wrapped, _fmt_time(n.get("created_at"))))

    def row_h(wrapped: List[str], ts: str) -> int:
        """单条记录占的高度：正文行 + 时间行，但不小于序号徽章的高度。"""
        body = len(wrapped) * line_h + (time_h if ts else 0)
        return max(BADGE, body)

    h_hint = probe.textbbox((0, 0), "Ag", font=font_hint)[3]

    if rows:
        body_h = sum(row_h(w, t) for _, w, t in rows)
        body_h += ROW_GAP * (len(rows) - 1)
        if hidden > 0:
            body_h += NOTICE_GAP + h_hint
    else:
        body_h = probe.textbbox((0, 0), "Ag", font=font_empty)[3] + EMPTY_GAP + h_hint

    tail_h = BOTTOM_PAD
    if interval_text:
        tail_h = (
            FOOT_TOP_GAP
            + FOOT_LINE_GAP
            + probe.textbbox((0, 0), "Ag", font=_font(FS_FOOT))[3]
            + FOOT_BOTTOM
        )

    card_h = HEAD_H + TOP_PAD + body_h + tail_h

    canvas = Image.new("RGB", (W, card_h + MARGIN + 34), C_BG_2)
    canvas.paste(_vgradient(canvas.size, C_BG_1, C_BG_2), (0, 0))
    _shadow(canvas, (CARD_X, MARGIN, CARD_X + CARD_W, MARGIN + card_h), RADIUS)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        [CARD_X, MARGIN, CARD_X + CARD_W, MARGIN + card_h], radius=RADIUS, fill=C_CARD
    )

    # ── 顶部品牌渐变条 ──
    head = _dgradient((CARD_W, HEAD_H), C_BRAND_1, C_BRAND_2)
    canvas.paste(head, (CARD_X, MARGIN), _mask_top_rounded((CARD_W, HEAD_H), RADIUS))
    draw = ImageDraw.Draw(canvas)

    _text(draw, CONTENT_X, MARGIN + 26, "备忘录", _font(FS_TITLE, bold=True), C_WHITE)
    _text(
        draw,
        CONTENT_X,
        MARGIN + 78,
        f"更新于 {now:%Y-%m-%d %H:%M}",
        _font(FS_SUB),
        (233, 238, 255),
    )

    if notes:
        _pill(
            draw,
            CONTENT_R,
            MARGIN + 28,
            f"{len(notes)} 条记录",
            _font(FS_PILL, bold=True),
            C_BRAND_1,
        )
    else:
        _pill(
            draw,
            CONTENT_R,
            MARGIN + 28,
            "暂无记录",
            _font(FS_PILL, bold=True),
            C_DIM,
        )

    # ── 记录行 ──
    y = MARGIN + HEAD_H + TOP_PAD
    if rows:
        for i, (num, wrapped, ts) in enumerate(rows):
            _badge(draw, CONTENT_X, y, num)

            ty = y + (2 if ts else 0)
            for line in wrapped:
                _text(draw, CONTENT_X + BADGE + BADGE_GAP, ty, line, font_note, C_TEXT)
                ty += line_h
            if ts:
                _text(draw, CONTENT_X + BADGE + BADGE_GAP, ty + 1, ts, font_time, C_DIM)

            y += row_h(wrapped, ts)
            if i < len(rows) - 1:
                y += ROW_GAP
                draw.line(
                    [CONTENT_X, y - ROW_GAP // 2, CONTENT_R, y - ROW_GAP // 2],
                    fill=C_LINE,
                    width=1,
                )

        if hidden > 0:
            y += NOTICE_GAP
            _text(
                draw,
                CONTENT_X,
                y,
                f"还有 {hidden} 条未显示，用 note list 分批查看",
                font_hint,
                C_DIM,
            )
    else:
        h_empty = probe.textbbox((0, 0), "Ag", font=font_empty)[3]
        msg = "还没有任何记录"
        mb = draw.textbbox((0, 0), msg, font=font_empty)
        _text(draw, (W - (mb[2] - mb[0])) // 2, y, msg, font_empty, C_DIM)

        hint = "发送  note add <内容>  开始记录"
        hb = draw.textbbox((0, 0), hint, font=font_hint)
        _text(
            draw,
            (W - (hb[2] - hb[0])) // 2,
            y + h_empty + EMPTY_GAP,
            hint,
            font_hint,
            C_DIM,
        )

    # ── 页脚（位置由 card_h 反推，与上方高度计算严格对应）──
    if interval_text:
        foot_font = _font(FS_FOOT)
        foot_text_h = probe.textbbox((0, 0), "Ag", font=foot_font)[3]
        fy = MARGIN + card_h - FOOT_BOTTOM - foot_text_h
        draw.line(
            [CONTENT_X, fy - FOOT_LINE_GAP, CONTENT_R, fy - FOOT_LINE_GAP],
            fill=C_LINE,
            width=1,
        )
        _text(
            draw,
            CONTENT_X,
            fy,
            f"每 {interval_text}自动推送  ·  note add 记录 / note rm 删除",
            foot_font,
            C_DIM,
        )

    buf = BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
