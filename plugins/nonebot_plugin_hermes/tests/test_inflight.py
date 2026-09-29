"""InflightRegistry 单元测试。"""

from __future__ import annotations

from nonebot_plugin_hermes.core.inflight import (
    MAX_REFIRE_DEPTH,
    InflightRegistry,
)
from nonebot_plugin_hermes.core.message_buffer import BufferedMessage


def _msg(ts: int, user: str = "u1", content: str = "hi") -> BufferedMessage:
    return BufferedMessage(
        ts=ts,
        adapter="ob11",
        group_id="g1",
        user_id=user,
        nickname=user,
        content=content,
        image_urls=[],
        reply_to_ts=None,
        is_bot=False,
    )


def test_try_enter_on_empty_returns_entered():
    reg = InflightRegistry()
    result = reg.try_enter(("ob11", "group:g1"), _msg(100), is_explicit_trigger=False, original_msg_id=None, now_ms=100)
    assert result == "entered"
    assert reg.take_pending(("ob11", "group:g1")) is None
    reg.exit(("ob11", "group:g1"))


def test_second_try_enter_returns_pending_set():
    reg = InflightRegistry()
    reg.try_enter(("ob11", "group:g1"), _msg(100), is_explicit_trigger=False, original_msg_id=None, now_ms=100)
    result = reg.try_enter(("ob11", "group:g1"), _msg(200), is_explicit_trigger=False, original_msg_id=None, now_ms=200)
    assert result == "pending_set"
    pending = reg.take_pending(("ob11", "group:g1"))
    assert pending is not None and pending.msg.ts == 200
    reg.exit(("ob11", "group:g1"))


def test_take_pending_is_destructive():
    reg = InflightRegistry()
    reg.try_enter(("ob11", "group:g1"), _msg(100), is_explicit_trigger=False, original_msg_id=None, now_ms=100)
    reg.try_enter(("ob11", "group:g1"), _msg(200), is_explicit_trigger=False, original_msg_id=None, now_ms=200)
    reg.take_pending(("ob11", "group:g1"))
    assert reg.take_pending(("ob11", "group:g1")) is None
    reg.exit(("ob11", "group:g1"))


def test_exit_releases_slot_for_reentry():
    reg = InflightRegistry()
    reg.try_enter(("ob11", "group:g1"), _msg(100), is_explicit_trigger=False, original_msg_id=None, now_ms=100)
    reg.exit(("ob11", "group:g1"))
    result = reg.try_enter(("ob11", "group:g1"), _msg(200), is_explicit_trigger=False, original_msg_id=None, now_ms=200)
    assert result == "entered"
    reg.exit(("ob11", "group:g1"))


def test_pending_overwritten_by_later_msg():
    reg = InflightRegistry()
    reg.try_enter(("ob11", "group:g1"), _msg(100), is_explicit_trigger=False, original_msg_id=None, now_ms=100)
    reg.try_enter(
        ("ob11", "group:g1"), _msg(200, content="first"), is_explicit_trigger=False, original_msg_id=None, now_ms=200
    )
    reg.try_enter(
        ("ob11", "group:g1"), _msg(300, content="latest"), is_explicit_trigger=False, original_msg_id=None, now_ms=300
    )
    pending = reg.take_pending(("ob11", "group:g1"))
    assert pending is not None and pending.msg.content == "latest"
    reg.exit(("ob11", "group:g1"))


def test_different_keys_independent():
    reg = InflightRegistry()
    assert (
        reg.try_enter(("ob11", "group:g1"), _msg(100), is_explicit_trigger=False, original_msg_id=None, now_ms=100)
        == "entered"
    )
    assert (
        reg.try_enter(("ob11", "group:g2"), _msg(110), is_explicit_trigger=False, original_msg_id=None, now_ms=110)
        == "entered"
    )
    assert (
        reg.try_enter(("ob11", "private:u1"), _msg(120), is_explicit_trigger=False, original_msg_id=None, now_ms=120)
        == "entered"
    )
    reg.exit(("ob11", "group:g1"))
    reg.exit(("ob11", "group:g2"))
    reg.exit(("ob11", "private:u1"))


def test_max_refire_depth_constant_is_3():
    assert MAX_REFIRE_DEPTH == 3


def test_take_pending_on_missing_slot_returns_none():
    reg = InflightRegistry()
    assert reg.take_pending(("ob11", "group:nonexistent")) is None


def test_exit_on_missing_slot_is_noop():
    reg = InflightRegistry()
    reg.exit(("ob11", "group:nonexistent"))


def test_try_enter_explicit_on_empty_returns_entered():
    """空 slot + explicit → entered,pending 仍为 None。"""
    reg = InflightRegistry()
    result = reg.try_enter(
        ("ob11", "group:g1"),
        _msg(100),
        is_explicit_trigger=True,
        original_msg_id=12345,
        now_ms=100,
    )
    assert result == "entered"
    assert reg.take_pending(("ob11", "group:g1")) is None
    reg.exit(("ob11", "group:g1"))


def test_try_enter_bystander_pending_upgraded_by_explicit():
    """已 entered + bystander pending,新 explicit → pending_set 且 entry 升级为 explicit。"""
    reg = InflightRegistry()
    reg.try_enter(("ob11", "group:g1"), _msg(100), is_explicit_trigger=True, original_msg_id=None, now_ms=100)
    reg.try_enter(("ob11", "group:g1"), _msg(200), is_explicit_trigger=False, original_msg_id=None, now_ms=200)
    result = reg.try_enter(
        ("ob11", "group:g1"),
        _msg(300),
        is_explicit_trigger=True,
        original_msg_id=999,
        now_ms=300,
    )
    assert result == "pending_set"
    entry = reg.take_pending(("ob11", "group:g1"))
    assert entry is not None
    assert entry.is_explicit_trigger is True
    assert entry.original_msg_id == 999
    assert entry.msg.ts == 300
    reg.exit(("ob11", "group:g1"))


def test_try_enter_explicit_pending_kept_against_bystander():
    """已 entered + explicit pending,新 bystander → pending_kept,explicit 不被覆盖。"""
    reg = InflightRegistry()
    reg.try_enter(("ob11", "group:g1"), _msg(100), is_explicit_trigger=True, original_msg_id=None, now_ms=100)
    reg.try_enter(
        ("ob11", "group:g1"),
        _msg(200),
        is_explicit_trigger=True,
        original_msg_id=111,
        now_ms=200,
    )
    result = reg.try_enter(
        ("ob11", "group:g1"),
        _msg(300, content="bystander chatter"),
        is_explicit_trigger=False,
        original_msg_id=None,
        now_ms=300,
    )
    assert result == "pending_kept"
    entry = reg.take_pending(("ob11", "group:g1"))
    assert entry is not None
    assert entry.is_explicit_trigger is True
    assert entry.original_msg_id == 111
    assert entry.msg.ts == 200
    reg.exit(("ob11", "group:g1"))


def test_try_enter_explicit_pending_overwritten_by_newer_explicit():
    """已 entered + explicit pending,新 explicit → pending_set,latest wins。"""
    reg = InflightRegistry()
    reg.try_enter(("ob11", "group:g1"), _msg(100), is_explicit_trigger=True, original_msg_id=None, now_ms=100)
    reg.try_enter(
        ("ob11", "group:g1"),
        _msg(200),
        is_explicit_trigger=True,
        original_msg_id=111,
        now_ms=200,
    )
    result = reg.try_enter(
        ("ob11", "group:g1"),
        _msg(300),
        is_explicit_trigger=True,
        original_msg_id=222,
        now_ms=300,
    )
    assert result == "pending_set"
    entry = reg.take_pending(("ob11", "group:g1"))
    assert entry is not None
    assert entry.original_msg_id == 222
    assert entry.msg.ts == 300
    reg.exit(("ob11", "group:g1"))


def test_try_enter_bystander_pending_overwritten_by_newer_bystander():
    """已 entered + bystander pending,新 bystander → pending_set,latest wins。"""
    reg = InflightRegistry()
    reg.try_enter(("ob11", "group:g1"), _msg(100), is_explicit_trigger=True, original_msg_id=None, now_ms=100)
    reg.try_enter(
        ("ob11", "group:g1"),
        _msg(200, content="first"),
        is_explicit_trigger=False,
        original_msg_id=None,
        now_ms=200,
    )
    result = reg.try_enter(
        ("ob11", "group:g1"),
        _msg(300, content="latest"),
        is_explicit_trigger=False,
        original_msg_id=None,
        now_ms=300,
    )
    assert result == "pending_set"
    entry = reg.take_pending(("ob11", "group:g1"))
    assert entry is not None
    assert entry.msg.content == "latest"
    reg.exit(("ob11", "group:g1"))


def test_pending_entry_carries_original_msg_id():
    """try_enter 透传 original_msg_id 到 PendingEntry。"""
    reg = InflightRegistry()
    reg.try_enter(("ob11", "group:g1"), _msg(100), is_explicit_trigger=True, original_msg_id=None, now_ms=100)
    reg.try_enter(
        ("ob11", "group:g1"),
        _msg(200),
        is_explicit_trigger=True,
        original_msg_id="abc-123",
        now_ms=200,
    )
    entry = reg.take_pending(("ob11", "group:g1"))
    assert entry is not None
    assert entry.original_msg_id == "abc-123"
    reg.exit(("ob11", "group:g1"))


def test_pending_entry_msg_field_is_buffered_message():
    """PendingEntry.msg 字段是原 BufferedMessage,不变形。"""
    reg = InflightRegistry()
    reg.try_enter(("ob11", "group:g1"), _msg(100), is_explicit_trigger=True, original_msg_id=None, now_ms=100)
    original = _msg(200, content="payload")
    reg.try_enter(
        ("ob11", "group:g1"),
        original,
        is_explicit_trigger=False,
        original_msg_id=None,
        now_ms=200,
    )
    entry = reg.take_pending(("ob11", "group:g1"))
    assert entry is not None and entry.msg is original
    reg.exit(("ob11", "group:g1"))


def test_pending_kept_does_not_mutate_existing_pending():
    """pending_kept 路径下,后到 bystander 不修改 PendingEntry 任何字段。"""
    reg = InflightRegistry()
    reg.try_enter(("ob11", "group:g1"), _msg(100), is_explicit_trigger=True, original_msg_id=None, now_ms=100)
    reg.try_enter(
        ("ob11", "group:g1"),
        _msg(200, content="explicit-payload"),
        is_explicit_trigger=True,
        original_msg_id=42,
        now_ms=200,
    )
    # 多次后到 bystander 都被挡住,explicit pending 保持原样
    for ts in (300, 400, 500):
        result = reg.try_enter(
            ("ob11", "group:g1"),
            _msg(ts, content=f"bystander-{ts}"),
            is_explicit_trigger=False,
            original_msg_id=None,
            now_ms=ts,
        )
        assert result == "pending_kept"
    entry = reg.take_pending(("ob11", "group:g1"))
    assert entry is not None
    assert entry.is_explicit_trigger is True
    assert entry.original_msg_id == 42
    assert entry.msg.content == "explicit-payload"
    reg.exit(("ob11", "group:g1"))
