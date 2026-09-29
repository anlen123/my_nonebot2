"""ImageFetcher 单元测试。"""

from __future__ import annotations

import asyncio
import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from nonebot_plugin_hermes.core.message_buffer import BufferedMessage
from nonebot_plugin_hermes.core.storage.image_cache import ImageCache
from nonebot_plugin_hermes.core.storage.image_fetcher import ImageFetcher
from nonebot_plugin_hermes.core.storage.message_store import MessageStore


@pytest.fixture
def store_and_cache(tmp_path):
    store = MessageStore(db_path=tmp_path / "m.db")
    cache = ImageCache(cache_dir=tmp_path / "imgs", quota_bytes=10 * 1024 * 1024)
    yield store, cache
    store.close()


def _mock_resp(bytes_: bytes, content_type: str = "image/jpeg", status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.content = bytes_
    resp.headers = {"content-type": content_type}
    resp.raise_for_status = MagicMock(return_value=None)
    return resp


async def _drain(fetcher: ImageFetcher, timeout_s: float = 2.0) -> None:
    """等 fetcher worker 把 queue + pending 都消费完。"""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        if fetcher.queue_size() == 0 and fetcher.inflight_urls() == 0:
            return
        await asyncio.sleep(0.02)


async def _patch_client_get(fetcher: ImageFetcher, get_side_effect) -> MagicMock:
    """把 fetcher._client.get 换成 mock。fetcher 必须 start() 完才能调本函数。"""
    assert fetcher._client is not None
    fetcher._client.get = AsyncMock(side_effect=get_side_effect)
    return fetcher._client.get


@pytest.mark.asyncio
async def test_fetcher_success_updates_store_and_cache(store_and_cache):
    store, cache = store_and_cache
    msg = BufferedMessage(
        ts=100,
        adapter="ob11",
        group_id="g1",
        user_id="u1",
        nickname="u1",
        content="hi",
        image_urls=["http://x.test/a.jpg"],
    )
    store.append(msg)

    fake_bytes = b"\xff\xd8\xff\xe0" + b"FAKE_JPG" * 16

    fetcher = ImageFetcher(store=store, cache=cache, timeout_s=5, max_attempts=2)
    await fetcher.start()
    await _patch_client_get(fetcher, lambda url: _mock_resp(fake_bytes, "image/jpeg"))
    fetcher.submit(msg.id, ["http://x.test/a.jpg"])
    await _drain(fetcher)
    await fetcher.stop()

    expected_sha = hashlib.sha256(fake_bytes).hexdigest()
    metas = store.get_message_images_meta([msg.id])
    assert metas[msg.id][0]["sha256"] == expected_sha
    assert metas[msg.id][0]["mime_type"] == "image/jpeg"
    payload = cache.get_bytes(expected_sha)
    assert payload is not None
    bytes_, mime = payload
    assert bytes_ == fake_bytes
    assert mime == "image/jpeg"


@pytest.mark.asyncio
async def test_fetcher_failure_after_max_attempts_leaves_sha_null(store_and_cache):
    store, cache = store_and_cache
    msg = BufferedMessage(
        ts=100,
        adapter="ob11",
        group_id="g1",
        user_id="u1",
        nickname="u1",
        content="hi",
        image_urls=["http://x.test/a.jpg"],
    )
    store.append(msg)

    fetcher = ImageFetcher(store=store, cache=cache, timeout_s=1, max_attempts=2)
    await fetcher.start()
    await _patch_client_get(fetcher, lambda url: (_ for _ in ()).throw(Exception("simulated network error")))
    fetcher.submit(msg.id, ["http://x.test/a.jpg"])
    await _drain(fetcher)
    await fetcher.stop()

    metas = store.get_message_images_meta([msg.id])
    assert metas[msg.id][0]["sha256"] is None
    assert metas[msg.id][0]["mime_type"] is None


@pytest.mark.asyncio
async def test_fetcher_queue_overflow_drops_oldest(store_and_cache):
    """队列上限达到时,新入队会挤掉最老的 URL。"""
    store, cache = store_and_cache
    fetcher = ImageFetcher(store=store, cache=cache, timeout_s=1, max_attempts=1, queue_max=3)
    # 不 start worker,只看 submit 入队行为
    for i in range(10):
        fetcher.submit(i, [f"http://x.test/{i}.jpg"])
    assert fetcher.queue_size() == 3
    # pending 表也应该清掉被丢的 URL
    assert fetcher.inflight_urls() == 3


@pytest.mark.asyncio
async def test_fetcher_non_image_content_type_skipped(store_and_cache):
    """content-type 不是 image/* 且字节头也不像图(html)时,不写 cache。"""
    store, cache = store_and_cache
    msg = BufferedMessage(
        ts=100,
        adapter="ob11",
        group_id="g1",
        user_id="u1",
        nickname="u1",
        content="hi",
        image_urls=["http://x.test/a.jpg"],
    )
    store.append(msg)

    fetcher = ImageFetcher(store=store, cache=cache, timeout_s=1, max_attempts=2)
    await fetcher.start()
    await _patch_client_get(fetcher, lambda url: _mock_resp(b"<html></html>", "text/html"))
    fetcher.submit(msg.id, ["http://x.test/a.jpg"])
    await _drain(fetcher)
    await fetcher.stop()

    metas = store.get_message_images_meta([msg.id])
    assert metas[msg.id][0]["sha256"] is None


@pytest.mark.asyncio
async def test_fetcher_accepts_octet_stream_when_bytes_sniff_image(store_and_cache):
    """Telegram CDN 常返回 application/octet-stream;只要字节头匹配 JPEG/PNG/WebP/GIF
    魔数,应该接受并按嗅探到的真 MIME 入 cache。这是 Telegram 路径能跑通的关键。"""
    store, cache = store_and_cache
    msg = BufferedMessage(
        ts=100,
        adapter="telegram",
        group_id="g1",
        user_id="u1",
        nickname="u1",
        content="hi",
        image_urls=["https://api.telegram.org/file/bot.../photo.jpg"],
    )
    store.append(msg)

    jpeg_bytes = b"\xff\xd8\xff\xe0" + b"FAKE_JPEG_PAYLOAD" * 8

    fetcher = ImageFetcher(store=store, cache=cache, timeout_s=1, max_attempts=2)
    await fetcher.start()
    await _patch_client_get(fetcher, lambda url: _mock_resp(jpeg_bytes, "application/octet-stream"))
    fetcher.submit(msg.id, msg.image_urls)
    await _drain(fetcher)
    await fetcher.stop()

    metas = store.get_message_images_meta([msg.id])
    assert metas[msg.id][0]["sha256"] is not None
    assert metas[msg.id][0]["mime_type"] == "image/jpeg"
    payload = cache.get_bytes(metas[msg.id][0]["sha256"])
    assert payload is not None
    bytes_back, mime_back = payload
    assert bytes_back == jpeg_bytes
    assert mime_back == "image/jpeg"


def test_sniff_image_mime_recognizes_known_formats():
    """字节嗅探:JPEG/PNG/GIF/WebP 各家魔数都识别。"""
    from nonebot_plugin_hermes.core.storage.image_fetcher import _sniff_image_mime

    assert _sniff_image_mime(b"\xff\xd8\xff\xe0xxx") == "image/jpeg"
    assert _sniff_image_mime(b"\x89PNG\r\n\x1a\n more") == "image/png"
    assert _sniff_image_mime(b"GIF87a more bytes") == "image/gif"
    assert _sniff_image_mime(b"GIF89a") == "image/gif"
    assert _sniff_image_mime(b"RIFF\x00\x00\x00\x00WEBP_____") == "image/webp"
    assert _sniff_image_mime(b"<html><body>") is None
    assert _sniff_image_mime(b"") is None


# --- URL dedupe(优化 3) ---


@pytest.mark.asyncio
async def test_fetcher_dedupes_same_url_into_one_http_fetch(store_and_cache):
    """同一 URL 被两条不同消息引用时,只 HTTP 抓一次;两条消息的 sha 都更新。

    覆盖典型场景:用户连发两张完全一样的图(同 file_id),fetcher 应该 dedupe。
    """
    store, cache = store_and_cache
    msg1 = BufferedMessage(
        ts=100,
        adapter="ob11",
        group_id="g1",
        user_id="u1",
        nickname="u1",
        content="hi",
        image_urls=["http://x.test/dup.jpg"],
    )
    msg2 = BufferedMessage(
        ts=101,
        adapter="ob11",
        group_id="g1",
        user_id="u1",
        nickname="u1",
        content="again",
        image_urls=["http://x.test/dup.jpg"],
    )
    store.append(msg1)
    store.append(msg2)

    fake_bytes = b"\xff\xd8\xff\xe0" + b"DUP" * 32
    call_count = 0

    def counting_get(url):
        nonlocal call_count
        call_count += 1
        return _mock_resp(fake_bytes, "image/jpeg")

    fetcher = ImageFetcher(store=store, cache=cache, timeout_s=1, max_attempts=2)
    await fetcher.start()
    await _patch_client_get(fetcher, counting_get)
    # 两条都 submit 同一 URL(在 worker 抢到之前)。
    # 第二个 submit 应该挂在 pending,不重复入队。
    fetcher.submit(msg1.id, ["http://x.test/dup.jpg"])
    fetcher.submit(msg2.id, ["http://x.test/dup.jpg"])
    # 此时 queue 只有 1 个 URL,pending 表里 1 个 URL 对应 2 个写入
    assert fetcher.queue_size() == 1
    assert fetcher.inflight_urls() == 1
    await _drain(fetcher)
    await fetcher.stop()

    expected_sha = hashlib.sha256(fake_bytes).hexdigest()
    assert call_count == 1, "expected exactly one HTTP fetch for duplicate URL"
    metas1 = store.get_message_images_meta([msg1.id])
    metas2 = store.get_message_images_meta([msg2.id])
    # 两条消息的 sha 都应该回填成功
    assert metas1[msg1.id][0]["sha256"] == expected_sha
    assert metas2[msg2.id][0]["sha256"] == expected_sha


@pytest.mark.asyncio
async def test_fetcher_dedupe_failure_propagates_to_all_pending(store_and_cache):
    """同 URL 多条 pending,HTTP 失败时所有 pending 都不写 sha(全部 NULL)。"""
    store, cache = store_and_cache
    msg1 = BufferedMessage(
        ts=100,
        adapter="ob11",
        group_id="g1",
        user_id="u1",
        nickname="u1",
        content="x",
        image_urls=["http://x.test/fail.jpg"],
    )
    msg2 = BufferedMessage(
        ts=101,
        adapter="ob11",
        group_id="g1",
        user_id="u1",
        nickname="u1",
        content="x",
        image_urls=["http://x.test/fail.jpg"],
    )
    store.append(msg1)
    store.append(msg2)

    fetcher = ImageFetcher(store=store, cache=cache, timeout_s=1, max_attempts=2)
    await fetcher.start()
    await _patch_client_get(fetcher, lambda url: (_ for _ in ()).throw(Exception("boom")))
    fetcher.submit(msg1.id, ["http://x.test/fail.jpg"])
    fetcher.submit(msg2.id, ["http://x.test/fail.jpg"])
    await _drain(fetcher)
    await fetcher.stop()

    assert store.get_message_images_meta([msg1.id])[msg1.id][0]["sha256"] is None
    assert store.get_message_images_meta([msg2.id])[msg2.id][0]["sha256"] is None


# --- httpx client reuse(优化 4) ---


@pytest.mark.asyncio
async def test_fetcher_reuses_single_httpx_client_across_fetches(store_and_cache):
    """start() 时建一个 AsyncClient,stop() 时 aclose 一次,期间所有 fetch 都用它。"""
    store, cache = store_and_cache
    msg = BufferedMessage(
        ts=100,
        adapter="ob11",
        group_id="g1",
        user_id="u1",
        nickname="u1",
        content="x",
        image_urls=["http://x.test/a.jpg"],
    )
    store.append(msg)

    fetcher = ImageFetcher(store=store, cache=cache, timeout_s=1, max_attempts=1)
    await fetcher.start()
    client_instance = fetcher._client
    assert client_instance is not None
    # 跑两次,客户端实例不应该变
    fake_bytes = b"\xff\xd8\xff\xe0pic"
    await _patch_client_get(fetcher, lambda url: _mock_resp(fake_bytes, "image/jpeg"))
    fetcher.submit(msg.id, ["http://x.test/a.jpg"])
    await _drain(fetcher)
    assert fetcher._client is client_instance
    fetcher.submit(msg.id, ["http://x.test/b.jpg"])  # 不同 URL,同一 client
    fetcher._client.get = AsyncMock(side_effect=lambda url: _mock_resp(fake_bytes, "image/jpeg"))
    await _drain(fetcher)
    assert fetcher._client is client_instance
    await fetcher.stop()
    # stop 后 client 应该被关掉、置 None
    assert fetcher._client is None
