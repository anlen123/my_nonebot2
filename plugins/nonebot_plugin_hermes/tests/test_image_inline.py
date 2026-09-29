"""当前轮图片内联(data: URL)单元测试。

覆盖:正常内联 + 下采样、data: 原样放行、抓取失败/解码失败逐张跳过、
单图与单轮字节预算、重复 URL 去重、开关关闭时的回退。
"""

from __future__ import annotations

import base64
import io
from unittest.mock import MagicMock

import pytest
from PIL import Image

from nonebot_plugin_hermes.core import image_inline
from nonebot_plugin_hermes.core.image_inline import inline_image_urls


def _img_bytes(w: int, h: int, fmt: str = "JPEG", mode: str = "RGB") -> bytes:
    im = Image.new(mode, (w, h), (200, 120, 60) if mode == "RGB" else (200, 120, 60, 128))
    buf = io.BytesIO()
    im.save(buf, format=fmt)
    return buf.getvalue()


def _decode(data_url: str) -> Image.Image:
    assert data_url.startswith("data:image/jpeg;base64,")
    raw = base64.b64decode(data_url.split(",", 1)[1])
    return Image.open(io.BytesIO(raw))


class _FakeClient:
    """按 url→bytes 映射作答;映射里没有的 URL 抛错,模拟抓取失败。"""

    def __init__(self, mapping: dict[str, bytes]) -> None:
        self._m = mapping

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def get(self, url: str):
        raw = self._m.get(url)
        if raw is None:
            raise RuntimeError("simulated fetch failure")
        resp = MagicMock()
        resp.content = raw
        resp.raise_for_status = MagicMock(return_value=None)
        return resp


@pytest.fixture
def cfg(monkeypatch):
    """默认配置,单测按需覆写字段。"""
    pc = image_inline.plugin_config
    monkeypatch.setattr(pc, "hermes_image_inline_enabled", True, raising=False)
    monkeypatch.setattr(pc, "hermes_image_inline_max_edge", 1280, raising=False)
    monkeypatch.setattr(pc, "hermes_image_inline_quality", 82, raising=False)
    monkeypatch.setattr(pc, "hermes_image_inline_max_bytes", 2_000_000, raising=False)
    monkeypatch.setattr(pc, "hermes_image_inline_total_max_bytes", 4_000_000, raising=False)
    monkeypatch.setattr(pc, "hermes_image_fetch_timeout_s", 5, raising=False)
    return pc


def _serve(monkeypatch, mapping: dict[str, bytes]) -> None:
    monkeypatch.setattr(image_inline.httpx, "AsyncClient", lambda **kw: _FakeClient(mapping))


@pytest.mark.asyncio
async def test_http_url_becomes_data_url_and_is_downscaled(cfg, monkeypatch):
    _serve(monkeypatch, {"https://cdn/x.jpg": _img_bytes(3000, 2000)})
    out = await inline_image_urls(["https://cdn/x.jpg"])
    assert len(out) == 1
    im = _decode(out[0])
    assert max(im.size) == 1280
    assert im.size == (1280, 853)


@pytest.mark.asyncio
async def test_small_image_is_not_upscaled(cfg, monkeypatch):
    _serve(monkeypatch, {"https://cdn/s.png": _img_bytes(120, 90, fmt="PNG")})
    out = await inline_image_urls(["https://cdn/s.png"])
    assert _decode(out[0]).size == (120, 90)


@pytest.mark.asyncio
async def test_rgba_is_flattened_not_blackened(cfg, monkeypatch):
    """带 alpha 的图合成到白底;直接 convert('RGB') 会把透明区压黑。"""
    im = Image.new("RGBA", (50, 50), (0, 0, 0, 0))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    _serve(monkeypatch, {"https://cdn/a.png": buf.getvalue()})
    out = await inline_image_urls(["https://cdn/a.png"])
    px = _decode(out[0]).convert("RGB").getpixel((25, 25))
    assert all(c > 200 for c in px), f"transparent area should flatten to white, got {px}"


@pytest.mark.asyncio
async def test_data_url_passes_through_untouched(cfg, monkeypatch):
    _serve(monkeypatch, {})
    data_url = "data:image/png;base64,iVBORw0KGgo="
    assert await inline_image_urls([data_url]) == [data_url]


@pytest.mark.asyncio
async def test_fetch_failure_skips_only_that_image(cfg, monkeypatch):
    _serve(monkeypatch, {"https://cdn/ok.jpg": _img_bytes(200, 200)})
    out = await inline_image_urls(["https://cdn/dead.jpg", "https://cdn/ok.jpg"])
    assert len(out) == 1
    assert _decode(out[0]).size == (200, 200)


@pytest.mark.asyncio
async def test_undecodable_bytes_are_skipped(cfg, monkeypatch):
    _serve(monkeypatch, {"https://cdn/junk.jpg": b"not an image at all"})
    assert await inline_image_urls(["https://cdn/junk.jpg"]) == []


@pytest.mark.asyncio
async def test_unsupported_scheme_skipped(cfg, monkeypatch):
    _serve(monkeypatch, {})
    assert await inline_image_urls(["file:///etc/passwd"]) == []


@pytest.mark.asyncio
async def test_duplicate_urls_fetched_once_and_emitted_once(cfg, monkeypatch):
    calls: list[str] = []

    class _Counting(_FakeClient):
        async def get(self, url: str):
            calls.append(url)
            return await super().get(url)

    monkeypatch.setattr(
        image_inline.httpx, "AsyncClient", lambda **kw: _Counting({"https://cdn/d.jpg": _img_bytes(100, 100)})
    )
    out = await inline_image_urls(["https://cdn/d.jpg", "https://cdn/d.jpg"])
    assert len(out) == 1
    assert calls == ["https://cdn/d.jpg"]


@pytest.mark.asyncio
async def test_per_image_cap_drops_image_after_ladder(cfg, monkeypatch):
    monkeypatch.setattr(cfg, "hermes_image_inline_max_bytes", 200, raising=False)
    _serve(monkeypatch, {"https://cdn/big.jpg": _img_bytes(2000, 2000)})
    assert await inline_image_urls(["https://cdn/big.jpg"]) == []


@pytest.mark.asyncio
async def test_total_budget_truncates_remaining_images(cfg, monkeypatch):
    monkeypatch.setattr(cfg, "hermes_image_inline_total_max_bytes", 3000, raising=False)
    _serve(
        monkeypatch,
        {
            "https://cdn/1.jpg": _img_bytes(400, 400),
            "https://cdn/2.jpg": _img_bytes(400, 400, mode="RGB"),
        },
    )
    out = await inline_image_urls(["https://cdn/1.jpg", "https://cdn/2.jpg"])
    assert len(out) < 2
    assert sum(len(u) for u in out) <= 3000


@pytest.mark.asyncio
async def test_disabled_returns_urls_unchanged(cfg, monkeypatch):
    monkeypatch.setattr(cfg, "hermes_image_inline_enabled", False, raising=False)
    urls = ["https://cdn/x.jpg", "https://cdn/y.jpg"]
    assert await inline_image_urls(urls) == urls


@pytest.mark.asyncio
async def test_empty_input_short_circuits(cfg, monkeypatch):
    assert await inline_image_urls([]) == []


def test_ladder_never_upscales_when_max_edge_configured_small():
    """阶梯必须单调不增:max_edge 配小于收紧档时,后续级不得反向放大。"""
    ladder = image_inline._build_ladder(512, 60)
    assert ladder == ((512, 60),)

    ladder = image_inline._build_ladder(1280, 82)
    assert ladder == ((1280, 82), (1024, 75), (768, 68))
    edges = [e for e, _ in ladder]
    qualities = [q for _, q in ladder]
    assert edges == sorted(edges, reverse=True)
    assert qualities == sorted(qualities, reverse=True)


def test_ladder_dedupes_identical_steps():
    assert image_inline._build_ladder(1024, 75) == ((1024, 75), (768, 68))


@pytest.mark.asyncio
async def test_small_max_edge_is_respected(cfg, monkeypatch):
    monkeypatch.setattr(cfg, "hermes_image_inline_max_edge", 256, raising=False)
    _serve(monkeypatch, {"https://cdn/x.jpg": _img_bytes(2000, 1000)})
    out = await inline_image_urls(["https://cdn/x.jpg"])
    assert max(_decode(out[0]).size) == 256


@pytest.mark.asyncio
async def test_animated_gif_uses_first_frame(cfg, monkeypatch):
    """动图只取首帧。Image.open 本就停在第 0 帧,这里钉住结果而非实现路径。"""
    frames = [Image.new("RGB", (60, 60), c) for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    _serve(monkeypatch, {"https://cdn/anim.gif": buf.getvalue()})

    out = await inline_image_urls(["https://cdn/anim.gif"])
    im = _decode(out[0]).convert("RGB")
    r, g, b = im.getpixel((30, 30))
    assert r > 200 and g < 50 and b < 50, f"expected frame 0 (red), got {(r, g, b)}"


@pytest.mark.asyncio
async def test_grayscale_is_converted_to_rgb(cfg, monkeypatch):
    """非 RGB 且无 alpha 的模式走 convert('RGB'),不应抛错。"""
    buf = io.BytesIO()
    Image.new("L", (80, 80), 128).save(buf, format="PNG")
    _serve(monkeypatch, {"https://cdn/gray.png": buf.getvalue()})

    out = await inline_image_urls(["https://cdn/gray.png"])
    assert _decode(out[0]).mode == "RGB"
