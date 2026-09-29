"""get_message_images 工具测试。"""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError

from nonebot_plugin_hermes.core.message_buffer import BufferedMessage
from nonebot_plugin_hermes.core.routing import CallerScope
from nonebot_plugin_hermes.core.storage.image_cache import ImageCache
from nonebot_plugin_hermes.core.storage.message_store import MessageStore
from nonebot_plugin_hermes.mcp.tools.get_message_images import (
    GetMessageImagesInput,
    get_message_images_impl,
)

# 这些用例测的是工具自身的行为,不是反向通道的范围收敛(那在 test_mcp_scope.py),
# 所以统一给开发模式 scope。scope 是必填参数:漏传是 TypeError 而不是静默放行。
_ANY_SCOPE = CallerScope.dev()


@pytest.fixture
def setup(tmp_path):
    store = MessageStore(db_path=tmp_path / "m.db")
    cache = ImageCache(cache_dir=tmp_path / "imgs", quota_bytes=10 * 1024 * 1024)
    yield store, cache
    store.close()


def _seed_image(store: MessageStore, cache: ImageCache, content: bytes, mime: str = "image/jpeg") -> tuple[int, str]:
    msg = BufferedMessage(
        ts=100,
        adapter="ob11",
        group_id="g1",
        user_id="u1",
        nickname="u1",
        content="x",
        image_urls=["http://x/seed.jpg"],
    )
    store.append(msg)
    sha = cache.put(content, mime)
    store.update_image_sha(msg.id, 0, sha, mime)
    return msg.id, sha


def test_happy_path_returns_text_and_image_blocks(setup):
    store, cache = setup
    msg_id, _sha = _seed_image(store, cache, b"\xff\xd8\xff\xe0FAKE_JPEG_BYTES")
    inp = GetMessageImagesInput(message_ids=[msg_id])
    result = asyncio.run(get_message_images_impl(inp, store=store, cache=cache, scope=_ANY_SCOPE))
    # content[0] 是 JSON header,后面成对 (marker TextContent, ImageContent)
    assert len(result) >= 3
    assert result[0].type == "text"
    header = json.loads(result[0].text)
    assert header["results"][0]["message_id"] == msg_id
    assert header["results"][0]["available"] is True
    assert header["results"][0]["mime"] == "image/jpeg"
    # 第一对 marker + ImageContent
    assert result[1].type == "text"
    assert f"[image for m:{msg_id} idx=0]" in result[1].text
    assert result[2].type == "image"
    assert len(result[2].data) > 0  # base64 data 非空


def test_cache_miss_marks_unavailable(setup):
    store, cache = setup
    msg = BufferedMessage(
        ts=100,
        adapter="ob11",
        group_id="g1",
        user_id="u1",
        nickname="u1",
        content="x",
        image_urls=["http://x/a.jpg"],
    )
    store.append(msg)
    # 没 update_image_sha → sha 仍为 NULL
    inp = GetMessageImagesInput(message_ids=[msg.id])
    result = asyncio.run(get_message_images_impl(inp, store=store, cache=cache, scope=_ANY_SCOPE))
    header = json.loads(result[0].text)
    assert header["results"][0]["available"] is False
    assert header["results"][0]["reason"] == "cache_miss"
    # 不应有 ImageContent 块
    assert len(result) == 1


def test_cache_miss_when_file_deleted_externally(setup):
    store, cache = setup
    msg_id, sha = _seed_image(store, cache, b"\xff\xd8\xff\xe0BYTES")
    # 手动从 cache 删除文件,模拟被 LRU 淘汰
    for p in cache._dir.iterdir():
        if p.name.startswith(sha):
            p.unlink()
    inp = GetMessageImagesInput(message_ids=[msg_id])
    result = asyncio.run(get_message_images_impl(inp, store=store, cache=cache, scope=_ANY_SCOPE))
    header = json.loads(result[0].text)
    assert header["results"][0]["available"] is False
    assert header["results"][0]["reason"] == "cache_miss"


def test_not_found_for_unknown_msg_id(setup):
    store, cache = setup
    inp = GetMessageImagesInput(message_ids=[9999])
    result = asyncio.run(get_message_images_impl(inp, store=store, cache=cache, scope=_ANY_SCOPE))
    header = json.loads(result[0].text)
    assert header["results"][0]["available"] is False
    assert header["results"][0]["reason"] == "not_found"


def test_too_large_marks_unavailable(setup):
    store, cache = setup
    huge = b"X" * (6 * 1024 * 1024)  # 6MB > 5MB cap
    msg_id, _ = _seed_image(store, cache, huge)
    inp = GetMessageImagesInput(message_ids=[msg_id])
    result = asyncio.run(get_message_images_impl(inp, store=store, cache=cache, scope=_ANY_SCOPE))
    header = json.loads(result[0].text)
    assert header["results"][0]["available"] is False
    assert header["results"][0]["reason"] == "too_large"


def test_max_4_message_ids_input():
    with pytest.raises(ValidationError):
        GetMessageImagesInput(message_ids=[1, 2, 3, 4, 5])


def test_empty_message_ids_rejected():
    with pytest.raises(ValidationError):
        GetMessageImagesInput(message_ids=[])


def test_multi_image_message_returns_all_images(setup):
    """一条消息里多张图都应被取回。"""
    store, cache = setup
    msg = BufferedMessage(
        ts=100,
        adapter="ob11",
        group_id="g1",
        user_id="u1",
        nickname="u1",
        content="x",
        image_urls=["http://x/a.jpg", "http://x/b.jpg"],
    )
    store.append(msg)
    sha_a = cache.put(b"BYTES_A", "image/jpeg")
    sha_b = cache.put(b"BYTES_B", "image/png")
    store.update_image_sha(msg.id, 0, sha_a, "image/jpeg")
    store.update_image_sha(msg.id, 1, sha_b, "image/png")

    inp = GetMessageImagesInput(message_ids=[msg.id])
    result = asyncio.run(get_message_images_impl(inp, store=store, cache=cache, scope=_ANY_SCOPE))
    header = json.loads(result[0].text)
    assert len(header["results"]) == 2
    assert all(r["available"] for r in header["results"])
    # 2 张图 = 2 个 (marker, ImageContent) 对 + 1 个 header = 5 块
    assert len(result) == 1 + 2 * 2


def test_mixed_available_and_not_found(setup):
    """合法 id 与不存在 id 混在一起,各自正确分流。"""
    store, cache = setup
    msg_id, _sha = _seed_image(store, cache, b"\xff\xd8\xff\xe0BYTES")
    inp = GetMessageImagesInput(message_ids=[msg_id, 9999])
    result = asyncio.run(get_message_images_impl(inp, store=store, cache=cache, scope=_ANY_SCOPE))
    header = json.loads(result[0].text)
    assert len(header["results"]) == 2
    by_msg = {r["message_id"]: r for r in header["results"]}
    assert by_msg[msg_id]["available"] is True
    assert by_msg[9999]["available"] is False
    assert by_msg[9999]["reason"] == "not_found"
    # 只有 msg_id 那张产生了 ImageContent
    assert len(result) == 1 + 2  # header + 1 (marker, ImageContent)
