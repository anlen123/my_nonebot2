"""词云图片生成器 —— bilibili_live 的弹幕词云，也可以单独当 CLI 用。

出图思路：先用 wordcloud 生成一张透明底的词云，再贴到「浅粉→浅蓝紫」渐变卡片上，
加标题和统计信息，整体和马卡龙变色，最后转 JPEG（比 PNG 小一个量级）。

模块用法：
    from .wordcloud_generator import build_wordcloud_from_texts
    jpeg = build_wordcloud_from_texts(["弹幕1", "弹幕2"], uname="名字", duration_text="1小时")
    # JPEG 字节，失败或无有效文本返回 None

CLI 用法：
    python wordcloud_generator.py [文本文件路径]          # 生成图片到 wordcloud.jpg
    python wordcloud_generator.py --show [文本文件路径]   # 生成后再弹窗预览
"""

from __future__ import annotations

import io
import logging
import os
import random
import sys
from collections import Counter
from typing import List, Optional, Sequence

import jieba

# 词云只需要出图，先切到非交互后端，免得 CLI 加了 --show 以后卡在窗口上
import matplotlib

matplotlib.use("Agg")
from wordcloud import WordCloud

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("bilibili_live")

# ── 卡片样式（与 live_card 同一套配色，改样式时两处一起改）──────────────────────
_CARD_W = 900
_MARGIN = 22
_CARD_RADIUS = 30
_BG_START = (255, 228, 240)
_BG_END = (230, 236, 255)
_CARD_FILL = (255, 255, 255)
_CARD_BORDER = (255, 211, 228)
_INK_TITLE = (61, 61, 82)
_INK_BODY = (140, 134, 160)
_HEART_BG = (255, 217, 234)
_HEART_CARD = (255, 230, 240)

# 马卡龙配色：在白底上要够清楚，又不能太扎眼（冷暖各留几支，避免颜色过杂）
_PASTEL = (
    "#FF6F91", "#F5789A", "#FF9671", "#FFA45B",
    "#845EC2", "#9B7EDE", "#C56BC0",
    "#4FA8E8", "#6C8CFF", "#4BBFA8",
)

_FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyhbd.ttc",                          # 微软雅黑 粗
    "C:/Windows/Fonts/msyh.ttc",                            # 微软雅黑
    "C:/Windows/Fonts/simhei.ttf",                          # 黑体
    "C:/Windows/Fonts/simsun.ttc",                          # 宋体
    "C:/Windows/Fonts/simkai.ttf",                          # 楷体
    "/System/Library/Fonts/PingFang.ttc",                   # macOS
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",         # Linux
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
)

_FONT_CACHE: dict[tuple[bool, int], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}


def _detect_font_path() -> Optional[str]:
    """返回第一个可用的中文字体路径，一个都没有则返回 None。"""
    for candidate in _FONT_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return None


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """取字体对象；粗体找不到就退回普通字重。"""
    cache_key = (bold, size)
    if cache_key in _FONT_CACHE:
        return _FONT_CACHE[cache_key]

    candidates = (
        ("C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/simhei.ttf") if bold
        else ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf")
    )
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont = ImageFont.load_default()
    for path in candidates:
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, size)
                break
            except OSError:
                continue
    _FONT_CACHE[cache_key] = font
    return font


def _cut_words(texts: Sequence[str]) -> List[str]:
    """jieba 分词后丢掉单字 —— 语气词、标点在词云里没有信息量。"""
    words: List[str] = []
    for text in texts:
        cut = jieba.cut(text.strip(), cut_all=False)
        words.extend(word for word in cut if len(word.strip()) > 1)
    return words


def _pastel_color(word, font_size, position, orientation, random_state=None, **kwargs) -> str:
    """给每个词从马卡龙色板里挑一个颜色（小字只用浅一点的几支，免得糊成一片）。"""
    palette = _PASTEL[:]
    if font_size <= 28:
        palette = [color for color in palette if color not in ("#845EC2", "#9B7EDE")]
    return random.choice(palette or list(_PASTEL))


def _gradient(width: int, height: int) -> Image.Image:
    """浅粉 → 浅蓝紫的竖向渐变底。"""
    canvas = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(canvas)
    for y in range(height):
        ratio = y / max(height - 1, 1)
        color = tuple(
            int(_BG_START[i] + (_BG_END[i] - _BG_START[i]) * ratio) for i in range(3)
        )
        draw.line([(0, y), (width, y)], fill=color)
    return canvas


def _draw_heart(draw: ImageDraw.ImageDraw, cx: float, cy: float, size: float, color) -> None:
    """用两个圆 + 一个方块拼出手绘感爱心（字体里没有彩色 emoji，只能自己画）。"""
    radius = size / 2
    draw.ellipse([cx - radius * 1.6, cy - radius * 1.1, cx + radius * 0.4, cy + radius * 0.9], fill=color)
    draw.ellipse([cx - radius * 0.4, cy - radius * 1.1, cx + radius * 1.6, cy + radius * 0.9], fill=color)
    draw.polygon(
        [(cx - radius * 1.55, cy - radius * 0.1), (cx + radius * 1.55, cy - radius * 0.1), (cx, cy + radius * 1.9)],
        fill=color,
    )


def _compose_card(
    layer: Image.Image,
    title: str,
    subtitle: str,
    words_count: int,
) -> bytes:
    """把透明底词云贴到渐变卡片上，输出 JPEG。"""
    inner_w = _CARD_W - _MARGIN * 2 - 56
    # 先把词云缩到卡片内宽，再据此算画布高度（否则高度会算错）
    target_w = min(inner_w, layer.width)
    if target_w != layer.width:
        ratio = target_w / layer.width
        layer = layer.resize((target_w, max(int(layer.height * ratio), 1)), Image.Resampling.LANCZOS)

    header = 100
    panel_pad = 16
    canvas_h = _MARGIN * 2 + 28 + header + layer.height + panel_pad * 2 + 44

    canvas = _gradient(_CARD_W, canvas_h).convert("RGBA")
    draw = ImageDraw.Draw(canvas, "RGBA")
    for cx, cy, size in ((_CARD_W - 54, 66, 20), (54, 92, 15)):
        _draw_heart(draw, cx, cy, size, _HEART_BG)

    card_box = (_MARGIN, _MARGIN, _CARD_W - _MARGIN, canvas_h - _MARGIN)
    draw.rounded_rectangle(
        (card_box[0] + 3, card_box[1] + 5, card_box[2] + 3, card_box[3] + 5),
        radius=_CARD_RADIUS,
        fill=(214, 200, 224, 90),
    )
    draw.rounded_rectangle(card_box, radius=_CARD_RADIUS, fill=_CARD_FILL)
    draw.rounded_rectangle(card_box, radius=_CARD_RADIUS, outline=_CARD_BORDER, width=3)

    inner_left = card_box[0] + 28
    title_y = card_box[1] + 26
    draw.text((inner_left, title_y), title, font=_load_font(30, bold=True), fill=_INK_TITLE)
    count_font = _load_font(20)
    count_text = f"{subtitle} · 共 {words_count} 个词" if subtitle else f"共 {words_count} 个词"
    draw.text((inner_left, title_y + 42), count_text, font=count_font, fill=_INK_BODY)
    _draw_heart(draw, card_box[2] - 40, title_y + 22, 16, _HEART_CARD)

    # 词云本体：先铺一块淡粉白「纸」，再把透明底词云按 alpha 叠上去
    # （直接 paste 会把透明像素的 RGB 一起搬进来，转 RGB 时那里会变成黑块）
    paste_x = card_box[0] + (card_box[2] - card_box[0] - layer.width) // 2
    paste_y = title_y + header + panel_pad
    draw.rounded_rectangle(
        (card_box[0] + 24, paste_y - panel_pad, card_box[2] - 24, paste_y + layer.height + panel_pad),
        radius=24,
        fill=(255, 250, 253),
        outline=(255, 232, 243),
        width=2,
    )
    mask = Image.new("L", layer.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, layer.width - 1, layer.height - 1), radius=20, fill=255)
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    overlay.paste(layer, (paste_x, paste_y), mask)
    canvas = Image.alpha_composite(canvas, overlay)
    draw = ImageDraw.Draw(canvas, "RGBA")

    draw.line(
        [(inner_left, canvas_h - _MARGIN - 30), (card_box[2] - 28, canvas_h - _MARGIN - 30)],
        fill=(255, 224, 236),
        width=2,
    )
    _draw_heart(draw, card_box[2] - 46, canvas_h - _MARGIN - 14, 15, _HEART_CARD)

    buffer = io.BytesIO()
    canvas.convert("RGB").save(buffer, format="JPEG", quality=92, optimize=True)
    return buffer.getvalue()


def build_wordcloud_from_texts(
    texts: List[str],
    width: int = 820,
    height: int = 400,
    background_color: str = "white",
    max_words: int = 150,
    colormap: Optional[str] = None,
    font_path: Optional[str] = None,
    title: str = "本场弹幕词云",
    uname: str = "",
    duration_text: str = "",
) -> Optional[bytes]:
    """从弹幕文本列表生成词云卡片的 JPEG 字节。

    Args:
        texts: 弹幕文本列表
        width / height: 词云区域尺寸（贴到卡片里时会等比缩放）
        background_color: 兼容旧参数；词云本身画成透明底，这里不再使用
        max_words: 最大词数
        colormap: 传了就沿用 matplotlib 的配色，不传用内置马卡龙色板
        font_path: 字体路径，不传则自动检测
        title / uname / duration_text: 卡片标题与副标题信息

    Returns:
        JPEG 图片的 bytes，失败或无有效文本返回 None
    """
    if not texts:
        return None

    words = _cut_words(texts)
    if not words:
        return None

    try:
        options = dict(
            font_path=font_path or _detect_font_path(),
            width=width,
            height=height,
            max_words=max_words,
            collocations=False,   # 不统计双词搭配，弹幕里成对出现的词没什么意义
            prefer_horizontal=1.0,
            margin=8,
        )
        if colormap:
            options.update(background_color=background_color, colormap=colormap)
        else:
            options.update(background_color=None, mode="RGBA", color_func=_pastel_color)
        cloud = WordCloud(**options).generate(" ".join(words))
        layer = cloud.to_image().convert("RGBA")
        subtitle = " · ".join(part for part in (uname, duration_text) if part)
        return _compose_card(layer, title=title, subtitle=subtitle, words_count=len(words))
    except Exception as exc:  # noqa: BLE001 — 字体/文本什么都能抛，反正这次就是不出图
        logger.warning(f"[bilibili_live] 生成词云失败：{exc}")
        return None


def generate_wordcloud_to_file(
    texts: List[str],
    output_path: str = "wordcloud.jpg",
    width: int = 820,
    height: int = 400,
    background_color: str = "white",
    max_words: int = 200,
    font_path: Optional[str] = None,
    colormap: Optional[str] = None,
    show: bool = False,
) -> bool:
    """从文本列表生成词云并保存到文件。"""
    img_bytes = build_wordcloud_from_texts(
        texts=texts,
        width=width,
        height=height,
        background_color=background_color,
        max_words=max_words,
        colormap=colormap,
        font_path=font_path,
    )
    if img_bytes is None:
        print("词云生成失败：无有效文本或字体不可用")
        return False

    with open(output_path, "wb") as handle:
        handle.write(img_bytes)
    print(f"词云已保存至: {output_path}")

    if show:
        _preview(img_bytes)

    return True


def _preview(img_bytes: bytes) -> None:
    """弹窗预览生成的图片（只有 CLI 的 --show 用得到）。"""
    import matplotlib.pyplot as plt

    plt.figure(figsize=(10, 8))
    plt.imshow(Image.open(io.BytesIO(img_bytes)))
    plt.axis("off")
    plt.tight_layout(pad=0)
    plt.show()


def load_text_file(file_path: str) -> str:
    """读取 UTF-8 文本文件。"""
    with open(file_path, "r", encoding="utf-8") as handle:
        return handle.read()


def segment_chinese(text: str) -> str:
    """中文分词，词之间用空格隔开（CLI 统计词频用）。"""
    return " ".join(_cut_words([text]))


def main() -> None:
    """CLI 入口：没给文件就用内置示例文本。"""
    args = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    show = "--show" in sys.argv
    file_path = args[0] if args else None

    if file_path:
        print(f"读取文件: {file_path}")
        text = load_text_file(file_path)
    else:
        print("未指定文件，使用内置示例文本。")
        text = (
            "这首歌唱得也太好了吧 好听 好听 主播加油 主播加油 主播加油 再来一首 再来一首 "
            "哈哈哈哈哈 笑死我了 笑死我了 这是什么操作 这是什么操作 太强了 太强了 太强了 "
            "前面的别走 我也是 我也是 同问 同问 下单了 抽奖 抽奖 抽奖 弹幕护体 弹幕护体"
        )

    word_counts = Counter(segment_chinese(text).split())
    print(f"共提取 {len(word_counts)} 个词，前10: {word_counts.most_common(10)}")

    font_path = _detect_font_path()
    if font_path:
        print(f"使用字体: {font_path}")
    else:
        print("警告: 未找到中文字体，词云可能无法正常显示中文")

    generate_wordcloud_to_file(
        texts=[text],
        show=show,
        font_path=font_path,
    )


if __name__ == "__main__":
    main()
