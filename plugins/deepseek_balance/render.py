"""deepseek_balance 的两张卡片图（Pillow，无浏览器依赖）

出口函数：
  render_multi(entries, source=..., baseline=...)  多供应商余额卡片（/ai余额 与定时推送共用）
  render_error(message)                            渲染失败时的提示卡片，保证任何时候都有图可发

设计要点：
  * 白色圆角卡片 + 柔和阴影 + 淡蓝渐变底，视觉上接近现代 App 的「账户卡片」
  * 顶部品牌渐变条（正常=蓝紫，异常=红橙），右上角白底胶囊显示状态
  * 按币种分组，一家一行；金额居中偏右，说明与已用/总额在下半行
  * 余额异常 → 金额转深琥珀；查询失败 → 整卡转红
  * 与上次查询对比：金额下方一行「↑ +1.23（+5.2%）」绿升红降，分组标题右侧给该币种合计增减，
    页脚写明对比基准时间（见 history.py；baseline 为 None 时显示「首次查询，暂无对比」）
  * 全部采用「游标 + textbbox 实测」排版，元素间距按实际墨迹计算，避免贴边/重叠
"""

from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

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
    """按顺序试字体文件，全部不可用才退回 Pillow 内置位图字体（缓存结果，避免重复找盘）"""
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]
    for path in (_BOLD_CANDIDATES if bold else _REGULAR_CANDIDATES):
        try:
            font = ImageFont.truetype(path, size)
            _font_cache[key] = font
            return font
        except (OSError, IOError):
            continue
    font = ImageFont.load_default()
    _font_cache[key] = font
    return font


# ── 配色 ──────────────────────────────────────────────────────────────────────
C_BRAND_1 = (77, 107, 254)      # DeepSeek 蓝
C_BRAND_2 = (124, 92, 255)      # 渐变末端紫
C_DANGER_1 = (219, 52, 52)
C_DANGER_2 = (240, 128, 72)
C_CARD = (255, 255, 255)
C_BG_1 = (243, 246, 255)
C_BG_2 = (255, 255, 255)
C_TEXT = (23, 31, 47)
C_DIM = (92, 102, 122)          # 次级灰（白底对比度 ≈ 5.9:1）
C_LINE = (229, 235, 245)
C_SOFT = (246, 248, 253)
C_TRACK = (228, 233, 243)
C_GREEN_BAR = (16, 185, 129)
C_BLUE = (47, 84, 235)
C_AMBER = (180, 83, 9)
C_RED = (200, 40, 40)
C_WHITE = (255, 255, 255)

# ── 布局 ──────────────────────────────────────────────────────────────────────
W = 880
MARGIN = 30
CARD_X = MARGIN
CARD_W = W - MARGIN * 2
RADIUS = 26
HEAD_H = 132
PAD_X = 34                       # 卡片内左右内边距
CONTENT_X = CARD_X + PAD_X
CONTENT_R = CARD_X + CARD_W - PAD_X

FS_TITLE = 34
FS_SUB = 16
FS_PILL = 17
FS_SEC = 18                      # 分组标题（如「人民币账户」）
FS_MONEY = 30
FS_ROW = 25
FS_SMALL = 15
FS_DELTA = 18                    # 「与上次对比」的增减文字

ROW_H = 96          # 一行的高度（标题/金额在上半行，说明与明细在下半行）
ROW_GAP = 12
GROUP_LABEL_H = 40
GROUP_GAP = 22

DELTA_FLOOR = 0.005             # 小于半分钱视为持平，避免显示「+0.00」

UNIT_LABEL = {
    "RMB": ("人民币账户", "¥"),
    "USD": ("美元账户", "$"),
}


# ── 基础绘制 ──────────────────────────────────────────────────────────────────
def _vgradient(
    size: Tuple[int, int], top: Tuple[int, int, int], bottom: Tuple[int, int, int]
) -> Image.Image:
    """竖直线性渐变（先画 1 像素宽的细条再拉伸，比逐像素画满图快得多）"""
    w, h = size
    img = Image.new("RGB", (1, h))
    px = img.load()
    for y in range(h):
        t = y / max(h - 1, 1)
        px[0, y] = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return img.resize((w, h), Image.BILINEAR)


def _dgradient(
    size: Tuple[int, int], left: Tuple[int, int, int], right: Tuple[int, int, int]
) -> Image.Image:
    """水平线性渐变"""
    w, h = size
    img = Image.new("RGB", (w, 1))
    px = img.load()
    for x in range(w):
        t = x / max(w - 1, 1)
        px[x, 0] = tuple(int(left[i] + (right[i] - left[i]) * t) for i in range(3))
    return img.resize((w, h), Image.BILINEAR)


def _rounded_mask(size: Tuple[int, int], radius: int) -> Image.Image:
    """整块圆角遮罩"""
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=255
    )
    return mask


def _mask_top_rounded(size: Tuple[int, int], radius: int) -> Image.Image:
    """上方圆角、下方直角的遮罩（卡片顶部渐变条用）"""
    mask = _rounded_mask(size, radius)
    ImageDraw.Draw(mask).rectangle([0, size[1] - radius, size[0], size[1]], fill=255)
    return mask


def _shadow(canvas: Image.Image, box: Tuple[int, int, int, int], radius: int) -> None:
    """在卡片位置上叠一层高斯模糊的圆角阴影"""
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


def _probe() -> ImageDraw.ImageDraw:
    """1×1 空画布上的 draw，只用来量文字宽度/高度，不实际出图"""
    return ImageDraw.Draw(Image.new("RGB", (1, 1)))


def _new_card(
    card_h: int, head_grad: Tuple[Tuple[int, int, int], Tuple[int, int, int]]
) -> Tuple[Image.Image, ImageDraw.ImageDraw]:
    """画好「渐变底 + 白卡片 + 顶部品牌条」，返回画布与它的 draw 对象"""
    canvas = Image.new("RGB", (W, card_h + MARGIN + 34), C_BG_2)
    canvas.paste(_vgradient(canvas.size, C_BG_1, C_BG_2), (0, 0))
    _shadow(canvas, (CARD_X, MARGIN, CARD_X + CARD_W, MARGIN + card_h), RADIUS)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        [CARD_X, MARGIN, CARD_X + CARD_W, MARGIN + card_h], radius=RADIUS, fill=C_CARD
    )

    head = _dgradient((CARD_W, HEAD_H), *head_grad)
    canvas.paste(head, (CARD_X, MARGIN), _mask_top_rounded((CARD_W, HEAD_H), RADIUS))
    return canvas, ImageDraw.Draw(canvas)


def _text(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: Tuple[int, int, int],
    *,
    right: Optional[int] = None,
) -> None:
    """绘制文字；给定 right 时改为右对齐（用于右侧金额）"""
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
    text_color: Tuple[int, int, int],
    *,
    dot_color: Optional[Tuple[int, int, int]] = None,
    bg: Tuple[int, int, int] = C_WHITE,
) -> None:
    """右上角白底胶囊标签（圆点为绘制图形，不依赖字体字形）"""
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


def _fmt_money(value: object, symbol: str = "¥") -> str:
    """金额文本；不是数字时原样带出来（如接口返回的 "--" 或异常说明）"""
    try:
        num = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return f"{symbol} {value}"
    return f"{symbol} {num:,.2f}"


def _money_or_dash(value: Optional[float], symbol: str) -> str:
    """没有余额数值时显示 --，避免卡片上出现「¥ None」"""
    return "--" if value is None else _fmt_money(value, symbol)


# ── 「与上次查询对比」的文字与配色 ────────────────────────────────────────────
def _delta_row_text(
    delta: float,
    pct: Optional[float],
    *,
    stale_at: Optional[datetime] = None,
) -> Tuple[str, Tuple[int, int, int]]:
    """单个账户增减的文案与颜色：↑变多（绿）/ ↓变少（红）/ → 持平（灰）

    stale_at 给出时说明这行的基准比整卡基准旧（例如上一轮该账户查询失败），
    此时直接把「是哪一次的数值」写进文案，避免「较上次」被误读成本轮上一次。
    """
    if abs(delta) < DELTA_FLOOR:
        text, color = "→ 0.00（持平）", C_DIM
    elif delta > 0:
        text, color = f"↑ +{abs(delta):,.2f}", C_GREEN_BAR
    else:
        text, color = f"↓ -{abs(delta):,.2f}", C_RED

    if stale_at is not None:
        text += f"（较 {stale_at:%m-%d %H:%M}）"
    elif pct is not None and abs(delta) >= DELTA_FLOOR:
        text += f"（{pct:+.1f}%）"
    return text, color


def _delta_group_text(
    delta: float, symbol: str
) -> Tuple[str, Tuple[int, int, int]]:
    """某币种合计增减的文案与颜色（带币种符号，因为它与金额不在同一处）"""
    if abs(delta) < DELTA_FLOOR:
        return "较上次 → 0.00", C_DIM
    if delta > 0:
        return f"较上次 ↑ {symbol} {abs(delta):,.2f}", C_GREEN_BAR
    return f"较上次 ↓ {symbol} {abs(delta):,.2f}", C_RED


def _row_delta(item: Dict[str, Any], baseline: Optional[datetime]) -> Tuple[str, Optional[Tuple[int, int, int]]]:
    """取一行在卡片上要画的增减文案与颜色；没有对比基准时返回「首次查询」"""
    extra = item.get("extra") or {}
    delta = extra.get("delta")
    if not isinstance(delta, (int, float)) or isinstance(delta, bool):
        if item.get("balance") is None:
            return "", None                     # 失败行不显示对比
        return "首次查询", C_DIM
    stale_at = None
    prev_at = extra.get("prev_at")
    if baseline is not None and isinstance(prev_at, (int, float)):
        # 与整卡基准相差超过 1 分钟，就认定这行用的是更早的一次记录
        if abs(float(prev_at) - baseline.timestamp()) > 60:
            stale_at = datetime.fromtimestamp(float(prev_at))
    pct = extra.get("delta_pct")
    return _delta_row_text(float(delta), pct if isinstance(pct, (int, float)) else None, stale_at=stale_at)


def _group_delta(group: dict) -> Tuple[str, Optional[Tuple[int, int, int]]]:
    """某币种分组的总增减文案；只有组内所有有数值的行都能算出差值时才给合计，

    否则（首次查询、上轮某家失败）宁可不显示，也不给一个口径不完整的合计数字。
    """
    rows = [
        item
        for item in group["items"]
        if item.get("balance") is not None and item.get("status") != "error"
    ]
    if not rows:
        return "", None

    total = 0.0
    for item in rows:
        delta = (item.get("extra") or {}).get("delta")
        if not isinstance(delta, (int, float)) or isinstance(delta, bool):
            return "", None
        total += float(delta)
    return _delta_group_text(total, group["symbol"])


def _human_interval(seconds: int) -> str:
    """把秒数写成「2 小时」这种可读形式（日志与页脚共用）"""
    seconds = max(int(seconds), 0)
    if seconds >= 3600 and seconds % 3600 == 0:
        return f"{seconds // 3600} 小时"
    if seconds >= 3600:
        return f"{seconds // 3600} 小时 {seconds % 3600 // 60} 分"
    if seconds >= 60:
        return f"{seconds // 60} 分钟"
    return f"{seconds} 秒"


def _clip(probe: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> str:
    """超宽就截断加省略号（单行展示用）"""
    if probe.textlength(text, font=font) <= max_w:
        return text
    out = ""
    for ch in text:
        if probe.textlength(out + ch + "…", font=font) > max_w:
            break
        out += ch
    return out + "…"


# ── 词感知折行（避免在英文单词中间断开） ──────────────────────────────────────
_WORD_CHARS = set("+-._/&%$#@='\"()[],:;?!")


def _tokenize(text: str) -> List[str]:
    """把文本切成「连续的 ASCII 词」与「单个非 ASCII 字符」，供折行时整词换行"""
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


def _wrap(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_w: int,
    max_lines: int = 8,
) -> List[str]:
    """按像素宽度折行；英文/数字不拆词，超长单词才硬切"""
    lines: List[str] = []
    cur = ""
    for token in _tokenize((text or "").strip()):
        cand = cur + token
        if draw.textlength(cand, font=font) <= max_w or not cur:
            if not cur and draw.textlength(token, font=font) > max_w:
                # 单个词就超宽 → 硬切
                piece = ""
                for ch in token:
                    if draw.textlength(piece + ch, font=font) <= max_w:
                        piece += ch
                    else:
                        break
                cur = piece or token[:1]
                rest = token[len(cur):]
                while rest:
                    if len(lines) >= max_lines:
                        return lines
                    lines.append(cur)
                    cur = ""
                    piece = ""
                    for ch in rest:
                        if draw.textlength(piece + ch, font=font) <= max_w:
                            piece += ch
                        else:
                            break
                    cur = piece or rest[:1]
                    rest = rest[len(cur):]
                continue
            cur = cand
            continue
        if len(lines) >= max_lines:
            lines.append(cur + "…")
            return lines
        lines.append(cur)
        cur = token.lstrip()
    if cur:
        lines.append(cur)
    return lines or [""]


# ── 多供应商卡片（DeepSeek + 其他家） ─────────────────────────────────────────
def render_multi(
    entries: Sequence[dict],
    *,
    now: Optional[datetime] = None,
    source: str = "手动查询",
    baseline: Optional[datetime] = None,
) -> bytes:
    """多供应商余额卡片：按币种分组，一家一行（错误行显示红色原因），返回图片字节

    baseline 是「上次查询」的时间，用于页脚写明对比基准；为 None 表示还没有历史。
    每行的增减取自 entry["extra"] 的 delta / delta_pct / prev_at（由 history.annotate 写入）。
    """
    now = now or datetime.now()
    entries = list(entries or [])
    probe = _probe()

    font_title = _font(FS_TITLE, bold=True)
    font_sub = _font(FS_SUB)
    font_pill = _font(FS_PILL, bold=True)
    font_sec = _font(FS_SEC, bold=True)
    font_row = _font(FS_ROW, bold=True)
    font_money = _font(FS_MONEY, bold=True)
    font_small = _font(FS_SMALL)
    font_delta = _font(FS_DELTA, bold=True)
    font_total = _font(20, bold=True)

    # 分组：人民币在前、美元其次、未知币种放最后（保持传入顺序）
    units = ["RMB", "USD"] + [
        u for u in dict.fromkeys(e.get("unit") for e in entries)
        if u not in ("RMB", "USD")
    ]
    groups: List[dict] = []
    for unit in units:
        items = [e for e in entries if e.get("unit") == unit]
        if not items:
            continue
        label, symbol = UNIT_LABEL.get(unit, (f"{unit} 账户", unit))
        groups.append({"unit": unit, "label": label, "symbol": symbol, "items": items})

    errors = [e for e in entries if e.get("status") == "error"]
    warnings = [e for e in entries if e.get("status") == "warning"]
    if errors:
        head_grad = (C_DANGER_1, C_DANGER_2)
        pill_text, pill_color, pill_dot = f"{len(errors)} 个异常", C_RED, C_RED
    elif warnings:
        head_grad = (C_BRAND_1, C_BRAND_2)
        pill_text, pill_color, pill_dot = f"{len(warnings)} 个提醒", C_AMBER, C_AMBER
    else:
        head_grad = (C_BRAND_1, C_BRAND_2)
        pill_text, pill_color, pill_dot = "全部正常", C_BLUE, C_GREEN_BAR

    # 先按行高算总高，才能确定画布尺寸
    body_h = 0
    for group in groups:
        body_h += GROUP_LABEL_H + sum(ROW_H + ROW_GAP for _ in group["items"])
    body_h += GROUP_GAP * (len(groups) - 1) if groups else 0

    # 页脚：数据来源 + 查询时间 + 对比基准（基准那截太长时自动折成两行）
    if baseline is not None:
        elapsed = _human_interval(max(int((now - baseline).total_seconds()), 0))
        footer_text = (
            f"数据来源：api.deepseek.com 及各家 API · 查询时间 {now:%H:%M:%S}"
            f" · 对比基准 {baseline:%m-%d %H:%M}（{elapsed}前）"
        )
    else:
        footer_text = (
            f"数据来源：api.deepseek.com 及各家 API · 查询时间 {now:%H:%M:%S}"
            f" · 首次查询，暂无对比"
        )
    footer_lines = _wrap(probe, footer_text, font_small, CONTENT_R - CONTENT_X, max_lines=2)
    footer_line_h = int(probe.textbbox((0, 0), "Ag", font=font_small)[3]) + 6

    body_top = MARGIN + HEAD_H + 26
    total_y = body_top + body_h + 10
    footer_y = total_y + 52
    card_h = int(footer_y + len(footer_lines) * footer_line_h + 31) - MARGIN

    canvas, draw = _new_card(card_h, head_grad)

    _text(draw, CONTENT_X, MARGIN + 26, "AI 账户余额", font_title, C_WHITE)
    _text(
        draw,
        CONTENT_X,
        MARGIN + 78,
        f"{len(entries)} 个账户 · {source} · {now:%Y-%m-%d %H:%M}",
        font_sub,
        (240, 244, 255),
    )
    _pill(draw, CONTENT_R, MARGIN + 28, pill_text, font_pill, pill_color, dot_color=pill_dot)

    # 逐组绘制
    name_x = CONTENT_X + 48
    detail_max_w = CONTENT_R - name_x - 22
    totals: List[str] = []
    y = body_top

    for gi, group in enumerate(groups):
        symbol = group["symbol"]
        head_delta_text, head_delta_color = _group_delta(group)
        _text(draw, CONTENT_X, y, group["label"], font_sec, C_DIM)
        line_y = y + GROUP_LABEL_H // 2 + 2
        label_w = probe.textlength(group["label"], font=font_sec)
        line_end = CONTENT_R
        if head_delta_text:
            # 组标题右侧给该币种的合计增减，分隔线让位给它
            _text(draw, CONTENT_R, y, head_delta_text, font_sec, head_delta_color or C_DIM, right=CONTENT_R)
            line_end = CONTENT_R - probe.textlength(head_delta_text, font=font_sec) - 16
            line_end = max(line_end, CONTENT_X + label_w + 30)
        draw.line(
            [(CONTENT_X + label_w + 14, line_y), (line_end, line_y)],
            fill=C_LINE,
            width=1,
        )
        y += GROUP_LABEL_H

        sum_value = 0.0
        for item in group["items"]:
            status = item.get("status", "ok")
            dot = {"ok": C_GREEN_BAR, "warning": C_AMBER, "error": C_RED}.get(status, C_TRACK)
            amount_color = {"ok": C_TEXT, "warning": C_AMBER, "error": C_RED}.get(status, C_TEXT)

            draw.rounded_rectangle(
                [CONTENT_X, y, CONTENT_R, y + ROW_H], radius=18, fill=C_SOFT, outline=C_LINE, width=1
            )
            dot_cy = y + 26
            draw.ellipse([CONTENT_X + 18, dot_cy - 6, CONTENT_X + 30, dot_cy + 6], fill=dot)

            _text(draw, name_x, y + 14, str(item.get("title", "?")), font_row, C_TEXT)

            money = _money_or_dash(item.get("balance"), symbol)
            _text(draw, CONTENT_R - 22, y + 13, money, font_money, amount_color, right=CONTENT_R - 22)

            # 金额正下方：与上次查询的增减（绿升红降，数字量化）
            delta_text, delta_color = _row_delta(item, baseline)
            delta_w = int(probe.textlength(delta_text, font=font_delta)) if delta_text else 0
            detail_w = detail_max_w - (delta_w + 18 if delta_w else 0)

            _text(
                draw,
                name_x,
                y + 50,
                _clip(probe, str(item.get("detail", "")), font_small, max(detail_w, 90)),
                font_small,
                C_RED if status == "error" else C_DIM,
            )

            if delta_text:
                _text(
                    draw,
                    CONTENT_R - 22,
                    y + 54,
                    delta_text,
                    font_delta,
                    delta_color or C_DIM,
                    right=CONTENT_R - 22,
                )

            extra = item.get("extra") or {}
            used, total = extra.get("used"), extra.get("total")
            if used is not None or total is not None:
                bits = []
                if used is not None:
                    bits.append(f"已用 {_fmt_money(used, symbol)}")
                if total is not None:
                    bits.append(f"总额 {_fmt_money(total, symbol)}")
                _text(draw, name_x, y + 71, " · ".join(bits), font_small, C_DIM)

            if item.get("balance") is not None and status != "error":
                sum_value += float(item["balance"])
            y += ROW_H + ROW_GAP

        if group["items"]:
            y -= ROW_GAP
        totals.append(f"{group['label'][:2]} {symbol} {sum_value:,.2f}")
        if gi != len(groups) - 1:
            y += GROUP_GAP

    # 合计 + 页脚
    draw.line([(CONTENT_X, total_y), (CONTENT_R, total_y)], fill=C_LINE, width=1)
    _text(draw, CONTENT_X, total_y + 16, "合计  " + "   ·   ".join(totals), font_total, C_TEXT)

    draw.line([(CONTENT_X, footer_y - 24), (CONTENT_R, footer_y - 24)], fill=C_LINE, width=1)
    for index, line in enumerate(footer_lines):
        _text(draw, CONTENT_X, footer_y + index * footer_line_h, line, font_small, C_DIM)

    buf = BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ── 错误卡片 ──────────────────────────────────────────────────────────────────
def render_error(message: str, *, now: Optional[datetime] = None) -> bytes:
    """查询失败时的提示卡片（与余额卡片同一套视觉），返回图片字节"""
    now = now or datetime.now()
    probe = _probe()
    font = _font(17)
    lines = _wrap(probe, message, font, CONTENT_R - CONTENT_X, max_lines=10)

    line_h = probe.textbbox((0, 0), "Ag", font=font)[3] + 12
    body_h = len(lines) * line_h
    card_h = HEAD_H + 36 + body_h + 30

    canvas, draw = _new_card(card_h, (C_DANGER_1, C_DANGER_2))

    _text(
        draw,
        CONTENT_X,
        MARGIN + 26,
        "AI 账户余额查询失败",
        _font(FS_TITLE, bold=True),
        C_WHITE,
    )
    _text(
        draw,
        CONTENT_X,
        MARGIN + 78,
        f"查询时间 {now:%Y-%m-%d %H:%M}",
        _font(FS_SUB),
        (255, 240, 236),
    )
    _pill(
        draw,
        CONTENT_R,
        MARGIN + 28,
        "请求异常",
        _font(FS_PILL, bold=True),
        C_RED,
        dot_color=C_RED,
    )

    y = MARGIN + HEAD_H + 36
    for line in lines:
        _text(draw, CONTENT_X, y, line, font, C_TEXT)
        y += line_h

    buf = BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
