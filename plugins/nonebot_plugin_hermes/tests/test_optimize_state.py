"""hermes-optimize-state:把历史 turn 里重复注入的 prompt 脚手架清出 state.db。

背景:reactive/passive 两路都把 `<recent_messages>` 窗口裹进 user message,每个 turn
重发整扇窗口。生产库实测 user 侧均 2,250 字符 vs assistant 262,窗口 35~63 行而每轮
只新增 1~2 行 —— 同一条群发言在 transcript 里被存了几十遍。Hermes 每轮回放整条
transcript,所以这既是磁盘也是账单。

本脚本把窗口回溯成增量形状:每条群发言只保留第一次出现,往后的重复删掉。按 `[m:<id>]`
消息主键去重(不是整行字符串),昵称改了也不会把同一条消息当成两条留下来。

不做的事:不删任何一条独有内容(有自证不变量);不碰 CLI/TUI/cron 会话;不改 assistant
侧模型自己的输出。
"""

from __future__ import annotations

import sqlite3

import pytest

from hermes_optimize_state import (
    apply_rewrites,
    logical_size_bytes,
    rebuild_in_progress,
    reclaim,
    render_sample,
    resolve_db,
    rewrite_user_content,
    scan,
    window_entries,
)

# 与上游 references/state.db 同形的最小 schema:FTS 由触发器维护,所以触发器必须在场 ——
# 改写走 UPDATE,索引一致性正是靠它们。WHEN 子句里的 rebuild marker 门也照抄,
# 否则「rebuild 半途拒绝执行」这条闸门测的就不是真情形。
_DDL = """
CREATE TABLE sessions (
    id      TEXT PRIMARY KEY,
    source  TEXT NOT NULL
);
CREATE TABLE messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    role        TEXT NOT NULL,
    content     TEXT,
    tool_name   TEXT,
    tool_calls  TEXT,
    timestamp   REAL NOT NULL,
    api_content TEXT
);
CREATE TABLE state_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE VIRTUAL TABLE messages_fts USING fts5(
    content, tool_name, tool_calls,
    content='messages', content_rowid='id'
);
CREATE VIEW messages_fts_trigram_src AS
    SELECT id, role, content, tool_name, tool_calls FROM messages WHERE role <> 'tool';
CREATE VIRTUAL TABLE messages_fts_trigram USING fts5(
    content, tool_name, tool_calls,
    content='messages_fts_trigram_src', content_rowid='id', tokenize='trigram'
);
CREATE TRIGGER messages_fts_insert AFTER INSERT ON messages
WHEN (new.id > COALESCE((SELECT CAST(value AS INTEGER) FROM state_meta
                         WHERE key = 'fts_rebuild_high_water'), -1))
BEGIN
    INSERT INTO messages_fts(rowid, content, tool_name, tool_calls)
    VALUES (new.id, new.content, new.tool_name, new.tool_calls);
END;
CREATE TRIGGER messages_fts_update
AFTER UPDATE OF content, tool_name, tool_calls ON messages
WHEN (old.content IS NOT new.content)
   AND (old.id > COALESCE((SELECT CAST(value AS INTEGER) FROM state_meta
                           WHERE key = 'fts_rebuild_high_water'), -1))
BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content, tool_name, tool_calls)
    VALUES ('delete', old.id, old.content, old.tool_name, old.tool_calls);
    INSERT INTO messages_fts(rowid, content, tool_name, tool_calls)
    VALUES (new.id, new.content, new.tool_name, new.tool_calls);
END;
CREATE TRIGGER messages_fts_trigram_insert AFTER INSERT ON messages
WHEN new.role <> 'tool'
   AND (new.id > COALESCE((SELECT CAST(value AS INTEGER) FROM state_meta
                           WHERE key = 'fts_rebuild_high_water'), -1))
BEGIN
    INSERT INTO messages_fts_trigram(rowid, content, tool_name, tool_calls)
    VALUES (new.id, new.content, new.tool_name, new.tool_calls);
END;
CREATE TRIGGER messages_fts_trigram_update
AFTER UPDATE OF content, tool_name, tool_calls, role ON messages
WHEN (old.content IS NOT new.content)
   AND (old.id > COALESCE((SELECT CAST(value AS INTEGER) FROM state_meta
                           WHERE key = 'fts_rebuild_high_water'), -1))
BEGIN
    INSERT INTO messages_fts_trigram(messages_fts_trigram, rowid, content, tool_name, tool_calls)
    SELECT 'delete', old.id, old.content, old.tool_name, old.tool_calls WHERE old.role <> 'tool';
    INSERT INTO messages_fts_trigram(rowid, content, tool_name, tool_calls)
    SELECT new.id, new.content, new.tool_name, new.tool_calls WHERE new.role <> 'tool';
END;
"""

_REMINDER = (
    "\n\n请用 submit_decision JSON 对象回复(字段:should_reply / reply_text / "
    "topic_hint / should_exit_active),不要直接说话或扮演动作。"
)


def _reactive(window: list[str], current: str, *, topic: str | None = None) -> str:
    """与 build_reactive_user_content 同形的 user content。"""
    runtime = ["<runtime_state>", "mode: reactive", "adapter: ob11", "group_id: g1", "you: [user=小助手 id=b1]"]
    if topic:
        runtime.append(f"topic_hint: {topic}")
    runtime.append("</runtime_state>")
    blocks = [
        "\n".join(runtime),
        "<recent_messages>\n" + "\n".join(window) + "\n</recent_messages>",
        f"<current_message>\n{current}\n</current_message>",
    ]
    return "\n\n".join(blocks) + _REMINDER


def _passive(window: list[str], current: str) -> str:
    """与 build_passive_user_content 同形:窗口 + 裸文本,无 runtime_state/reminder。"""
    return "<recent_messages>\n" + "\n".join(window) + "\n</recent_messages>\n\n" + current


def _line(mid: int, text: str, *, bot: bool = False, nick: str = "阿甲", uid: str = "u1") -> str:
    return f"[m:{mid}] {'[bot] ' if bot else ''}[user={nick} id={uid}]: {text}"


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    c = sqlite3.connect(tmp_path / "state.db")
    c.row_factory = sqlite3.Row
    c.executescript(_DDL)
    c.execute("INSERT INTO sessions (id, source) VALUES ('hermes-ob11+group+g1+u1', 'api_server')")
    return c


def _add(conn, content: str, *, sid: str = "hermes-ob11+group+g1+u1", role: str = "user", ts: float = 1.0) -> int:
    cur = conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
        (sid, role, content, ts),
    )
    return int(cur.lastrowid)


# ── 纯函数:逐行改写 ─────────────────────────────────────────────────────────


def test_repeated_window_lines_are_dropped():
    """第二个 turn 的窗口只该留下新增那一行。"""
    seen: set[str] = set()
    first = rewrite_user_content(_reactive([_line(1, "早"), _line(2, "在吗")], "[user=阿甲 id=u1]: 在吗"), seen)
    seen.update(first.window_keys)

    second = rewrite_user_content(
        _reactive([_line(1, "早"), _line(2, "在吗"), _line(3, "帮我看下")], "[user=阿甲 id=u1]: 帮我看下"),
        seen,
    )

    assert "[m:3]" in second.content
    assert "[m:1]" not in second.content
    assert "[m:2]" not in second.content


def test_window_block_removed_when_all_lines_seen():
    """整扇窗口都见过时,连标签一起消失 —— 留个空块只是换一种浪费。"""
    seen = {"m:1", "m:2"}
    r = rewrite_user_content(_reactive([_line(1, "早"), _line(2, "在吗")], "[user=阿甲 id=u1]: 在吗"), seen)

    assert "<recent_messages>" not in r.content
    assert "</recent_messages>" not in r.content


def test_protocol_reminder_is_removed():
    """协议尾句是静态样板,system 端的 decision_protocol 已经写着。"""
    r = rewrite_user_content(_reactive([_line(1, "早")], "[user=阿甲 id=u1]: 早"), set())

    assert "submit_decision" not in r.content


def test_runtime_state_and_current_message_survive():
    """这两块是本 turn 的实况,不是重复注入,一个字都不能动。"""
    r = rewrite_user_content(_reactive([_line(1, "早")], "[user=阿甲 id=u1]: 早", topic="修网关"), set())

    assert "<runtime_state>" in r.content
    assert "you: [user=小助手 id=b1]" in r.content
    assert "topic_hint: 修网关" in r.content
    assert "<current_message>\n[user=阿甲 id=u1]: 早\n</current_message>" in r.content


def test_passive_shape_keeps_trailing_plain_text():
    """passive 路径没有 current_message 标签,裸文本必须留住。"""
    seen = {"m:1"}
    r = rewrite_user_content(_passive([_line(1, "早")], "这是当前这句"), seen)

    assert "这是当前这句" in r.content
    assert "<recent_messages>" not in r.content


def test_content_without_window_is_left_alone():
    """没有窗口的行(私聊、工具结果)原样返回,changed=False。"""
    r = rewrite_user_content("就是一句普通的话", set())

    assert r.changed is False
    assert r.content == "就是一句普通的话"


def test_dedup_keys_on_message_id_not_line_text():
    """同一条消息昵称改了,不该因为字符串不同而被当成新行留下来。"""
    seen: set[str] = set()
    first = rewrite_user_content(_reactive([_line(1, "早", nick="阿甲")], "x"), seen)
    seen.update(first.window_keys)

    second = rewrite_user_content(_reactive([_line(1, "早", nick="改了个名")], "y"), seen)

    assert "<recent_messages>" not in second.content


# ── DB 层 ───────────────────────────────────────────────────────────────────


def test_scan_reports_savings_without_writing(db):
    win = [_line(1, "早"), _line(2, "在吗")]
    rid = _add(db, _reactive(win, "[user=阿甲 id=u1]: 在吗"))
    _add(db, _reactive(win + [_line(3, "帮我看下")], "[user=阿甲 id=u1]: 帮我看下"))
    before = db.execute("SELECT content FROM messages WHERE id = ?", (rid,)).fetchone()["content"]

    report = scan(db)

    assert report.rows_to_rewrite == 2
    assert report.bytes_after < report.bytes_before
    assert db.execute("SELECT content FROM messages WHERE id = ?", (rid,)).fetchone()["content"] == before


def test_apply_rewrites_shrinks_repeated_windows(db):
    win = [_line(1, "早"), _line(2, "在吗")]
    _add(db, _reactive(win, "[user=阿甲 id=u1]: 在吗"))
    second = _add(db, _reactive(win + [_line(3, "帮我看下")], "[user=阿甲 id=u1]: 帮我看下"))

    apply_rewrites(db)

    content = db.execute("SELECT content FROM messages WHERE id = ?", (second,)).fetchone()["content"]
    assert "[m:3]" in content
    assert "[m:1]" not in content


def test_only_api_server_sessions_are_touched(db):
    """CLI / TUI / cron 会话不是插件流量,一行都不碰。"""
    db.execute("INSERT INTO sessions (id, source) VALUES ('20260819_120000_abc', 'cli')")
    win = [_line(1, "早")]
    _add(db, _reactive(win, "x"))
    cli_row = _add(db, _reactive(win, "x"), sid="20260819_120000_abc")
    before = db.execute("SELECT content FROM messages WHERE id = ?", (cli_row,)).fetchone()["content"]

    apply_rewrites(db)

    assert db.execute("SELECT content FROM messages WHERE id = ?", (cli_row,)).fetchone()["content"] == before


def test_apply_clears_api_content_sidecar(db):
    """api_content 是 content 的替代品;改了 content 却留着旧 sidecar 等于没改。"""
    rid = _add(db, _reactive([_line(1, "早"), _line(2, "在吗")], "x"))
    db.execute("UPDATE messages SET api_content = '旧的发送字节' WHERE id = ?", (rid,))

    apply_rewrites(db)

    assert db.execute("SELECT api_content FROM messages WHERE id = ?", (rid,)).fetchone()["api_content"] is None


def test_every_unique_message_key_survives_apply(db):
    """自证不变量:改写前窗口里出现过的每个 [m:<id>],改写后仍能在本 session 找到。"""
    win: list[str] = []
    for i in range(1, 8):
        win.append(_line(i, f"第{i}句"))
        _add(db, _reactive(list(win), f"[user=阿甲 id=u1]: 第{i}句"))

    stats = apply_rewrites(db)

    surviving = "\n".join(r["content"] for r in db.execute("SELECT content FROM messages"))
    for i in range(1, 8):
        assert f"[m:{i}]" in surviving
    assert stats.lost_keys == []


def test_apply_refuses_while_fts_rebuild_pending(db):
    """rebuild 半途:落在未迁移区间的行不走触发器,改了就是静默毁索引。"""
    _add(db, _reactive([_line(1, "早")], "x"))
    db.execute("INSERT INTO state_meta (key, value) VALUES ('fts_rebuild_high_water', '999')")

    assert rebuild_in_progress(db) is not None
    with pytest.raises(RuntimeError, match="rebuild"):
        apply_rewrites(db)


def test_fts_index_stays_consistent_after_apply(db):
    win = [_line(1, "早"), _line(2, "在吗")]
    _add(db, _reactive(win, "x"))
    _add(db, _reactive(win + [_line(3, "帮我看下")], "y"))

    apply_rewrites(db)

    db.execute("INSERT INTO messages_fts(messages_fts) VALUES('integrity-check')")
    db.execute("INSERT INTO messages_fts_trigram(messages_fts_trigram) VALUES('integrity-check')")


def test_reclaim_shrinks_logical_size(db):
    win = [_line(i, f"第{i}句话有点长" * 20) for i in range(1, 40)]
    for i in range(40):
        _add(db, _reactive(win, f"[user=阿甲 id=u1]: 第{i}句"))
    db.commit()
    apply_rewrites(db)
    before = logical_size_bytes(db)

    reclaim(db)

    assert logical_size_bytes(db) < before


# ── 抽样展示 ─────────────────────────────────────────────────────────────────


def _window_growing(db, turns: int) -> list[int]:
    """连续 turns 个 turn,窗口每轮新增一行 —— 生产库的形状。"""
    win: list[str] = []
    ids: list[int] = []
    for i in range(1, turns + 1):
        win.append(_line(i, f"第{i}句"))
        ids.append(_add(db, _reactive(list(win), f"[user=阿甲 id=u1]: 第{i}句")))
    return ids


def test_scan_returns_requested_number_of_samples(db):
    _window_growing(db, 6)

    report = scan(db, sample=3)

    assert len(report.samples) == 3


def test_scan_without_sample_returns_no_samples(db):
    _window_growing(db, 6)

    assert scan(db).samples == []


def test_samples_span_first_and_last_changed_rows(db):
    """去重效果随位置差别很大,抽样必须跨到首尾,不能只挑省得最多的。"""
    ids = _window_growing(db, 6)

    report = scan(db, sample=3)

    picked = [s.row_id for s in report.samples]
    assert picked[0] == ids[0]
    assert picked[-1] == ids[-1]
    assert picked == sorted(picked)


def test_sample_render_folds_dropped_window_lines():
    seen = {"m:1", "m:2"}
    old = _reactive([_line(1, "早"), _line(2, "在吗"), _line(3, "帮我看下")], "[user=阿甲 id=u1]: 帮我看下")
    new = rewrite_user_content(old, seen).content

    out = render_sample(old, new)

    assert "2 条" in out
    assert "[m:1]" not in out
    assert "[m:2]" not in out


def test_sample_render_keeps_surviving_window_line():
    seen = {"m:1"}
    old = _reactive([_line(1, "早"), _line(2, "在吗")], "[user=阿甲 id=u1]: 在吗")
    new = rewrite_user_content(old, seen).content

    out = render_sample(old, new)

    assert "[m:2]" in out
    assert "<recent_messages>" in out


def test_sample_render_notes_removed_reminder():
    old = _reactive([_line(1, "早")], "[user=阿甲 id=u1]: 早")
    new = rewrite_user_content(old, set()).content

    out = render_sample(old, new)

    assert "submit_decision" not in out
    assert "协议尾句" in out


def test_sample_render_folds_fully_removed_window():
    """整扇窗口都被删时,折叠标记要在场,标签不该留下。"""
    seen = {"m:1", "m:2"}
    old = _reactive([_line(1, "早"), _line(2, "在吗")], "[user=阿甲 id=u1]: 在吗")
    new = rewrite_user_content(old, seen).content

    out = render_sample(old, new)

    assert "2 条" in out
    assert "<recent_messages>" not in out
    assert "<current_message>" in out


# ── 多行正文:历史行的正文本身可以带换行 ──────────────────────────────────────


def _multiline(mid: int, head: str, *body: str, uid: str = "u1") -> str:
    return f"[m:{mid}] [bot] [user=小助手 id={uid}]: {head}\n" + "\n".join(body)


def test_multiline_body_survives_when_another_message_shares_a_line():
    """两条不同的消息可以有逐字相同的一行(列表项、模板句)。

    按物理行去重会把这一行从后出现的那条消息身上摘走 —— 消息被削,而首行还在,
    所以只查消息主键的不变量看不出来。
    """
    a = _multiline(1, "工具有这些：", "1. push_message", "2. get_recent_messages")
    b = _multiline(2, "重新拉了一下：", "1. push_message", "3. get_message_images")
    seen: set[str] = set()
    first = rewrite_user_content(_reactive([a], "x"), seen)
    seen.update(first.window_keys)

    second = rewrite_user_content(_reactive([a, b], "y"), seen)

    assert "1. push_message" in second.content
    assert "3. get_message_images" in second.content
    assert "[m:1]" not in second.content


def test_blank_lines_inside_body_are_preserved():
    a = _multiline(1, "第一段", "", "第二段")

    r = rewrite_user_content(_reactive([a], "x"), set())

    assert "第一段\n\n第二段" in r.content


def test_repeated_multiline_entry_is_dropped_whole():
    """第二次出现要整条消失,不能只掉首行、留下一堆无主的正文行。"""
    a = _multiline(1, "工具有这些：", "1. push_message", "2. get_recent_messages")
    seen = {"m:1"}

    r = rewrite_user_content(_reactive([a], "x"), seen)

    assert "1. push_message" not in r.content
    assert "工具有这些" not in r.content
    assert "<recent_messages>" not in r.content


def test_window_entries_groups_continuation_lines():
    a = _multiline(1, "第一段", "", "第二段")
    b = _multiline(2, "另一条")

    entries = window_entries(f"{a}\n{b}\n")

    assert [k for k, _body in entries] == ["m:1", "m:2"]
    assert entries[0][1].endswith("第一段\n\n第二段")


def test_window_entries_keys_idless_lines_by_text():
    """id=None 的 transient 消息没有 [m:] 前缀,只能按整条正文当键。"""
    entries = window_entries("[user=阿甲 id=u1]: 没有主键的一行\n")

    assert entries[0][0] == "[user=阿甲 id=u1]: 没有主键的一行"


def test_multiline_bodies_are_byte_identical_after_apply(db):
    a = _multiline(1, "工具有这些：", "1. push_message", "", "2. get_recent_messages")
    b = _multiline(2, "重新拉了一下：", "1. push_message", "", "3. get_message_images")
    _add(db, _reactive([a], "x"))
    _add(db, _reactive([a, b], "y"))
    _add(db, _reactive([a, b], "z"))

    apply_rewrites(db)

    surviving = "\n".join(r["content"] for r in db.execute("SELECT content FROM messages"))
    assert a in surviving
    assert b in surviving


# ── 多 profile:库路径解析 ────────────────────────────────────────────────────


def test_default_db_when_nothing_specified(tmp_path):
    origin, path = resolve_db(home=tmp_path)

    assert path == tmp_path / ".hermes" / "state.db"
    assert "~/.hermes" in origin


def test_hermes_home_is_authoritative_and_never_falls_back(tmp_path):
    """HERMES_HOME 指的库不存在时绝不能回落到默认 profile。

    打错 profile 名、或 profile 还没初始化,都是这个形状;回落就等于把改写落到
    另一份库上,而那份库看起来一切正常。
    """
    (tmp_path / ".hermes").mkdir()
    (tmp_path / ".hermes" / "state.db").touch()
    team = tmp_path / ".hermes" / "profiles" / "team"
    team.mkdir(parents=True)

    origin, path = resolve_db(env=str(team), home=tmp_path)

    assert path == team / "state.db"
    assert "HERMES_HOME" in origin


def test_profile_resolves_under_profiles_root_lowercased(tmp_path):
    _origin, path = resolve_db(profile="Team", home=tmp_path)

    assert path == tmp_path / ".hermes" / "profiles" / "team" / "state.db"


def test_profile_default_maps_to_root_home(tmp_path):
    """上游 get_profile_dir:default 是根 home 本身,不是 profiles/default。"""
    _origin, path = resolve_db(profile="Default", home=tmp_path)

    assert path == tmp_path / ".hermes" / "state.db"


def test_profile_anchors_to_root_when_hermes_home_is_itself_a_profile(tmp_path):
    """已经身处某个 profile 时,--profile 仍锚在根上,不是 profiles/team/profiles/other。"""
    env = tmp_path / ".hermes" / "profiles" / "team"

    _origin, path = resolve_db(profile="other", env=str(env), home=tmp_path)

    assert path == tmp_path / ".hermes" / "profiles" / "other" / "state.db"


def test_profile_anchors_to_grandparent_for_custom_hermes_home(tmp_path):
    """Docker / 自定义部署:HERMES_HOME 在 ~/.hermes 之外,父目录名为 profiles。"""
    env = tmp_path / "opt" / "data" / "profiles" / "team"

    _origin, path = resolve_db(profile="other", env=str(env), home=tmp_path)

    assert path == tmp_path / "opt" / "data" / "profiles" / "other" / "state.db"


def test_custom_hermes_home_outside_dot_hermes_is_its_own_root(tmp_path):
    env = tmp_path / "opt" / "data"

    _origin, path = resolve_db(profile="team", env=str(env), home=tmp_path)

    assert path == tmp_path / "opt" / "data" / "profiles" / "team" / "state.db"


def test_db_and_profile_together_raise(tmp_path):
    with pytest.raises(ValueError, match="--db"):
        resolve_db(db="/somewhere/state.db", profile="team", home=tmp_path)


def test_blank_hermes_home_is_treated_as_unset(tmp_path):
    _origin, path = resolve_db(env="   ", home=tmp_path)

    assert path == tmp_path / ".hermes" / "state.db"
