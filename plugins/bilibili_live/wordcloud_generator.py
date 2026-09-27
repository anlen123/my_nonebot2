"""词云图片生成器 —— bilibili_live 的弹幕词云，也可以单独当 CLI 用。

模块用法：
    from .wordcloud_generator import build_wordcloud_from_texts
    png = build_wordcloud_from_texts(["弹幕1", "弹幕2"])   # PNG 字节，失败返回 None

CLI 用法：
    python wordcloud_generator.py [文本文件路径]          # 生成图片到 wordcloud.png
    python wordcloud_generator.py --show [文本文件路径]   # 生成后再弹窗预览
"""

from __future__ import annotations

import io
import logging
import os
import sys
from collections import Counter
from typing import List, Optional, Sequence

import jieba

# 词云只需要出图，先切到非交互后端，免得 CLI 加了 --show 以后卡在窗口上
import matplotlib

matplotlib.use("Agg")
from wordcloud import WordCloud

logger = logging.getLogger("bilibili_live")

# 按平台顺序依次尝试的中文字体，取第一个存在的
_FONT_CANDIDATES = (
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


def _detect_font_path() -> Optional[str]:
    """返回第一个可用的中文字体路径，一个都没有则返回 None。"""
    for candidate in _FONT_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return None


def _cut_words(texts: Sequence[str]) -> List[str]:
    """jieba 分词后丢掉单字 —— 语气词、标点在词云里没有信息量。"""
    words: List[str] = []
    for text in texts:
        cut = jieba.cut(text.strip(), cut_all=False)
        words.extend(word for word in cut if len(word.strip()) > 1)
    return words


def build_wordcloud_from_texts(
    texts: List[str],
    width: int = 900,
    height: int = 450,
    background_color: str = "white",
    max_words: int = 150,
    colormap: str = "viridis",
    font_path: Optional[str] = None,
) -> Optional[bytes]:
    """从弹幕文本列表生成词云图片。

    Args:
        texts: 弹幕文本列表
        width: 图片宽度
        height: 图片高度
        background_color: 背景色
        max_words: 最大词数
        colormap: 颜色映射方案
        font_path: 字体路径，不传则自动检测

    Returns:
        PNG 图片的 bytes，失败或无有效文本返回 None
    """
    if not texts:
        return None

    words = _cut_words(texts)
    if not words:
        return None

    try:
        cloud = WordCloud(
            font_path=font_path or _detect_font_path(),
            width=width,
            height=height,
            background_color=background_color,
            max_words=max_words,
            colormap=colormap,
            collocations=False,   # 不统计双词搭配，弹幕里成对出现的词没什么意义
        ).generate(" ".join(words))

        buffer = io.BytesIO()
        cloud.to_image().save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception as exc:  # noqa: BLE001 — 字体/文本什么都能抛，反正这次就是不出图
        logger.warning(f"[bilibili_live] 生成词云失败：{exc}")
        return None


def generate_wordcloud_to_file(
    texts: List[str],
    output_path: str = "wordcloud.png",
    width: int = 800,
    height: int = 600,
    background_color: str = "white",
    max_words: int = 200,
    font_path: Optional[str] = None,
    colormap: str = "viridis",
    show: bool = False,
) -> bool:
    """从文本列表生成词云并保存到文件。

    Returns:
        True 表示生成成功
    """
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
    from PIL import Image as PILImage

    plt.figure(figsize=(10, 8))
    plt.imshow(PILImage.open(io.BytesIO(img_bytes)))
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
            "Python 是一门优雅高效的编程语言，广泛应用于数据科学、人工智能、"
            "Web 开发、自动化运维等领域。Python 拥有丰富的第三方库生态，"
            "如 NumPy、Pandas、Matplotlib 用于数据分析与可视化，"
            "Django、Flask 用于 Web 开发，PyTorch、TensorFlow 用于深度学习。"
            "学习 Python 可以帮助你快速实现想法，提升开发效率。"
            "Python 的语法简洁清晰，非常适合初学者入门学习。"
            "编程 编程 编程 数据 数据 数据 开发 开发 开发 学习 学习 学习"
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
