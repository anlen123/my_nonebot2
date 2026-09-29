"""MessageStore 单元测试。"""

from __future__ import annotations

import pytest

from nonebot_plugin_hermes.core.message_buffer import BufferedMessage
from nonebot_plugin_hermes.core.storage.message_store import MessageStore


def _msg(
    ts: int,
    group: str | None = "g1",
    user: str = "u1",
    content: str = "hi",
    image_urls=None,
    is_bot: bool = False,
) -> BufferedMessage:
    return BufferedMessage(
        ts=ts,
        adapter="ob11",
        group_id=group,
        user_id=user,
        nickname=user,
        content=content,
        image_urls=image_urls or [],
        is_bot=is_bot,
    )


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "messages.db"
    s = MessageStore(db_path=db_path)
    yield s
    s.close()


def test_append_assigns_autoincrement_id(store):
    msg = _msg(ts=100)
    store.append(msg)
    assert msg.id is not None
    assert msg.id > 0


def test_append_returns_msg_id(store):
    msg = _msg(ts=100)
    msg_id = store.append(msg)
    assert msg_id is not None
    assert msg_id == msg.id


def test_get_recent_returns_newest_first(store):
    for ts in (100, 200, 300):
        store.append(_msg(ts=ts))
    rows = store.get_recent("ob11", "g1", limit=10)
    assert [m.ts for m in rows] == [300, 200, 100]
    assert all(m.id is not None for m in rows)


def test_get_recent_returns_image_urls(store):
    msg = _msg(ts=100, image_urls=["http://x/a.jpg", "http://x/b.jpg"])
    store.append(msg)
    rows = store.get_recent("ob11", "g1", limit=10)
    assert len(rows) == 1
    assert rows[0].image_urls == ["http://x/a.jpg", "http://x/b.jpg"]


def test_get_recent_before_ts_strict_lt(store):
    for ts in (100, 200, 300):
        store.append(_msg(ts=ts))
    rows = store.get_recent("ob11", "g1", limit=10, before_ts=200)
    assert [m.ts for m in rows] == [100]


def test_group_isolation(store):
    store.append(_msg(ts=100, group="g1"))
    store.append(_msg(ts=200, group="g2"))
    g1_rows = store.get_recent("ob11", "g1", limit=10)
    g2_rows = store.get_recent("ob11", "g2", limit=10)
    assert [m.ts for m in g1_rows] == [100]
    assert [m.ts for m in g2_rows] == [200]


def test_private_chat_user_isolation(store):
    """私聊 group_id=None;不同 user_id 必须互不污染。"""
    store.append(_msg(ts=100, group=None, user="u1"))
    store.append(_msg(ts=200, group=None, user="u2"))
    u1_rows = store.get_recent("ob11", None, limit=10, owner_user_id="u1")
    u2_rows = store.get_recent("ob11", None, limit=10, owner_user_id="u2")
    assert [m.ts for m in u1_rows] == [100]
    assert [m.ts for m in u2_rows] == [200]


def test_private_requires_owner_user_id(store):
    with pytest.raises(ValueError, match="owner_user_id is required"):
        store.get_recent("ob11", None, limit=10)


def test_get_image_meta_for_message(store):
    msg = _msg(ts=100, image_urls=["http://x/a.jpg", "http://x/b.jpg"])
    store.append(msg)
    rows = store.get_message_images_meta([msg.id])
    assert msg.id in rows
    metas = rows[msg.id]
    assert len(metas) == 2
    assert metas[0]["url"] == "http://x/a.jpg"
    assert metas[0]["sha256"] is None  # fetcher 还没跑
    assert metas[0]["idx"] == 0


def test_update_image_sha(store):
    msg = _msg(ts=100, image_urls=["http://x/a.jpg"])
    store.append(msg)
    store.update_image_sha(message_id=msg.id, idx=0, sha256="abc123", mime_type="image/jpeg")
    rows = store.get_message_images_meta([msg.id])
    assert rows[msg.id][0]["sha256"] == "abc123"
    assert rows[msg.id][0]["mime_type"] == "image/jpeg"


def test_get_message_images_meta_unknown_id_omitted(store):
    """请求不存在的 id 不应抛,只是 dict 里没那把 key。"""
    rows = store.get_message_images_meta([9999])
    assert rows == {}


def test_get_message_images_meta_empty_list(store):
    assert store.get_message_images_meta([]) == {}


def test_vacuum_drops_old_rows(store):
    """retention 触发后老消息被删,新的留着。"""
    store.append(_msg(ts=1000))
    store.append(_msg(ts=2000))
    store.append(_msg(ts=3000))
    deleted = store.vacuum(min_ts=2000, max_rows=1000)
    assert deleted == 1
    rows = store.get_recent("ob11", "g1", limit=10)
    assert [m.ts for m in rows] == [3000, 2000]


def test_vacuum_drops_rows_over_max(store):
    for ts in range(100, 110):
        store.append(_msg(ts=ts))
    deleted = store.vacuum(min_ts=0, max_rows=3)
    assert deleted == 7
    rows = store.get_recent("ob11", "g1", limit=10)
    assert [m.ts for m in rows] == [109, 108, 107]


def test_vacuum_cascades_to_message_images(store):
    """删 messages 行时 message_images 应跟着删(级联)。"""
    msg = _msg(ts=100, image_urls=["http://x/a.jpg"])
    store.append(msg)
    msg_id = msg.id
    store.vacuum(min_ts=200, max_rows=10000)
    assert store.get_message_images_meta([msg_id]) == {}


def test_known_groups_returns_group_scope_tuples(store):
    store.append(_msg(ts=100, group="g1"))
    store.append(_msg(ts=200, group="g2"))
    store.append(_msg(ts=300, group=None, user="up"))
    groups = store.known_groups()
    g_set = set(groups)
    assert ("ob11", "g1") in g_set
    assert ("ob11", "g2") in g_set
    # 私聊使用同 bucket_key 助手合成的 scope
    assert any(s.startswith("@private:") and "up" in s for _, s in groups)


def test_limit_respected(store):
    for ts in range(1, 21):
        store.append(_msg(ts=ts))
    rows = store.get_recent("ob11", "g1", limit=5)
    assert len(rows) == 5
    assert rows[0].ts == 20


def test_close_idempotent(store):
    store.close()
    store.close()  # 二次关闭不抛


def test_counts_by_scope_groups_private_and_group_scopes(store):
    """按 scope 计数:群按 group_id 合并(不按发言人拆),私聊按 @private:<user_id>。

    /hermes-status 靠它统计每个 bucket 的消息数;之前它直接摸 MessageBuffer._buckets,
    而 SQLite 化之后那个属性已经不存在了。
    """
    store.append(_msg(1000, group="g1", user="u1", content="a"))
    store.append(_msg(1001, group="g1", user="u2", content="b"))
    store.append(_msg(1002, group="g2", user="u1", content="c"))
    store.append(_msg(1003, group=None, user="u9", content="d"))

    counts = store.counts_by_scope()

    assert counts[("ob11", "g1")] == 2, "同群不同人要合并计数"
    assert counts[("ob11", "g2")] == 1
    assert counts[("ob11", "@private:u9")] == 1
    assert set(counts) == set(store.known_groups()), "scope 口径必须与 known_groups 一致"


def test_counts_by_scope_empty_store(store):
    assert store.counts_by_scope() == {}


# ---------------------------------------------------------------------------
# message_forwards 附表(折叠消息全文)
# ---------------------------------------------------------------------------


def test_append_stores_forward_content(store):
    msg = _msg(ts=100, content='x <forwarded_messages count="2" preview="A / B"/>')
    msg.forward_content = '<forwarded_messages count="2">\nA: hi\nB: yo\n</forwarded_messages>'
    store.append(msg)
    got = store.get_recent(adapter="ob11", group_id="g1", limit=5)
    assert got[0].forward_content == msg.forward_content


def test_append_without_forward_content_writes_no_row(store):
    store.append(_msg(ts=100))
    n = store._conn.execute("SELECT COUNT(*) AS c FROM message_forwards").fetchone()["c"]
    assert n == 0


def test_get_recent_forward_content_none_for_plain_messages(store):
    store.append(_msg(ts=100))
    got = store.get_recent(adapter="ob11", group_id="g1", limit=5)
    assert got[0].forward_content is None


def test_vacuum_cascades_to_message_forwards(store):
    msg = _msg(ts=100)
    msg.forward_content = '<forwarded_messages count="1">\nA: x\n</forwarded_messages>'
    store.append(msg)
    store.vacuum(min_ts=200, max_rows=100)
    n = store._conn.execute("SELECT COUNT(*) AS c FROM message_forwards").fetchone()["c"]
    assert n == 0
