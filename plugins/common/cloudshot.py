"""云端网页截图：渲染全部交给第三方服务，本机不装、不跑浏览器。

调用方：`xr` 命令、大巴扎卡片页截图。

设计要点：
  * 服务商按顺序兜底 —— 前一个失败自动换下一个，全部失败才报错；
  * 每个服务商只负责「给我图片字节」，出错抛出带服务商名的异常，便于定位；
  * 出图后统一做发送前处理：限宽 → 超长切片 → 体积过大转 JPEG（QQ 对单图有限制）；
  * 同一网址短期内重复请求直接命中缓存，避免把免费额度烧在无意义的重复渲染上。
"""

from __future__ import annotations

import base64
import io
import logging
import os
import time
from collections import OrderedDict
from typing import Awaitable, Callable, List, Optional, Sequence, Tuple

import httpx
from PIL import Image

logger = logging.getLogger("cloudshot")

# ── 可配置项（.env 里用 CLOUDSHOT_* 覆盖）──────────────────────────────────────
DEFAULT_PROXY = "http://127.0.0.1:7892"
_PROXY_OFF = {"", "direct", "none", "off", "0", "false", "no"}

RENDER_WIDTH = 1440   # 送进渲染服务的逻辑视口宽度，拿到图后再缩到 MAX_WIDTH
RENDER_WAIT = 8       # 等待秒数：纯前端渲染的站点需要时间自己把内容画出来

# 出图参数（针对 QQ 的限制留足余量）
MAX_WIDTH = 1080           # 缩放后的图片宽度
CHUNK_HEIGHT = 3200        # 单张图最大高度，超出则切片
MAX_CHUNKS = 8             # 最多切几张
IMAGE_MAX_BYTES = 1_500_000  # 单张超过此大小则转 JPEG
TOTAL_MAX_BYTES = 8_000_000  # 整条消息的图片总量上限

CACHE_TTL = 1800   # 同一网址的截图缓存时长（秒）
CACHE_SIZE = 32    # 缓存条数上限

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

Provider = Callable[[httpx.AsyncClient, str], Awaitable[bytes]]


# ── 配置读取 ──────────────────────────────────────────────────────────────────
def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _proxy() -> Optional[str]:
    raw = _env("CLOUDSHOT_PROXY", DEFAULT_PROXY)
    return None if raw.lower() in _PROXY_OFF else raw


def _timeout() -> float:
    try:
        return float(_env("CLOUDSHOT_TIMEOUT", "60"))
    except ValueError:
        return 60.0


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        proxy=_proxy(),
        timeout=httpx.Timeout(connect=8.0, read=_timeout(), write=15.0, pool=8.0),
        follow_redirects=True,
        trust_env=False,
        headers={
            "User-Agent": UA,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )


# ── 渲染服务商 ────────────────────────────────────────────────────────────────
def _as_image(response: httpx.Response) -> bytes:
    """确认对方真的返回了一张像样的图，否则抛出可读的异常。"""
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}")
    content_type = response.headers.get("content-type", "")
    if not content_type.startswith("image/"):
        raise RuntimeError(f"返回的不是图片（{content_type or '无 content-type'}）")
    if len(response.content) < 3000:
        raise RuntimeError(f"图片仅 {len(response.content)} 字节，疑似占位图")
    return response.content


async def _thumio(client: httpx.AsyncClient, url: str) -> bytes:
    """thum.io：免费、无需密钥，支持整页截图与自定义等待时间。"""
    api = (
        f"https://image.thum.io/get/width/{RENDER_WIDTH}"
        f"/wait/{RENDER_WAIT}/noanimate/fullpage/{url}"
    )
    return _as_image(await client.get(api))


async def _microlink(client: httpx.AsyncClient, url: str) -> bytes:
    """microlink：先拿 JSON，再从其中的地址取图。"""
    response = await client.get(
        "https://api.microlink.io/",
        params={
            "url": url,
            "screenshot": "true",
            "meta": "false",
            "screenshot.fullPage": "true",
            "waitUntil": "networkidle0",
        },
    )
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}")
    payload = response.json()
    shot = ((payload or {}).get("data") or {}).get("screenshot") or {}
    if not shot.get("url"):
        raise RuntimeError(f"未返回截图地址：{str(payload)[:120]}")
    return _as_image(await client.get(shot["url"]))


PROVIDERS: Sequence[Tuple[str, Provider]] = (
    ("thum.io", _thumio),
    ("microlink", _microlink),
)


# ── 出图处理：限宽 / 切片 / 压缩 ───────────────────────────────────────────────
def _encode(piece: Image.Image, *, jpeg: bool = False, quality: int = 86) -> bytes:
    buffer = io.BytesIO()
    if jpeg:
        piece.save(buffer, format="JPEG", quality=quality, optimize=True, progressive=True)
    else:
        piece.save(buffer, format="PNG", optimize=True)
    data = buffer.getvalue()
    if not jpeg and len(data) > IMAGE_MAX_BYTES:  # PNG 太大就压成 JPEG
        return _encode(piece, jpeg=True, quality=quality)
    return data


def _fit_for_qq(raw: bytes) -> List[bytes]:
    """把截图整成 QQ 收得下的若干张图。"""
    image = Image.open(io.BytesIO(raw)).convert("RGB")

    if image.width > MAX_WIDTH:
        ratio = MAX_WIDTH / image.width
        image = image.resize(
            (MAX_WIDTH, max(1, round(image.height * ratio))), Image.Resampling.LANCZOS
        )

    if image.height <= CHUNK_HEIGHT:
        pieces = [image]
    else:
        count = -(-image.height // CHUNK_HEIGHT)
        if count > MAX_CHUNKS:  # 太高：整体缩小，硬塞进 MAX_CHUNKS 张
            scale = CHUNK_HEIGHT * MAX_CHUNKS / image.height
            image = image.resize(
                (max(400, round(image.width * scale)), max(1, round(image.height * scale))),
                Image.Resampling.LANCZOS,
            )
            count = -(-image.height // CHUNK_HEIGHT)
        pieces = [
            image.crop(
                (0, i * CHUNK_HEIGHT, image.width, min(image.height, (i + 1) * CHUNK_HEIGHT))
            )
            for i in range(count)
        ]

    images = [_encode(piece) for piece in pieces]
    for quality in (82, 72):  # 总量仍超标就整体转 JPEG 再压一档
        if sum(map(len, images)) <= TOTAL_MAX_BYTES:
            break
        images = [_encode(piece, jpeg=True, quality=quality) for piece in pieces]
    return images


# ── 截图缓存 ──────────────────────────────────────────────────────────────────
_cache: "OrderedDict[str, Tuple[float, bytes, str]]" = OrderedDict()


def _cache_get(url: str) -> Optional[Tuple[bytes, str]]:
    hit = _cache.get(url)
    if hit is None:
        return None
    saved_at, raw, service = hit
    if time.monotonic() - saved_at > CACHE_TTL:
        _cache.pop(url, None)
        return None
    _cache.move_to_end(url)
    return raw, service


def _cache_put(url: str, raw: bytes, service: str) -> None:
    _cache[url] = (time.monotonic(), raw, service)
    _cache.move_to_end(url)
    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)


async def _render(url: str) -> Tuple[bytes, str]:
    """依次尝试各渲染服务，返回 (原始图片字节, 服务名)。"""
    errors: List[str] = []
    async with _client() as client:
        for name, provider in PROVIDERS:
            try:
                raw = await provider(client, url)
            except Exception as exc:  # noqa: BLE001 — 换下一个服务商继续，不中断
                errors.append(f"{name}：{exc}")
                logger.warning(f"[cloudshot] {name} 渲染失败 {url} —— {exc}")
                continue
            logger.info(f"[cloudshot] {name} 出图成功（{len(raw)} 字节）← {url}")
            return raw, name
    raise RuntimeError("所有云端渲染服务都失败了（" + "；".join(errors) + "）")


# ── 对外接口 ──────────────────────────────────────────────────────────────────
async def capture(url: str) -> Tuple[List[bytes], str]:
    """把网页拍成可直接发给 QQ 的图片。

    返回 (图片字节列表, 实际使用的渲染服务名)；所有服务都失败时抛出 RuntimeError。
    """
    cached = _cache_get(url)
    if cached is not None:
        raw, service = cached
        logger.info(f"[cloudshot] 命中截图缓存（{service}）← {url}")
    else:
        raw, service = await _render(url)
        _cache_put(url, raw, service)
    return _fit_for_qq(raw), service


def to_base64(images: Sequence[bytes]) -> List[str]:
    """转成 OneBot 的 base64:// 图片段，不依赖本机文件路径。"""
    return [f"base64://{base64.b64encode(image).decode()}" for image in images]
