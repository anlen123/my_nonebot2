"""在线人数折线图 —— 下播通知里附带的那张曲线图，风格与开播/播报卡片一致。

模块用法：
    from .online_chart import build_online_chart
    jpeg = build_online_chart(
        [(“22:03”, 1200), (“22:04”, 1350), ...],
        uname="无敌小机器人大王", duration_text="2小时10分0秒",
    )   # JPEG 字节；点数少于 2 个或渲染失败返回 None

CLI 用法（自己出图检查排版，不连 QQ）：
    python online_chart.py out.jpg [--name 名字] [--duration 1小时] [--peak 4200]
"""

from __future__ import annotations

import io
import sys
from typing import Optional, Sequence

from PIL import Image, ImageDraw

from .live_card import (
    BG_END,
    BG_START,
    CARD_BORDER,
    CARD_FILL,
    CARD_RADIUS,
    DIVIDER,
    HEART_BG,
    HEART_CARD,
    INK_BODY,
    INK_ONLINE,
    INK_TITLE,
    MARGIN,
    _draw_heart,
    _fit_text,
    _gradient,
    _load_font,
)

CANVAS_W = 700
PLOT_H = 250
LINE_COLOR = (255, 122, 167)
FILL_COLOR = (255, 214, 231, 170)
GRID = (240, 233, 248)
AXIS_INK = (176, 168, 196)
PEAK_INK = (255, 92, 138)


def _fmt_people(value: float) -> str:
    """在线人数按「万」折算，和文字版口径一致。"""
    if value >= 10000:
        return f"{value / 10000:.1f}万"
    return str(int(value))


def build_online_chart(
    samples: Sequence[tuple[str, int]],
    uname: str = "",
    duration_text: str = "",
    title: str = "在线人数曲线",
) -> Optional[bytes]:
    """把 [(时间标签, 在线人数), ...] 画成一张可爱风格的折线图。"""
    points = [(str(label), int(value)) for label, value in (samples or []) if value is not None]
    if len(points) < 2:
        return None
    try:
        image = _render_chart(points, uname, duration_text, title)
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=92, optimize=True)
        return buffer.getvalue()
    except Exception:  # noqa: BLE001 — 出图失败不该影响下播通知
        return None


def _render_chart(
    points: list[tuple[str, int]],
    uname: str,
    duration_text: str,
    title: str,
) -> Image.Image:
    values = [value for _, value in points]
    peak = max(values)
    peak_index = values.index(peak)

    title_y = MARGIN + 28
    sub_y = title_y + 44
    plot_top = sub_y + 38
    plot_bottom = plot_top + PLOT_H
    canvas_h = plot_bottom + 78

    canvas = _gradient(CANVAS_W, canvas_h).convert("RGBA")
    for cx, cy, size in ((54, 74, 22), (646, 100, 17)):
        _draw_heart(ImageDraw.Draw(canvas, "RGBA"), cx, cy, size, HEART_BG)

    draw = ImageDraw.Draw(canvas, "RGBA")
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

    # 标题 + 峰值胶囊
    title_font = _load_font(28, bold=True)
    draw.text((inner_left, title_y), title, font=title_font, fill=INK_TITLE)
    chip_font = _load_font(21, bold=True)
    chip_text = f"最高 {_fmt_people(peak)} 人"
    chip_w = int(draw.textlength(chip_text, font=chip_font)) + 34
    chip_box = (inner_right - chip_w, title_y + 2, inner_right, title_y + 42)
    draw.rounded_rectangle(chip_box, radius=20, fill=(255, 231, 241))
    draw.text((chip_box[0] + 17, (chip_box[1] + chip_box[3]) / 2), chip_text,
              font=chip_font, fill=PEAK_INK, anchor="lm")

    sub_font = _load_font(20)
    subtitle = f"{uname} · 已播 {duration_text}" if duration_text else uname
    draw.text((inner_left, sub_y), _fit_text(draw, subtitle, sub_font, inner_width), font=sub_font, fill=INK_BODY)

    # ── 绘图区 ────────────────────────────────────────────────────────────────
    plot_left = inner_left + 58
    plot_right = inner_right
    plot_w = plot_right - plot_left
    y_max = max(peak * 1.15, 1)
    if len(set(values)) == 1:      # 全程平线时给一点向上空间，别贴着顶
        y_max = max(peak * 1.4, 1)

    def to_xy(index: int, value: int) -> tuple[float, float]:
        x = plot_left + plot_w * (index / (len(points) - 1))
        y = plot_bottom - (plot_bottom - plot_top) * (value / y_max)
        return x, y

    # 网格线 + 左侧刻度
    axis_font = _load_font(18)
    for ratio in (0.0, 0.5, 1.0):
        y = plot_bottom - (plot_bottom - plot_top) * ratio
        draw.line([(plot_left, y), (plot_right, y)], fill=GRID, width=2)
        draw.text((plot_left - 12, y), _fmt_people(y_max * ratio), font=axis_font, fill=AXIS_INK, anchor="rm")

    # 折线下方的粉色填充
    coords = [to_xy(index, value) for index, value in enumerate(values)]
    polygon = [(coords[0][0], plot_bottom)] + coords + [(coords[-1][0], plot_bottom)]
    draw.polygon(polygon, fill=FILL_COLOR)

    # 折线本体
    draw.line(coords, fill=LINE_COLOR, width=5, joint="curve")

    # 点：点少时全部画出来，点多时只标峰值
    if len(coords) <= 40:
        for x, y in coords:
            draw.ellipse([x - 4, y - 4, x + 4, y + 4], fill=(255, 255, 255), outline=LINE_COLOR, width=3)
    peak_x, peak_y = coords[peak_index]
    draw.ellipse([peak_x - 7, peak_y - 7, peak_x + 7, peak_y + 7], fill=PEAK_INK)
    _draw_heart(draw, peak_x, peak_y - 26, 14, (255, 138, 176, 235))

    # 横轴时间标签：首 / 中 / 尾
    label_font = _load_font(18)
    draw.line([(plot_left, plot_bottom), (plot_right, plot_bottom)], fill=GRID, width=2)
    for index, anchor in ((0, "lm"), (len(points) // 2, "mm"), (len(points) - 1, "rm")):
        x, _ = to_xy(index, 0)
        draw.text((x, plot_bottom + 16), points[index][0], font=label_font, fill=AXIS_INK, anchor=anchor)

    draw.line([(inner_left, canvas_h - MARGIN - 30), (inner_right, canvas_h - MARGIN - 30)], fill=DIVIDER, width=2)
    _draw_heart(draw, inner_right - 18, canvas_h - MARGIN - 14, 18, HEART_CARD)
    _draw_heart(draw, inner_left + 18, canvas_h - MARGIN - 14, 13, HEART_CARD)

    return canvas


def _main() -> None:
    """CLI：用一条合成曲线出图，方便看排版。"""
    args = sys.argv[1:]
    out = args[0] if args else "online_chart.jpg"
    name = "无敌小机器人大王"
    duration = "2小时10分0秒"
    peak = 4200
    for index, arg in enumerate(args):
        if arg == "--name" and index + 1 < len(args):
            name = args[index + 1]
        elif arg == "--duration" and index + 1 < len(args):
            duration = args[index + 1]
        elif arg == "--peak" and index + 1 < len(args):
            peak = int(args[index + 1])

    samples = []
    for i in range(40):
        wave = 1 + 0.45 * ((i % 11) / 10) + 0.15 * ((i % 5) / 4)
        samples.append((f"{21 + (i // 12):02d}:{(i * 5) % 60:02d}", int(peak * wave / 1.6)))
    data = build_online_chart(samples, uname=name, duration_text=duration)
    if not data:
        print("出图失败")
        return
    with open(out, "wb") as handle:
        handle.write(data)
    print(f"已生成 {out}（{len(data) // 1024} KB）")


if __name__ == "__main__":
    _main()
