"""ImageCache 单元测试。"""

from __future__ import annotations

import os
import time

import pytest

from nonebot_plugin_hermes.core.storage.image_cache import ImageCache


@pytest.fixture
def cache_dir(tmp_path):
    d = tmp_path / "images"
    d.mkdir()
    return d


def test_put_and_get_round_trip(cache_dir):
    cache = ImageCache(cache_dir=cache_dir, quota_bytes=1024 * 1024)
    sha = cache.put(b"hello world", "image/png")
    assert sha is not None
    payload = cache.get_bytes(sha)
    assert payload is not None
    bytes_, mime = payload
    assert bytes_ == b"hello world"
    assert mime == "image/png"


def test_put_idempotent_same_sha(cache_dir):
    cache = ImageCache(cache_dir=cache_dir, quota_bytes=1024 * 1024)
    sha1 = cache.put(b"hello", "image/jpeg")
    sha2 = cache.put(b"hello", "image/jpeg")
    assert sha1 == sha2
    files = list(cache_dir.glob("*.jpg"))
    assert len(files) == 1


def test_get_bytes_returns_none_for_unknown_sha(cache_dir):
    cache = ImageCache(cache_dir=cache_dir, quota_bytes=1024 * 1024)
    assert cache.get_bytes("0" * 64) is None


def test_get_bytes_returns_none_if_file_deleted_externally(cache_dir):
    cache = ImageCache(cache_dir=cache_dir, quota_bytes=1024 * 1024)
    sha = cache.put(b"hi", "image/png")
    # 外部手删
    next(cache_dir.iterdir()).unlink()
    assert cache.get_bytes(sha) is None


def test_evict_if_over_quota_removes_oldest_atime_first(cache_dir):
    cache = ImageCache(cache_dir=cache_dir, quota_bytes=300)  # 故意小
    # 三张图各 100 字节,共 300,刚好不超
    sha_a = cache.put(b"A" * 100, "image/png")
    time.sleep(0.02)
    sha_b = cache.put(b"B" * 100, "image/png")
    time.sleep(0.02)
    cache.put(b"C" * 100, "image/png")
    # 现在添加第四张 → 总 400 > 300,A 应被淘汰
    sha_d = cache.put(b"D" * 100, "image/png")
    removed = cache.evict_if_over_quota()
    assert removed >= 100
    assert cache.get_bytes(sha_a) is None
    assert cache.get_bytes(sha_b) is not None
    assert cache.get_bytes(sha_d) is not None


def test_get_bytes_updates_atime(cache_dir):
    cache = ImageCache(cache_dir=cache_dir, quota_bytes=1024 * 1024)
    sha = cache.put(b"hi", "image/png")
    path = cache_dir / f"{sha}.png"
    # 手动倒推 atime 到一段时间前
    fixed_mtime = path.stat().st_mtime
    old_atime = fixed_mtime - 10.0
    os.utime(path, (old_atime, fixed_mtime))
    time.sleep(0.05)
    cache.get_bytes(sha)
    fresh_atime = path.stat().st_atime
    assert fresh_atime > old_atime


def test_mime_extension_mapping(cache_dir):
    cache = ImageCache(cache_dir=cache_dir, quota_bytes=1024 * 1024)
    sha_jpg = cache.put(b"x", "image/jpeg")
    sha_png = cache.put(b"y", "image/png")
    sha_webp = cache.put(b"z", "image/webp")
    sha_gif = cache.put(b"w", "image/gif")
    assert (cache_dir / f"{sha_jpg}.jpg").exists()
    assert (cache_dir / f"{sha_png}.png").exists()
    assert (cache_dir / f"{sha_webp}.webp").exists()
    assert (cache_dir / f"{sha_gif}.gif").exists()


def test_unknown_mime_falls_back_to_bin_ext(cache_dir):
    cache = ImageCache(cache_dir=cache_dir, quota_bytes=1024 * 1024)
    sha = cache.put(b"data", "application/octet-stream")
    assert (cache_dir / f"{sha}.bin").exists()
    payload = cache.get_bytes(sha)
    assert payload is not None
    bytes_, mime = payload
    assert bytes_ == b"data"
    # bin 扩展映射回 octet-stream
    assert mime == "application/octet-stream"


def test_total_size_bytes_diagnostic(cache_dir):
    cache = ImageCache(cache_dir=cache_dir, quota_bytes=1024 * 1024)
    assert cache.total_size_bytes() == 0
    cache.put(b"x" * 100, "image/png")
    cache.put(b"y" * 200, "image/png")
    assert cache.total_size_bytes() == 300
