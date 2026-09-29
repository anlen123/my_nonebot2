"""hermes-repair-sessions:解开被 compression 血缘歧义卡死的会话。

背景:插件早期不采纳上游轮换后的 session id(响应头 X-Hermes-Session-Id),每轮都把
会话钉回被 end_reason='compression' 关闭的父会话。于是每次压缩都从同一个父会话再分叉
一个兄弟快照,live 子会话不止一个之后,上游 find_live_compression_child() 判定歧义
fail-closed —— 该会话从此一条消息也写不进去。

本脚本只做两件事:把父会话重新打开、把那些快照子会话标记为 ended。不删任何消息行。
"""

from __future__ import annotations

import sqlite3

import pytest

from hermes_repair_sessions import repair, scan

_SNAPSHOT_FILTER_COLS = """
    CREATE TABLE sessions (
        id                TEXT PRIMARY KEY,
        parent_session_id TEXT,
        started_at        REAL,
        ended_at          REAL,
        end_reason        TEXT,
        message_count     INTEGER DEFAULT 0,
        model_config      TEXT,
        source            TEXT
    )
"""


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(_SNAPSHOT_FILTER_COLS)
    c.execute("CREATE TABLE messages (session_id TEXT, timestamp REAL, content TEXT)")
    return c


def _session(
    conn, sid, *, parent=None, started=1000.0, ended=None, reason=None, model_config=None, source="api_server"
):
    conn.execute(
        "INSERT INTO sessions (id, parent_session_id, started_at, ended_at, end_reason, model_config, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (sid, parent, started, ended, reason, model_config, source),
    )


def _messages(conn, sid, timestamps):
    conn.executemany(
        "INSERT INTO messages (session_id, timestamp, content) VALUES (?, ?, 'x')",
        [(sid, t) for t in timestamps],
    )


def _stuck_fixture(conn) -> str:
    """一个被压缩关闭的父会话 + 两个快照子会话。

    快照的形态是整段对话在一次轮换里批量写入:行数可能上百,时间戳逐行递增但
    首尾只差几秒。真会话则跨天(线上实测:子会话 0.4~3.2 秒,父会话 72~89 天)。
    """
    parent = "hermes-ob11+group+g1+u1"
    _session(conn, parent, started=1000.0, ended=2000.0, reason="compression")
    _messages(conn, parent, [1001.0, 90000.0, 300000.0])  # 关闭后仍在写(旧版本容忍)
    _session(conn, "20260704_194519_714fc7", parent=parent, started=2000.0)
    _messages(conn, "20260704_194519_714fc7", [2000.0, 2000.4, 2000.9])
    _session(conn, "20260808_134001_e7398a", parent=parent, started=2500.0)
    _messages(conn, "20260808_134001_e7398a", [400000.0, 400001.2, 400002.6])
    return parent


def test_scan_flags_parent_with_multiple_live_children(conn):
    parent = _stuck_fixture(conn)

    found = scan(conn)

    assert [c.parent_id for c in found] == [parent]
    assert found[0].blocked_reason is None
    assert len(found[0].live_children) == 2


def test_scan_skips_parent_upstream_can_still_recover(conn):
    """恰好一个 live 子会话时上游自己能 adopt,没必要动刀。"""
    parent = "hermes-ob11+group+g1+u1"
    _session(conn, parent, ended=2000.0, reason="compression")
    _session(conn, "20260704_194519_714fc7", parent=parent, started=2000.0)

    assert scan(conn) == []


def test_scan_ignores_sessions_not_issued_by_the_plugin(conn):
    """桌面/CLI 会话有自己的生命周期,不在本脚本职责内。"""
    parent = "20260518_101010_deadbe"
    _session(conn, parent, ended=2000.0, reason="compression")
    _session(conn, "20260704_194519_714fc7", parent=parent, started=2000.0)
    _session(conn, "20260704_194600_aa11bb", parent=parent, started=2100.0)

    assert scan(conn) == []


def test_scan_ignores_branch_and_delegate_children(conn):
    """分支 / 子代理不是 continuation,不该被算进歧义,也不该被退休。"""
    parent = "hermes-ob11+group+g1+u1"
    _session(conn, parent, ended=2000.0, reason="compression")
    _session(conn, "20260704_194519_714fc7", parent=parent, started=2000.0)
    _session(conn, "branch-1", parent=parent, started=2100.0, model_config='{"_branched_from": "x"}')
    _session(conn, "delegate-1", parent=parent, started=2200.0, model_config='{"_delegate_from": "x"}')
    _session(conn, "tool-1", parent=parent, started=2300.0, source="tool")

    assert scan(conn) == []


def test_scan_refuses_when_a_child_holds_newer_real_turns(conn):
    """子会话的消息跨越很长时间 = 真的被用起来了的 continuation(不是一次性快照)。
    重开父会话会把它甩掉,这种情况必须停手交给人。"""
    parent = "hermes-ob11+group+g1+u1"
    _session(conn, parent, ended=2000.0, reason="compression")
    _messages(conn, parent, [1500.0])
    _session(conn, "20260704_194519_714fc7", parent=parent, started=2000.0)
    _messages(conn, "20260704_194519_714fc7", [2000.0, 2000.4, 2000.9])
    _session(conn, "20260808_134001_e7398a", parent=parent, started=2500.0)
    _messages(conn, "20260808_134001_e7398a", [2500.0, 60000.0, 900000.0])  # 跨天,真的在续写

    found = scan(conn)

    assert len(found) == 1
    assert found[0].blocked_reason is not None
    assert "20260808_134001_e7398a" in found[0].blocked_reason


def test_repair_reopens_parent_and_retires_snapshots(conn):
    parent = _stuck_fixture(conn)

    changed = repair(conn, scan(conn), now=5000.0)

    row = conn.execute("SELECT ended_at, end_reason FROM sessions WHERE id=?", (parent,)).fetchone()
    assert row["ended_at"] is None and row["end_reason"] is None
    kids = conn.execute("SELECT id, ended_at, end_reason FROM sessions WHERE parent_session_id=?", (parent,)).fetchall()
    assert all(k["ended_at"] == 5000.0 and k["end_reason"] == "orphan_cleanup" for k in kids)
    assert changed == (1, 2)


def test_repair_keeps_every_message_row(conn):
    parent = _stuck_fixture(conn)
    before = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    repair(conn, scan(conn), now=5000.0)

    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == before
    assert conn.execute("SELECT COUNT(*) FROM messages WHERE session_id=?", (parent,)).fetchone()[0] == 3


def test_repair_skips_blocked_candidates(conn):
    parent = "hermes-ob11+group+g1+u1"
    _session(conn, parent, ended=2000.0, reason="compression")
    _messages(conn, parent, [1500.0])
    _session(conn, "20260704_194519_714fc7", parent=parent, started=2000.0)
    _messages(conn, "20260704_194519_714fc7", [2000.0, 2000.4, 2000.9])
    _session(conn, "20260808_134001_e7398a", parent=parent, started=2500.0)
    _messages(conn, "20260808_134001_e7398a", [2500.0, 60000.0, 900000.0])

    changed = repair(conn, scan(conn), now=5000.0)

    assert changed == (0, 0)
    assert conn.execute("SELECT ended_at FROM sessions WHERE id=?", (parent,)).fetchone()["ended_at"] == 2000.0
