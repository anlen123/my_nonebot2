"""把当前轮的图片转成自包含的 `data:` URL,供多模态 parts 直接携带。

为什么不能直接把平台图片 URL 交出去:Hermes 对 http(s) 图片 URL 是**纯透传**,
自己不下载,原样转给 provider。于是链路上每一环都可能失败,且失败形态不统一 ——
provider 拉不到会把整轮打成 400;Anthropic 形态转成 `{"type":"url"}` 仍由对端拉取;
而 Gemini native 会**静默丢弃**非 `data:` 的 part,Bedrock 把它降级成一行文本。
平台侧的 URL 还普遍带时效凭据或鉴权 token,交出去既会过期也会外泄。

内联不会增加上游持久化体积:Hermes 落库前把 image part 投影成文本占位,
图片字节不进它的会话库。代价只在单次请求体上,而请求体有硬上限,所以
归一化是必需项而非优化项 —— 一张手机原图 base64 之后就能吃掉大半配额。

下采样到最长边 `max_edge` 后重编码,同时也丢掉 EXIF(含地理位置)。视觉模型
内部本就会降采样,原图分辨率换不来识别质量,只换更高的 token 成本。
"""

from __future__ import annotations

import asyncio
import base64
import io
from collections.abc import Sequence
from typing import TYPE_CHECKING

import httpx
from nonebot import logger

from ..config import plugin_config

if TYPE_CHECKING:
    from PIL import Image

# 逐级降质阶梯的收紧目标。实际阶梯由 _build_ladder 对用户配置取下界生成,
# 保证每一级都不高于上一级 —— 直接用固定值会在 max_edge 被配得更小时反向放大。
# 走完仍超限就放弃这张图:继续压下去得到的已经不是「这张图」了,不如明确缺席。
_TIGHTEN_STEPS: tuple[tuple[int, int], ...] = ((1024, 75), (768, 68))


def _build_ladder(max_edge: int, quality: int) -> tuple[tuple[int, int], ...]:
    """(edge, quality) 单调不增的尝试序列,去掉重复级。"""
    ladder: list[tuple[int, int]] = [(max_edge, quality)]
    for edge, q in _TIGHTEN_STEPS:
        step = (min(edge, max_edge), min(q, quality))
        if step != ladder[-1]:
            ladder.append(step)
    return tuple(ladder)


def _normalize_image(raw: bytes) -> Image.Image | None:
    """解码原始字节为摆正方向、消除透明通道的 RGB 图像对象。

    同步 CPU 密集操作,必须在工作线程中调用。
    """
    from PIL import Image, ImageOps

    try:
        with Image.open(io.BytesIO(raw)) as im:
            # EXIF 方向必须在缩放前应用,否则竖拍照片会以横向送进模型。
            im = ImageOps.exif_transpose(im)

            # 动图只取首帧:后续帧对静态视觉理解无信息增量。
            if getattr(im, "is_animated", False):
                im.seek(0)

            # JPEG 无 alpha 通道;带透明度的图像(贴纸/截图)合成到纯白底色,
            # 避免直接 convert("RGB") 将透明区域压黑导致视觉异常。
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1])
                return bg
            elif im.mode != "RGB":
                return im.convert("RGB")
            else:
                return im.copy()
    except Exception as exc:
        logger.warning(f"[image_inline] decode failed ({type(exc).__name__}: {exc})")
        return None


def _encode_one(raw: bytes, max_edge: int, quality: int, per_image_cap: int) -> tuple[bytes, str] | None:
    """解码 → 摆正 → 下采样 → JPEG 重编码。返回 (bytes, mime);无法处理或超限返回 None。

    同步 CPU 密集,调用方须放进线程。
    """
    from PIL import Image

    base_im = _normalize_image(raw)
    if base_im is None:
        return None

    ladder = _build_ladder(max_edge, quality)
    for edge, q in ladder:
        try:
            target_im = base_im
            if max(base_im.size) > edge:
                target_im = base_im.copy()
                target_im.thumbnail((edge, edge), Image.LANCZOS)

            buf = io.BytesIO()
            # optimize=True 生成最优霍夫曼编码表;不写入 exif 参数即可自动剔除所有地理位置等隐私元数据。
            target_im.save(buf, format="JPEG", quality=q, optimize=True)
            out = buf.getvalue()
            if len(out) <= per_image_cap:
                return out, "image/jpeg"
        except Exception as exc:
            logger.warning(f"[image_inline] encode failed ({type(exc).__name__}: {exc})")
            return None

    return None


async def _fetch(client: httpx.AsyncClient, url: str) -> bytes | None:
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content
    except Exception as exc:
        # 截断 URL:平台图片地址常把鉴权 token 带在 query 里,整条进日志会导致凭据落盘泄露。
        logger.warning(f"[image_inline] fetch failed url={url[:80]} ({type(exc).__name__}: {exc})")
        return None


async def inline_image_urls(urls: Sequence[str]) -> list[str]:
    """把 http(s) 图片 URL 抓取并转成 `data:image/jpeg;base64,...`。

    - 已经是 `data:` 的原样保留(自包含,无需重复抓取与编码)
    - 抓取失败 / 解码失败 / 超出单图上限的那张被跳过,其余照常返回
    - 累计超出本轮总预算后不再处理剩余图片

    跳过是刻意的降级:少一张图仍能答话,整轮 400 则什么都没有。
    """
    if not urls or not plugin_config.hermes_image_inline_enabled:
        return list(urls)

    # 保留顺序且高效去重(转发/引用叠加消息中可能出现重复图片)。
    ordered = list(dict.fromkeys(urls))

    max_edge = plugin_config.hermes_image_inline_max_edge
    quality = plugin_config.hermes_image_inline_quality
    per_image_cap = plugin_config.hermes_image_inline_max_bytes
    total_cap = plugin_config.hermes_image_inline_total_max_bytes

    passthrough: dict[str, str] = {}
    to_fetch: list[str] = []
    for u in ordered:
        if u.startswith("data:"):
            passthrough[u] = u
        elif u.startswith(("http://", "https://")):
            to_fetch.append(u)
        else:
            logger.warning(f"[image_inline] unsupported scheme, skipped: {u[:40]}")

    # 并发拉取所有网络图片。
    fetched: dict[str, bytes] = {}
    if to_fetch:
        timeout = plugin_config.hermes_image_fetch_timeout_s
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            raws = await asyncio.gather(*(_fetch(client, u) for u in to_fetch))
        fetched = {u: raw for u, raw in zip(to_fetch, raws) if raw is not None}

    # 在工作线程池中并发执行 CPU 密集的图片归一化与阶梯重编码。
    encoded_map: dict[str, str] = {}
    if fetched:
        encoded_results = await asyncio.gather(
            *(asyncio.to_thread(_encode_one, raw, max_edge, quality, per_image_cap) for raw in fetched.values())
        )
        for u, raw, res in zip(fetched.keys(), fetched.values(), encoded_results):
            if res is None:
                logger.warning(f"[image_inline] dropped oversized/undecodable image url={u[:80]} raw={len(raw)}B")
                continue
            data, mime = res
            encoded = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
            logger.debug(f"[image_inline] inlined url={u[:80]} raw={len(raw)}B -> {len(data)}B")
            encoded_map[u] = encoded

    # 按原始顺序组装输出,并进行本轮总字节预算检查。
    out: list[str] = []
    total_bytes = 0
    for pos, u in enumerate(ordered):
        encoded = passthrough.get(u) or encoded_map.get(u)
        if encoded is None:
            continue
        if total_bytes + len(encoded) > total_cap:
            logger.warning(
                f"[image_inline] total budget {total_cap}B exhausted; "
                f"{len(ordered) - pos} image(s) dropped from this turn"
            )
            break
        out.append(encoded)
        total_bytes += len(encoded)

    return out
