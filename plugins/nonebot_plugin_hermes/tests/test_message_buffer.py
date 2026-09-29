"""MessageBuffer (薄壳层) 单元测试。

注:LRU 淘汰相关的旧行为 (per_group_cap / total_groups_cap / read-promotes-LRU)
已经被 MessageStore + retention vacuum 取代,对应测试见 test_message_store.py 的
`test_vacuum_*`。本文件只覆盖薄壳层自己的事:转调 store + 触发 fetcher。
"""

from __future__ import annotations

import pytest

from nonebot_plugin_hermes.core.message_buffer import (
    BufferedMessage,
    MessageBuffer,
    is_private_key,
)
from nonebot_plugin_hermes.core.storage.image_cache import ImageCache
from nonebot_plugin_hermes.core.storage.image_fetcher import ImageFetcher
from nonebot_plugin_hermes.core.storage.message_store import MessageStore


def _msg(
    ts: int,
    group: str | None = "g1",
    user: str = "u1",
    content: str = "hi",
    image_urls=None,
) -> BufferedMessage:
    return BufferedMessage(
        ts=ts,
        adapter="ob11",
        group_id=group,
        user_id=user,
        nickname=user,
        content=content,
        image_urls=image_urls or [],
        reply_to_ts=None,
        is_bot=False,
    )


@pytest.fixture
def buf(tmp_path):
    store = MessageStore(db_path=tmp_path / "m.db")
    cache = ImageCache(cache_dir=tmp_path / "imgs", quota_bytes=10 * 1024 * 1024)
    fetcher = ImageFetcher(store=store, cache=cache)
    yield MessageBuffer(store=store, fetcher=fetcher)
    store.close()


# --- 基础 append/get_recent 行为(与之前的 MessageBuffer 语义对齐) ---


def test_append_and_get_recent_returns_newest_first(buf):
    for ts in (100, 200, 300):
        buf.append(_msg(ts))
    recent = buf.get_recent("ob11", "g1", limit=10)
    assert [m.ts for m in recent] == [300, 200, 100]


def test_append_assigns_id_via_store(buf):
    msg = _msg(ts=100)
    buf.append(msg)
    assert msg.id is not None and msg.id > 0


def test_get_recent_returns_msg_with_id(buf):
    buf.append(_msg(ts=100))
    rows = buf.get_recent("ob11", "g1", limit=10)
    assert rows[0].id is not None


def test_get_recent_with_before_ts_filter(buf):
    for ts in (100, 200, 300, 400):
        buf.append(_msg(ts))
    recent = buf.get_recent("ob11", "g1", limit=10, before_ts=300)
    assert [m.ts for m in recent] == [200, 100]


def test_get_recent_unknown_group_returns_empty(buf):
    assert buf.get_recent("ob11", "ghost", limit=10) == []


def test_known_groups_lists_appended_buckets(buf):
    buf.append(_msg(100, group="A"))
    buf.append(_msg(200, group="B"))
    buf.append(_msg(300, group="C"))
    known = set(buf.known_groups())
    assert ("ob11", "A") in known
    assert ("ob11", "B") in known
    assert ("ob11", "C") in known


def test_private_message_uses_user_as_group_key(buf):
    buf.append(_msg(100, group=None, user="alice"))
    recent = buf.get_recent("ob11", None, limit=10, owner_user_id="alice")
    assert len(recent) == 1


def test_get_recent_private_without_owner_raises(buf):
    """守卫:私聊查询忘传 owner_user_id 必须早爆,不能静默返空。"""
    with pytest.raises(ValueError, match="owner_user_id is required"):
        buf.get_recent("ob11", None, limit=10)


def test_is_private_key_helper(buf):
    """is_private_key 区分群桶 / 私聊桶,无需 string-match。"""
    buf.append(_msg(100, group="g1"))
    buf.append(_msg(200, group=None, user="alice"))

    keys = buf.known_groups()
    assert sum(1 for k in keys if is_private_key(k)) == 1
    assert sum(1 for k in keys if not is_private_key(k)) == 1


def test_limit_respected(buf):
    for ts in range(1, 21):
        buf.append(_msg(ts))
    recent = buf.get_recent("ob11", "g1", limit=5)
    assert len(recent) == 5
    assert recent[0].ts == 20


# --- 薄壳层自己的特殊行为:append 带图时触发 fetcher.submit ---


def test_append_with_images_triggers_fetcher_submit(buf):
    """带图消息 append 后 fetcher.submit 接到任务(queue 非空)。"""
    msg = _msg(100, image_urls=["http://x/a.jpg", "http://x/b.jpg"])
    buf.append(msg)
    assert buf._fetcher.queue_size() == 2


def test_append_without_images_does_not_submit(buf):
    buf.append(_msg(100))
    assert buf._fetcher.queue_size() == 0


def test_append_with_store_failure_does_not_submit(buf, monkeypatch):
    """若 store.append 返回 None (写库失败),fetcher.submit 不应被调用。"""

    def fake_append(_msg):
        return None

    monkeypatch.setattr(buf._store, "append", fake_append)
    buf.append(_msg(100, image_urls=["http://x/a.jpg"]))
    assert buf._fetcher.queue_size() == 0


# --- BufferedMessage dataclass 自身的字段约束 ---


def test_buffered_message_id_defaults_to_none():
    msg = BufferedMessage(
        ts=100,
        adapter="ob11",
        group_id="g1",
        user_id="u1",
        nickname="u1",
        content="hi",
    )
    assert msg.id is None


def test_buffered_message_id_can_be_set():
    msg = BufferedMessage(
        ts=100,
        adapter="ob11",
        group_id="g1",
        user_id="u1",
        nickname="u1",
        content="hi",
    )
    msg.id = 42
    assert msg.id == 42
