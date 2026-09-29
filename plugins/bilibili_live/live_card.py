"""开播卡片渲染 —— 把「封面图 + 文字」合成一张可爱风格的图，供 bilibili_live 的通知使用。

两个入口：
  * build_live_card(...)   —— 开播通知（封面 + 名字/标题 + 在线人数 + 链接）
  * build_hourly_card(...) —— 直播中每小时播报（封面 + 名字 + 已播时长/在线人数 + 最近 10 条弹幕）
两者都返回 JPEG 字节；渲染失败返回 None，调用方自行退回文字版。

CLI 用法（自己出图检查排版，不连 QQ）：
    python live_card.py out.jpg [封面图路径] [--name 名字] [--title 标题] [--room 房间号] [--online 1.9万]

样式说明：
  * 浅粉 → 浅蓝紫的渐变底 + 白色圆角卡片 + 粉色描边 + 手绘爱心，整体走可爱风；
  * 不依赖 emoji 字形（微软雅黑里没有彩色 emoji），红点、爱心都是画出来的，
    所以在 Windows / Linux / macOS 上渲染结果一致。
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

# ── 尺寸与配色 ────────────────────────────────────────────────────────────────
CANVAS_W, CANVAS_H = 700, 640
MARGIN = 24                      # 卡片到画布边缘的留白
CARD_RADIUS = 34

COVER_W, COVER_H = 596, 335      # 16:9
COVER_RADIUS = 26
COVER_RADIUS_TOP = 26

BG_START = (255, 228, 240)       # 浅粉
BG_END = (230, 236, 255)         # 浅蓝紫

CARD_FILL = (255, 255, 255)
CARD_BORDER = (255, 211, 228)

INK_TITLE = (61, 61, 82)         # 名字
INK_BODY = (110, 110, 133)       # 直播标题
INK_ONLINE = (255, 92, 138)      # 在线人数
INK_LINK = (76, 125, 240)        # 直播间链接
INK_PILL = (255, 77, 109)        # 直播中 标签
DIVIDER = (255, 224, 236)

HEART_BG = (255, 217, 234)       # 背景上的爱心
HEART_CARD = (255, 230, 240)     # 卡片里的爱心

# 中文字体候选，取第一个存在的（粗体 / 常规各一份）
_FONT_BOLD_CANDIDATES = (
    "C:/Windows/Fonts/msyhbd.ttc",      # 微软雅黑 粗体
    "C:/Windows/Fonts/simhei.ttf",      # 黑体
    "C:/Windows/Fonts/msyh.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
)
_FONT_REGULAR_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",        # 微软雅黑
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simkai.ttf",      # 楷体
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
)

_FONT_CACHE: dict[tuple[bool, int], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}


def _pick_font_path(bold: bool) -> Optional[str]:
    """挑一个存在的中文字体文件。"""
    for candidate in (_FONT_BOLD_CANDIDATES if bold else _FONT_REGULAR_CANDIDATES):
        if Path(candidate).exists():
            return candidate
    return None


def _load_font(size: int, bold: bool = False):
    """按 (粗体, 字号) 缓存字体对象；一个中文字体都没有时退回 PIL 默认字体。"""
    key = (bold, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

    path = _pick_font_path(bold) or _pick_font_path(not bold)
    font = None
    if path:
        try:
            font = ImageFont.truetype(path, size=size)
        except Exception:  # noqa: BLE001 — 字体坏了就退回默认字体，不能因此不出图
            font = None
    if font is None:
        font = ImageFont.load_default(size=size)
    _FONT_CACHE[key] = font
    return font


# ── 绘制小工具 ────────────────────────────────────────────────────────────────
def _gradient(w: int, h: int) -> Image.Image:
    """斜向线性渐变的底图（浅粉 → 浅蓝紫）。"""
    base = Image.new("RGB", (w, h), BG_END)
    draw = ImageDraw.Draw(base)
    for y in range(h):
        for step in range(0, 1):  # 每行只画一次，减少循环开销
            ratio_y = y / max(h - 1, 1)
            row_color = tuple(
                int(BG_START[i] + (BG_END[i] - BG_START[i]) * ratio_y) for i in range(3)
            )
            draw.line([(0, y), (w, y)], fill=row_color)
    # 再叠一层横向渐变，让左上角更粉一点
    overlay = Image.new("L", (w, h), 0)
    odraw = ImageDraw.Draw(overlay)
    for x in range(w):
        odraw.line([(x, 0), (x, h)], fill=int(70 * (1 - x / max(w - 1, 1))))
    pink = Image.new("RGB", (w, h), BG_START)
    base = Image.composite(Image.blend(base, pink, 0.35), base, overlay)
    return base


def _draw_heart(draw: ImageDraw.ImageDraw, cx: float, cy: float, size: float, color) -> None:
    """画一颗爱心：两个圆 + 一个三角，不依赖字体里的 ♥ 字形。"""
    radius = size / 4
    draw.ellipse([cx - size / 2, cy - size / 4, cx, cy + size / 4], fill=color)
    draw.ellipse([cx, cy - size / 4, cx + size / 2, cy + size / 4], fill=color)
    draw.polygon(
        [(cx - size / 2, cy), (cx + size / 2, cy), (cx, cy + size / 2)],
        fill=color,
    )


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    """超宽就截断并加省略号。"""
    if draw.textlength(text, font=font) <= max_width:
        return text
    ellipsis = "…"
    while text and draw.textlength(text + ellipsis, font=font) > max_width:
        text = text[:-1]
    return (text + ellipsis) if text else ellipsis


def _load_cover(cover_bytes: Optional[bytes], size: tuple[int, int]) -> Optional[Image.Image]:
    """把封面裁成目标尺寸（等比缩放后居中裁剪）。"""
    if not cover_bytes:
        return None
    try:
        with Image.open(io.BytesIO(cover_bytes)) as image:
            image = image.convert("RGB")
            target_w, target_h = size
            scale = max(target_w / image.width, target_h / image.height)
            resized = image.resize(
                (max(int(image.width * scale), target_w), max(int(image.height * scale), target_h)),
                Image.Resampling.LANCZOS,
            )
            left = (resized.width - target_w) // 2
            top = (resized.height - target_h) // 2
            return resized.crop((left, top, left + target_w, top + target_h))
    except Exception:  # noqa: BLE001 — 封面坏了就当没有封面，用占位底色
        return None


def _placeholder_cover(size: tuple[int, int]) -> Image.Image:
    """没有封面时的占位图：浅粉底 + 一颗大爱心。"""
    w, h = size
    image = Image.new("RGB", (w, h), (255, 240, 247))
    draw = ImageDraw.Draw(image)
    _draw_heart(draw, w / 2, h / 2 - 10, 120, (255, 205, 225))
    return image


# ── 主渲染 ────────────────────────────────────────────────────────────────────
def _render(
    uname: str,
    title: str,
    room_id: str,
    online_text: str,
    cover_bytes: Optional[bytes],
) -> Image.Image:
    canvas = _gradient(CANVAS_W, CANVAS_H)
    draw = ImageDraw.Draw(canvas, "RGBA")

    # 背景上零散的小爱心
    for cx, cy, size in ((52, 70, 22), (648, 96, 17), (40, 560, 16), (662, 540, 21)):
        _draw_heart(draw, cx, cy, size, HEART_BG)

    # 卡片（先画一层浅阴影，再画白卡片）
    card_box = (MARGIN, MARGIN, CANVAS_W - MARGIN, CANVAS_H - MARGIN)
    draw.rounded_rectangle(
        (card_box[0] + 3, card_box[1] + 5, card_box[2] + 3, card_box[3] + 5),
        radius=CARD_RADIUS,
        fill=(214, 200, 224, 90),
    )
    draw.rounded_rectangle(card_box, radius=CARD_RADIUS, fill=CARD_FILL)
    draw.rounded_rectangle(
        card_box, radius=CARD_RADIUS, outline=CARD_BORDER, width=3
    )

    inner_left = card_box[0] + 28
    inner_right = card_box[2] - 28
    inner_width = inner_right - inner_left

    # 封面（圆角裁剪后贴进卡片）
    cover_box = (inner_left, card_box[1] + 28, inner_left + COVER_W, card_box[1] + 28 + COVER_H)
    cover = _load_cover(cover_bytes, (COVER_W, COVER_H)) or _placeholder_cover((COVER_W, COVER_H))
    mask = Image.new("L", (COVER_W, COVER_H), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, COVER_W - 1, COVER_H - 1), radius=COVER_RADIUS, fill=255)
    canvas.paste(cover, (cover_box[0], cover_box[1]), mask)
    draw.rounded_rectangle(cover_box, radius=COVER_RADIUS, outline=(255, 255, 255), width=3)

    # 封面左上角的「直播中」标签：白点 + 文案，全是画出来的
    pill_box = (cover_box[0] + 18, cover_box[1] + 18, cover_box[0] + 18 + 132, cover_box[1] + 18 + 44)
    draw.rounded_rectangle(pill_box, radius=22, fill=INK_PILL)
    dot_cx, dot_cy = pill_box[0] + 26, (pill_box[1] + pill_box[3]) / 2
    draw.ellipse([dot_cx - 7, dot_cy - 7, dot_cx + 7, dot_cy + 7], fill=(255, 255, 255))
    pill_font = _load_font(22, bold=True)
    draw.text((dot_cx + 15, dot_cy), "直播中", font=pill_font, fill=(255, 255, 255), anchor="lm")

    # 名字 + 开播啦
    name_font = _load_font(38, bold=True)
    room_url = f"https://live.bilibili.com/{room_id}" if room_id != "" else "https://live.bilibili.com"
    name_text = _fit_text(draw, f"{uname} 开播啦~", name_font, inner_width)
    name_y = cover_box[3] + 26
    draw.text((inner_left, name_y), name_text, font=name_font, fill=INK_TITLE)

    # 直播标题
    title_font = _load_font(24)
    title_text = _fit_text(draw, title or "未知标题", title_font, inner_width)
    title_y = name_y + 56
    draw.text((inner_left, title_y), title_text, font=title_font, fill=INK_BODY)

    # 分隔线
    divider_y = title_y + 46
    draw.line([(inner_left, divider_y), (inner_right, divider_y)], fill=DIVIDER, width=2)

    # 底部一行：在线人数（左） + 直播间链接（右对齐）
    meta_font = _load_font(23, bold=True)
    link_font = _load_font(21)
    meta_y = divider_y + 20
    online_label = f"在线 {online_text} 人在看" if online_text else "在线人数未知"
    draw.text((inner_left, meta_y), online_label, font=meta_font, fill=INK_ONLINE)
    link_width = draw.textlength(room_url, font=link_font)
    draw.text((inner_right - link_width, meta_y + 3), room_url, font=link_font, fill=INK_LINK)

    # 卡片右下角的小爱心点缀
    _draw_heart(draw, inner_right - 16, meta_y + 74, 20, HEART_CARD)
    _draw_heart(draw, inner_right - 52, meta_y + 82, 13, HEART_CARD)

    return canvas


def build_live_card(
    uname: str,
    title: str,
    room_id: str | int = "",
    online_text: str = "",
    cover_bytes: Optional[bytes] = None,
) -> Optional[bytes]:
    """渲染开播卡片，返回 PNG 字节；任何异常都返回 None（调用方退回文字版）。"""
    try:
        image = _render(
            uname=str(uname),
            title=str(title),
            room_id=str(room_id),
            online_text=str(online_text or ""),
            cover_bytes=cover_bytes,
        )
        buffer = io.BytesIO()
        # 卡片里有照片，JPEG 比 PNG 小一个量级；质量 92 时这两档字号的文字依然清晰
        image.convert("RGB").save(buffer, format="JPEG", quality=92, optimize=True)
        return buffer.getvalue()
    except Exception:  # noqa: BLE001 — 出图失败不该影响开播通知本身
        return None


# ── 直播中播报卡片 ────────────────────────────────────────────────────────────
HOURLY_ROW_H = 42            # 每条弹幕占的高度
HOURLY_MAX_DANMAKU = 10      # 播报卡里最多列几条弹幕


def _draw_live_pill(draw: ImageDraw.ImageDraw, x: float, y: float) -> None:
    """封面左上角的「直播中」标签：红底 + 白点 + 白字，全是画出来的。"""
    box = (x, y, x + 132, y + 44)
    draw.rounded_rectangle(box, radius=22, fill=INK_PILL)
    dot_cx, dot_cy = box[0] + 26, (box[1] + box[3]) / 2
    draw.ellipse([dot_cx - 7, dot_cy - 7, dot_cx + 7, dot_cy + 7], fill=(255, 255, 255))
    draw.text((dot_cx + 15, dot_cy), "直播中", font=_load_font(22, bold=True), fill=(255, 255, 255), anchor="lm")


def _draw_chip(
    draw: ImageDraw.ImageDraw, x: float, y: float, text: str, font, bg, fg
) -> float:
    """画一枚圆角信息胶囊，返回它的右边缘 x（方便接着画下一个）。"""
    width = int(draw.textlength(text, font=font)) + 36
    draw.rounded_rectangle((x, y, x + width, y + 42), radius=21, fill=bg)
    draw.text((x + 18, y + 21), text, font=font, fill=fg, anchor="lm")
    return x + width


def build_hourly_card(
    uname: str,
    duration_text: str,
    online_text: str,
    room_id: str | int = "",
    cover_bytes: Optional[bytes] = None,
    danmaku: Optional[list[tuple[str, str]]] = None,
) -> Optional[bytes]:
    """直播中每小时播报的卡片：封面 + 名字 + 已播时长 / 在线人数 + 最近几条弹幕。

    danmaku 传 [(用户名, 弹幕原文), ...]，只取最后 HOURLY_MAX_DANMAKU 条。
    渲染失败返回 None，调用方退回原来的文字播报。
    """
    try:
        rows = [(str(user), str(text)) for user, text in (danmaku or []) if str(text).strip()]
        image = _render_hourly(
            uname=str(uname),
            duration_text=str(duration_text),
            online_text=str(online_text or ""),
            room_id=str(room_id),
            cover_bytes=cover_bytes,
            danmaku=rows[-HOURLY_MAX_DANMAKU:],
        )
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=92, optimize=True)
        return buffer.getvalue()
    except Exception:  # noqa: BLE001 — 出图失败不该影响播报本身
        return None


def _render_hourly(
    uname: str,
    duration_text: str,
    online_text: str,
    room_id: str,
    cover_bytes: Optional[bytes],
    danmaku: list[tuple[str, str]],
) -> Image.Image:
    row_count = max(len(danmaku), 1)

    inner_top = MARGIN + 28
    cover_bottom = inner_top + COVER_H
    name_y = cover_bottom + 24
    chips_y = name_y + 56
    link_y = chips_y + 50          # 链接单独一行，避免和「在线 X 人」胶囊撞在一起
    divider_y = link_y + 30
    header_y = divider_y + 24
    rows_y = header_y + 46
    canvas_h = rows_y + row_count * HOURLY_ROW_H + 56

    canvas = _gradient(CANVAS_W, canvas_h)
    draw = ImageDraw.Draw(canvas, "RGBA")

    for cx, cy, size in ((54, 74, 22), (646, 100, 17)):
        _draw_heart(draw, cx, cy, size, HEART_BG)

    card_box = (MARGIN, MARGIN, CANVAS_W - MARGIN, canvas_h - MARGIN)
    draw.rounded_rectangle(
        (card_box[0] + 3, card_box[1] + 5, card_box[2] + 3, card_box[3] + 5),
        radius=CARD_RADIUS,
        fill=(214, 200, 224, 90),
    )
    draw.rounded_rectangle(card_box, radius=CARD_RADIUS, fill=CARD_FILL)
    draw.rounded_rectangle(card_box, radius=CARD_RADIUS, outline=CARD_BORDER, width=3)

    inner_left = card_box[0] + 28
    inner_right = card_box[2] - 28
    inner_width = inner_right - inner_left

    # 封面（圆角裁剪）
    cover_box = (inner_left, inner_top, inner_left + COVER_W, cover_bottom)
    cover = _load_cover(cover_bytes, (COVER_W, COVER_H)) or _placeholder_cover((COVER_W, COVER_H))
    mask = Image.new("L", (COVER_W, COVER_H), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, COVER_W - 1, COVER_H - 1), radius=COVER_RADIUS, fill=255
    )
    canvas.paste(cover, (cover_box[0], cover_box[1]), mask)
    draw.rounded_rectangle(cover_box, radius=COVER_RADIUS, outline=(255, 255, 255), width=3)
    _draw_live_pill(draw, cover_box[0] + 18, cover_box[1] + 18)

    # 名字
    name_font = _load_font(36, bold=True)
    room_url = f"https://live.bilibili.com/{room_id}" if room_id else "https://live.bilibili.com"
    draw.text(
        (inner_left, name_y),
        _fit_text(draw, f"{uname} 正在直播", name_font, inner_width),
        font=name_font,
        fill=INK_TITLE,
    )

    # 已播时长 / 在线人数两枚胶囊；链接单独一行，避免长文案时互相压字
    chip_font = _load_font(22, bold=True)
    chip_x = _draw_chip(draw, inner_left, chips_y, f"已播 {duration_text}", chip_font, (255, 231, 241), INK_ONLINE)
    online_label = f"在线 {online_text} 人" if online_text else "在线人数未知"
    _draw_chip(draw, chip_x + 12, chips_y, online_label, chip_font, (237, 233, 255), (108, 92, 231))

    link_font = _load_font(20)
    draw.text(
        (inner_left + 4, link_y),
        _fit_text(draw, room_url, link_font, inner_width - 8),
        font=link_font,
        fill=INK_LINK,
    )

    draw.line([(inner_left, divider_y), (inner_right, divider_y)], fill=DIVIDER, width=2)

    # 「最近弹幕」标题：画一个小小的对话气泡，不用 emoji 字形
    header_font = _load_font(25, bold=True)
    bubble = (inner_left, header_y + 4, inner_left + 30, header_y + 26)
    draw.rounded_rectangle(bubble, radius=9, fill=(255, 209, 227))
    draw.polygon(
        [(inner_left + 8, header_y + 25), (inner_left + 19, header_y + 25), (inner_left + 11, header_y + 34)],
        fill=(255, 209, 227),
    )
    draw.text((inner_left + 44, header_y), "最近弹幕", font=header_font, fill=INK_TITLE)

    # 弹幕行
    row_font = _load_font(20)
    user_font = _load_font(20, bold=True)
    for index in range(row_count):
        top_y = rows_y + index * HOURLY_ROW_H
        fill = (255, 246, 250) if index % 2 == 0 else (247, 246, 255)
        draw.rounded_rectangle((inner_left, top_y, inner_right, top_y + 34), radius=12, fill=fill)
        if index < len(danmaku):
            user, text = danmaku[index]
            label = _fit_text(draw, f"{user}：", user_font, int(inner_width * 0.45))
            draw.text((inner_left + 12, top_y + 6), label, font=user_font, fill=INK_ONLINE)
            label_width = draw.textlength(label, font=user_font)
            content = _fit_text(draw, text, row_font, max(int(inner_width - 24 - label_width), 40))
            draw.text((inner_left + 12 + label_width, top_y + 6), content, font=row_font, fill=INK_BODY)
        else:
            draw.text(
                (inner_left + 12, top_y + 6), "这会儿还没抓到弹幕～", font=row_font, fill=INK_BODY
            )

    # 卡片右下角的爱心点缀
    _draw_heart(draw, inner_right - 18, rows_y + row_count * HOURLY_ROW_H + 30, 20, HEART_CARD)
    _draw_heart(draw, inner_right - 54, rows_y + row_count * HOURLY_ROW_H + 38, 13, HEART_CARD)

    return canvas


def _main() -> None:
    """CLI：自己出图检查排版。"""
    args = sys.argv[1:]
    if not args:
        raise SystemExit(__doc__)

    out = Path(args[0])
    cover_path = Path(args[1]) if len(args) > 1 and not args[1].startswith("--") else None

    def option(name: str, default: str) -> str:
        if name in args:
            return args[args.index(name) + 1]
        return default

    cover_bytes = cover_path.read_bytes() if cover_path and cover_path.exists() else None
    png = build_live_card(
        uname=option("--name", "小和尚济海"),
        title=option("--title", "大巴扎：冲冲冲玩好玩的"),
        room_id=option("--room", "8767907"),
        online_text=option("--online", "1.9万"),
        cover_bytes=cover_bytes,
    )
    if png is None:
        raise SystemExit("渲染失败")
    out.write_bytes(png)
    print(f"已写出 {out}（{len(png)} 字节）")


if __name__ == "__main__":
    _main()
