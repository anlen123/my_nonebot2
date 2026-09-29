#!/usr/bin/env python3
"""把历史 turn 里重复注入的 prompt 脚手架清出 Hermes 的 state.db,并回收空间。

症状:state.db 体积与群聊活跃度成倍数关系增长,user 侧消息行远比 assistant 侧长
(实测量级 7~8 倍),而且每轮回放的 uncached input 居高不下。

成因:reactive 与 passive 两路都把 `<recent_messages>` 窗口裹进 user message。窗口有
几十行,每个 turn 只新增一两行 —— 同一条群发言于是在 transcript 里被存了几十遍。
Hermes 每轮回放整条 transcript,所以这份重复既是磁盘也是账单。

本脚本把窗口回溯成增量形状:每条群发言只保留第一次出现,往后的重复删掉;顺带删掉
每行都带一份的静态协议尾句(system 端的 decision_protocol 已经写着同一件事)。
`<runtime_state>` 与 `<current_message>` 是本 turn 的实况、不是重复注入,原样保留。

去重单位是**消息**,不是物理行。一条历史行的正文本身可以带换行(bot 发的列表、多段
回复),按物理行去重会把两条消息共有的某一行从后出现的那条身上摘走 —— 消息被削,而
首行还在。键用 `[m:<id>]` 主键而非整行字符串:同一条消息在不同 turn 里可能渲染出
不同的昵称,按字符串比会把它当成两条不同的发言留下来。

默认 dry-run,只报告不改动:

    hermes-optimize-state                    # 只看:能省多少、会动哪些行
    hermes-optimize-state --sample           # 再看 3 条改写前后的对照
    hermes-optimize-state --apply            # 备份后改写 + 回收空间

多 profile 部署下每个 profile 是一份完整的 HERMES_HOME,库要逐个跑:

    hermes-optimize-state --profile team     # ~/.hermes/profiles/team/state.db
    HERMES_HOME=/opt/data hermes-optimize-state
    hermes-optimize-state --db /path/to/state.db

命中的来源就是最终答案,**库不存在时直接报错、绝不回落到别的 profile** —— profile 名打错
或 profile 还没初始化时回落,会把改写静默落到默认 profile 那份库上。

前置:先停掉 hermes gateway(`systemctl stop hermes-gateway`)。拿不到写锁会直接退出,
而且跑着的 agent 手上可能有已装载的旧 transcript。

三条安全边界:
  * 只动 `sessions.source='api_server'` 且 role='user' 的行 —— CLI / TUI / cron 会话
    不是插件流量,一行不碰;assistant 侧是模型自己的输出,也不碰。
  * FTS 索引由触发器跟随 UPDATE 维护,但触发器的 WHEN 子句挂在 rebuild 水位标记上:
    rebuild 半途时落在未迁移区间的行不会同步,改了就是静默毁掉搜索索引。发现标记
    在场即拒绝执行,让 `hermes sessions optimize-storage` 先跑完。
  * 改写前记下每条消息的主键**与正文摘要**,改写后逐一回查:少一条、或留下来的正文
    不是原文里出现过的任一份,就整体回滚。只比对主键是不够的 —— 正文被削掉几行的
    消息,首行还在,主键查得到。这个自证不变量是本脚本敢改历史内容的唯一依据。

代价:被改写的 session 下一轮是一次完整的 prompt cache miss,之后按新的、更小的前缀
重新建缓存。

故意不 import nonebot_plugin_hermes:包的 __init__ 里的 require() 在没有 NoneBot
进程时直接抛错(与 hermes_install_skill.py / hermes_repair_sessions.py 同因)。
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import os
import re
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# 与 core/prompt_builder.py 的 _render_recent_messages_block 同形。空窗口渲染成
# `<recent_messages>\n</recent_messages>`(中间没有内容行),所以不能要求闭标签前必有换行。
_RE_WINDOW = re.compile(r"<recent_messages>\n(.*?)</recent_messages>(\n*)", re.DOTALL)

# 协议尾句由 build_reactive_user_content 追加在 content 末尾。字段列表随版本变过,
# 所以只锚定开头那句、一路吃到结尾,不去枚举字段名。
_RE_REMINDER = re.compile(r"\n*请用 submit_decision JSON 对象回复\(.*\Z", re.DOTALL)

# 历史行的 `[m:<id>] ` 前缀是 DB 主键,跨 turn 稳定(id=None 的 transient 消息没有前缀)。
_RE_MSG_KEY = re.compile(r"^\[m:([^\]]+)\] ")

# 一条历史行的开头:有主键前缀,或(id=None 时)直接是 speaker 标签。正文的续行两者都不是。
_RE_ENTRY_HEAD = re.compile(r"^(?:\[m:[^\]]+\] )?(?:\[bot\] )?\[user=")

# 插件流量的口径:上游 api_server 适配器写入的会话。
_PLUGIN_SOURCE = "api_server"

# 两条 rebuild 水位标记(基础索引与 CJK 索引各一对)。存在即说明 rebuild 未收尾。
_REBUILD_MARKERS = ("fts_rebuild_high_water", "fts_cjk_rebuild_high_water")

_FTS_TABLES = ("messages_fts", "messages_fts_trigram", "messages_fts_cjk")


@contextlib.contextmanager
def _manual_transactions(conn: sqlite3.Connection):
    """临时接管事务控制。

    sqlite3 模块默认在 DML 前自动开一个事务,显式 `BEGIN IMMEDIATE` 会撞上「事务里
    不能再开事务」。BEGIN IMMEDIATE 是必需的:改写要么整体落地要么整体不落地,而
    自证不变量的回查必须看到本次 UPDATE 的结果。
    """
    previous = conn.isolation_level
    if conn.in_transaction:
        conn.commit()
    conn.isolation_level = None
    try:
        yield
    finally:
        conn.isolation_level = previous


def window_entries(inner: str) -> list[tuple[str, str]]:
    """把 `<recent_messages>` 块的内容切成 (去重键, 整条正文) 的序列。

    一条历史行的正文本身可以带换行(bot 发的列表、多段回复),所以「行」不是去重单位 ——
    消息才是。以 `[m:<id>] ` 或裸的 speaker 标签开头的物理行是一条消息的开头,其余物理行
    (含空行)属于上一条。按物理行去重会把两条消息共有的某一行从后出现的那条身上摘走。

    去重键:带主键的用 `m:<id>`(跨 turn 稳定);id=None 的 transient 消息没有前缀,
    只能拿整条正文当键。
    """
    entries: list[tuple[str, str]] = []
    for line in inner.split("\n"):
        if _RE_ENTRY_HEAD.match(line):
            entries.append(("", line))
            continue
        if entries:
            entries[-1] = (entries[-1][0], entries[-1][1] + "\n" + line)
        elif line.strip():
            entries.append(("", line))
    out: list[tuple[str, str]] = []
    for _key, body in entries:
        body = body.rstrip("\n")
        if not body.strip():
            continue
        m = _RE_MSG_KEY.match(body)
        out.append((f"m:{m.group(1)}" if m else body, body))
    return out


@dataclass
class Rewrite:
    content: str
    window_keys: list[str]
    changed: bool


def rewrite_user_content(content: str, seen: set[str]) -> Rewrite:
    """把一条 user content 里已出现过的窗口行删掉,并摘掉静态协议尾句。

    ``seen`` 是本 session 到这一行之前已经出现过的消息主键集合,只读;调用方负责用
    返回的 ``window_keys`` 更新它。窗口内没有任何新行时,整块连标签一起删除 —— 留个
    空块只是换一种形式的浪费。
    """
    keys: list[str] = []
    emitted: set[str] = set()

    def _sub(m: re.Match[str]) -> str:
        inner, tail = m.group(1), m.group(2)
        fresh: list[str] = []
        for key, body in window_entries(inner):
            keys.append(key)
            if key in seen or key in emitted:
                continue
            emitted.add(key)
            fresh.append(body)
        if not fresh:
            return ""
        return "<recent_messages>\n" + "\n".join(fresh) + "\n</recent_messages>" + tail

    new = _RE_WINDOW.sub(_sub, content)
    new = _RE_REMINDER.sub("", new)
    return Rewrite(content=new, window_keys=keys, changed=new != content)


@dataclass
class _Plan:
    rows: list[tuple[int, str, str, str]] = field(default_factory=list)  # (id, session_id, old, new)
    bytes_before: int = 0
    bytes_after: int = 0
    session_entries: dict[str, dict[str, set[bytes]]] = field(default_factory=dict)
    sessions: set[str] = field(default_factory=set)


@dataclass
class Sample:
    """一条改写前后的对照样本。"""

    session_id: str
    row_id: int
    old: str
    new: str

    @property
    def before_bytes(self) -> int:
        return len(self.old.encode("utf-8"))

    @property
    def after_bytes(self) -> int:
        return len(self.new.encode("utf-8"))


def _pick_samples(rows: list[tuple[int, str, str, str]], count: int) -> list[Sample]:
    """在改写行最多的那个 session 里,按位置均匀取样。

    刻意不挑「省得最多」的行:去重效果随位置差别极大 —— 冷启动那条几乎整扇窗口都留着,
    中段每轮只留一两行。只展示省得最多的行会把效果说得过好。
    """
    if count <= 0 or not rows:
        return []
    by_session: dict[str, list[tuple[int, str, str, str]]] = {}
    for row in rows:
        by_session.setdefault(row[1], []).append(row)
    pool = max(by_session.values(), key=len)
    if count >= len(pool):
        picked = pool
    elif count == 1:
        picked = [pool[0]]
    else:
        step = (len(pool) - 1) / (count - 1)
        picked = [pool[round(i * step)] for i in range(count)]
    return [Sample(session_id=sid, row_id=rid, old=old, new=new) for rid, sid, old, new in picked]


def render_sample(old: str, new: str) -> str:
    """把一条改写前后的对照折叠成可读的一块。

    展示的是**改写后**的整行(模型下一轮实际会读到的东西),被删掉的重复消息折成一行计数。
    逐条列出被删的消息没有意义:它们按构造就是在别处出现过的重复品。
    """
    kept = [body for m in _RE_WINDOW.finditer(new) for _key, body in window_entries(m.group(1))]

    def _fold(m: re.Match[str]) -> str:
        dropped = len(window_entries(m.group(1))) - len(kept)
        parts: list[str] = []
        if dropped > 0:
            parts.append(f"… {dropped} 条重复窗口已删(均已在前面的 turn 出现过)…")
        if kept:
            parts.append("<recent_messages>\n" + "\n".join(kept) + "\n</recent_messages>")
        return "\n".join(parts) + m.group(2)

    body = _RE_WINDOW.sub(_fold, old, count=1)
    reminder = _RE_REMINDER.search(old)
    if reminder is not None and _RE_REMINDER.search(new) is None:
        size = len(reminder.group(0).strip().encode("utf-8"))
        body = _RE_REMINDER.sub(f"\n\n… 协议尾句已删(静态样板,{size} B)…", body)
    return body


@dataclass
class ScanReport:
    rows_scanned: int
    rows_to_rewrite: int
    bytes_before: int
    bytes_after: int
    sessions: int
    samples: list[Sample] = field(default_factory=list)

    @property
    def bytes_saved(self) -> int:
        return self.bytes_before - self.bytes_after


@dataclass
class ApplyStats:
    rows_rewritten: int
    bytes_before: int
    bytes_after: int
    sessions: int
    lost_keys: list[str]


def rebuild_in_progress(conn: sqlite3.Connection) -> str | None:
    """rebuild 水位标记在场时返回标记名,否则 None。"""
    try:
        rows = conn.execute(
            f"SELECT key FROM state_meta WHERE key IN ({','.join('?' * len(_REBUILD_MARKERS))})",
            _REBUILD_MARKERS,
        ).fetchall()
    except sqlite3.OperationalError:  # 没有 state_meta 表 —— 旧库,谈不上 rebuild
        return None
    return rows[0][0] if rows else None


def _build_plan(conn: sqlite3.Connection) -> _Plan:
    """按 session 顺序走一遍候选行,算出每行改写后的内容。

    必须按 (session_id, id) 排序:去重是「此前是否出现过」,顺序错了就会把先出现的
    那次当成重复删掉。
    """
    plan = _Plan()
    seen_by_session: dict[str, set[str]] = {}
    cur = conn.execute(
        "SELECT m.id, m.session_id, m.content FROM messages m "
        "JOIN sessions s ON s.id = m.session_id "
        "WHERE s.source = ? AND m.role = 'user' AND m.content LIKE '%<recent_messages>%' "
        "ORDER BY m.session_id, m.id",
        (_PLUGIN_SOURCE,),
    )
    for row in cur:
        rid, sid, content = int(row[0]), str(row[1]), row[2] or ""
        seen = seen_by_session.setdefault(sid, set())
        r = rewrite_user_content(content, seen)
        seen.update(r.window_keys)
        _merge_digests(plan.session_entries.setdefault(sid, {}), _entry_digests(content))
        plan.sessions.add(sid)
        plan.bytes_before += len(content.encode("utf-8"))
        plan.bytes_after += len(r.content.encode("utf-8"))
        if r.changed:
            plan.rows.append((rid, sid, content, r.content))
    return plan


def scan(conn: sqlite3.Connection, *, sample: int = 0) -> ScanReport:
    """只读:算出能省多少、会动多少行。``sample`` > 0 时附带若干条改写前后的对照样本。"""
    plan = _build_plan(conn)
    scanned = int(
        conn.execute(
            "SELECT COUNT(*) FROM messages m JOIN sessions s ON s.id = m.session_id "
            "WHERE s.source = ? AND m.role = 'user' AND m.content LIKE '%<recent_messages>%'",
            (_PLUGIN_SOURCE,),
        ).fetchone()[0]
    )
    return ScanReport(
        rows_scanned=scanned,
        rows_to_rewrite=len(plan.rows),
        bytes_before=plan.bytes_before,
        bytes_after=plan.bytes_after,
        sessions=len(plan.sessions),
        samples=_pick_samples(plan.rows, sample),
    )


def _digest(body: str) -> bytes:
    return hashlib.blake2b(body.encode("utf-8"), digest_size=16).digest()


def _entry_digests(text: str) -> dict[str, set[bytes]]:
    """键 → 该键出现过的正文摘要集合。"""
    out: dict[str, set[bytes]] = {}
    for m in _RE_WINDOW.finditer(text):
        for key, body in window_entries(m.group(1)):
            out.setdefault(key, set()).add(_digest(body))
    return out


def _merge_digests(into: dict[str, set[bytes]], other: dict[str, set[bytes]]) -> None:
    for key, digests in other.items():
        into.setdefault(key, set()).update(digests)


def _session_digests(conn: sqlite3.Connection, session_id: str) -> dict[str, set[bytes]]:
    out: dict[str, set[bytes]] = {}
    for (content,) in conn.execute(
        "SELECT content FROM messages WHERE session_id = ? AND content IS NOT NULL",
        (session_id,),
    ):
        _merge_digests(out, _entry_digests(content))
    return out


def apply_rewrites(conn: sqlite3.Connection) -> ApplyStats:
    """改写候选行。任何一条消息在改写后找不回来、或正文与原文不符,就整体回滚。

    `api_content` 一并置 NULL:它是 content 的**替代品**而不是补充,改了 content 却
    留着旧 sidecar,回放读到的还是旧那份。
    """
    marker = rebuild_in_progress(conn)
    if marker is not None:
        raise RuntimeError(
            f"FTS rebuild 未收尾(state_meta.{marker} 在场):此时改写 messages 会让"
            "未迁移区间的行绕过触发器、静默毁掉搜索索引。请先跑完 "
            "`hermes sessions optimize-storage`"
        )

    plan = _build_plan(conn)
    if not plan.rows:
        return ApplyStats(0, plan.bytes_before, plan.bytes_after, len(plan.sessions), [])

    with _manual_transactions(conn):
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.executemany(
                "UPDATE messages SET content = ?, api_content = NULL WHERE id = ?",
                [(new, rid) for rid, _sid, _old, new in plan.rows],
            )
            lost: list[str] = []
            mangled: list[str] = []
            for sid, expected in plan.session_entries.items():
                surviving = _session_digests(conn, sid)
                lost.extend(sorted(set(expected) - set(surviving)))
                # 正文摘要必须是该键原有的某一份。少一条正文行、多一个字节都会落在这里 ——
                # 只比对主键的话,被削掉正文的消息因为首行还在而查不出来。
                mangled.extend(sorted(k for k, d in surviving.items() if not d <= expected.get(k, set())))
            if lost or mangled:
                conn.execute("ROLLBACK")
                raise RuntimeError(
                    f"自证不变量失败:{len(lost)} 个消息在改写后找不回来(如 {lost[:5]}),"
                    f"{len(mangled)} 个消息的正文与原文不符(如 {mangled[:5]}),已回滚。"
                    "请带上这份输出报 issue"
                )
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise

    return ApplyStats(
        rows_rewritten=len(plan.rows),
        bytes_before=plan.bytes_before,
        bytes_after=plan.bytes_after,
        sessions=len(plan.sessions),
        lost_keys=[],
    )


def logical_size_bytes(conn: sqlite3.Connection) -> int:
    """SQLite 自己记的库大小 —— WAL checkpoint 回主文件后它就是文件大小。

    报 VACUUM 收益只能看这个:WAL 模式下 VACUUM 的重写先落在 -wal 里,主文件的
    stat() 要等 checkpoint,期间 before/after 的差值会偏小甚至为负。
    """
    page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
    page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
    return page_count * page_size


def reclaim(conn: sqlite3.Connection, *, vacuum: bool = True) -> int:
    """合并 FTS 段、checkpoint WAL、VACUUM。返回合并过的 FTS 索引数。

    顺序有意义:先合并段,腾出的页才能被随后的 VACUUM 一起还给操作系统。
    """
    present = {
        r[0]
        for r in conn.execute(
            f"SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ({','.join('?' * len(_FTS_TABLES))})",
            _FTS_TABLES,
        )
    }
    optimized = 0
    for table in _FTS_TABLES:
        if table not in present:
            continue
        try:
            conn.execute(f"INSERT INTO {table}({table}) VALUES('optimize')")
            optimized += 1
        except sqlite3.OperationalError:
            pass
    conn.commit()
    with _manual_transactions(conn):
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.OperationalError:
            pass
        if vacuum:
            conn.execute("VACUUM")
    return optimized


def _hermes_root(env: str | None, home: Path) -> Path:
    """profiles 的锚点,与上游 `get_default_hermes_root` 同规则。

    profile 目录挂在**根** home 下,而不是当前 `HERMES_HOME` 下 —— 否则身处 team
    profile 时 `--profile other` 会去找 `profiles/team/profiles/other`。
    """
    native = home / ".hermes"
    if not env or not env.strip():
        return native
    path = Path(env).expanduser()
    try:
        path.relative_to(native)  # HERMES_HOME 在 ~/.hermes 之下(普通模式或 profile 模式)
        return native
    except ValueError:
        pass
    # Docker / 自定义部署:父目录名为 profiles 时,根是祖父目录
    if path.parent.name == "profiles":
        return path.parent.parent
    return path


def resolve_db(
    *,
    db: str | None = None,
    profile: str | None = None,
    env: str | None = None,
    home: Path | None = None,
) -> tuple[str, Path]:
    """决定这一次动哪份 state.db,返回 (来源说明, 路径)。

    优先级:`--db` > `--profile` > `HERMES_HOME` > `~/.hermes`。

    **命中的来源就是最终答案,不存在也不回落。** 多 profile 部署下每个 profile 是一份
    完整的 HERMES_HOME,回落意味着:profile 名打错、或 profile 还没初始化时,改写会静默
    落到默认 profile 那份库上 —— 而那份库看起来一切正常,事后无从分辨。
    """
    home = home or Path.home()
    if db and profile:
        raise ValueError("--db 与 --profile 只能给一个:前者是完整路径,后者按 profile 名推导")
    if db:
        return ("--db", Path(db).expanduser())
    if profile:
        canon = profile.strip().lower()
        if not canon:
            raise ValueError("--profile 不能为空")
        root = _hermes_root(env, home)
        # 上游 get_profile_dir:default 是根 home 本身,不是 profiles/default
        target = root if canon == "default" else root / "profiles" / canon
        return (f"--profile {canon}", target / "state.db")
    if env and env.strip():
        return ("HERMES_HOME", Path(env.strip()).expanduser() / "state.db")
    return ("~/.hermes 默认位置", home / ".hermes" / "state.db")


def _backup(db_path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = db_path.with_name(f"{db_path.name}.bak-{stamp}")
    shutil.copy2(db_path, dest)
    for suffix in ("-wal", "-shm"):
        side = db_path.with_name(db_path.name + suffix)
        if side.exists():
            shutil.copy2(side, dest.with_name(dest.name + suffix))
    return dest


def _fmt_bytes(n: int) -> str:
    val = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(val) < 1024 or unit == "GiB":
            return f"{val:.1f} {unit}" if unit != "B" else f"{int(val)} B"
        val /= 1024
    return f"{val:.1f} GiB"


def _report(report: ScanReport) -> None:
    print(f"[optimize] 候选行(api_server + user + 带窗口):{report.rows_scanned},跨 {report.sessions} 个会话")
    print(f"[optimize] 需要改写:{report.rows_to_rewrite} 行")
    print(
        f"[optimize] 这些行的内容:{_fmt_bytes(report.bytes_before)} → "
        f"{_fmt_bytes(report.bytes_after)}(省 {_fmt_bytes(report.bytes_saved)})"
    )
    if report.bytes_before:
        print(f"[optimize] 压缩比:{report.bytes_after / report.bytes_before * 100:.1f}%")
    _print_samples(report.samples)


def _print_samples(samples: list[Sample]) -> None:
    """打印抽样对照。展示的是改写后的整行 —— 压缩比回答不了「改完还能不能读」。"""
    if not samples:
        return
    total = len(samples)
    print(f"\n[optimize] 抽样 {total} 条(取自改写行最多的会话,按位置均匀铺开):")
    for i, sample in enumerate(samples, start=1):
        pct = sample.after_bytes / sample.before_bytes * 100 if sample.before_bytes else 0.0
        print(f"\n── 样本 {i}/{total}  session={sample.session_id}  row={sample.row_id} ──")
        print(f"{sample.before_bytes:,} B → {sample.after_bytes:,} B  ({pct:.1f}%)\n")
        for line in render_sample(sample.old, sample.new).split("\n"):
            print(f"  {line}")
    print()


def _check_writable(conn: sqlite3.Connection) -> None:
    """拿不到写锁就别往下走:gateway 还在跑的话它手上有已装载的旧 transcript。"""
    try:
        with _manual_transactions(conn):
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("ROLLBACK")
    except sqlite3.OperationalError as exc:
        raise RuntimeError(f"拿不到写锁({exc})—— 请先停掉 hermes gateway(systemctl stop hermes-gateway)") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=None, help="state.db 完整路径,盖过下面所有推导")
    parser.add_argument(
        "--profile",
        default=None,
        metavar="NAME",
        help="按 profile 名定位(~/.hermes/profiles/<name>/state.db;default 指根 home)",
    )
    parser.add_argument("--apply", action="store_true", help="真的写回;缺省只报告")
    parser.add_argument("--no-backup", action="store_true", help="跳过整库备份(不建议)")
    parser.add_argument("--no-vacuum", action="store_true", help="跳过 VACUUM(不回收空间,磁盘不够时用)")
    parser.add_argument(
        "--sample",
        nargs="?",
        type=int,
        const=3,
        default=0,
        metavar="N",
        help="额外打印 N 条改写前后的对照样本(不带数字时为 3),看清改完的行还能不能读",
    )
    args = parser.parse_args()

    try:
        origin, db_path = resolve_db(db=args.db, profile=args.profile, env=os.environ.get("HERMES_HOME"))
    except ValueError as exc:
        print(f"[optimize] {exc}", file=sys.stderr)
        return 1
    if not db_path.exists():
        # 刻意不回落到默认 profile:见 resolve_db 的说明
        print(f"[optimize] [{origin}] 下没有 state.db:{db_path}", file=sys.stderr)
        print("[optimize] 不会改用别的库 —— 请确认 profile 名,或用 --db 指定路径", file=sys.stderr)
        return 1

    print(f"[optimize] 库([{origin}]):{db_path}")
    conn = sqlite3.connect(db_path)
    try:
        marker = rebuild_in_progress(conn)
        if marker is not None:
            print(
                f"[optimize] FTS rebuild 未收尾(state_meta.{marker} 在场)。此时改写会绕过"
                "触发器、静默毁掉搜索索引 —— 请先跑 `hermes sessions optimize-storage`",
                file=sys.stderr,
            )
            return 2

        before_logical = logical_size_bytes(conn)
        print(f"[optimize] 当前逻辑大小:{_fmt_bytes(before_logical)}")
        _report(scan(conn, sample=args.sample))

        if not args.apply:
            print("[optimize] dry-run,未改动任何数据;确认无误后加 --apply")
            return 0

        _check_writable(conn)
        if not args.no_backup:
            print(f"[optimize] 备份:{_backup(db_path)}")

        stats = apply_rewrites(conn)
        print(f"[optimize] 已改写 {stats.rows_rewritten} 行;消息零丢失、留存正文与原文逐字一致")

        optimized = reclaim(conn, vacuum=not args.no_vacuum)
        print(f"[optimize] 合并 {optimized} 个 FTS 索引" + ("" if args.no_vacuum else " + VACUUM"))

        for stmt in ("PRAGMA integrity_check",):
            result = conn.execute(stmt).fetchone()[0]
            if result != "ok":
                print(f"[optimize] {stmt} 失败:{result}", file=sys.stderr)
                return 3
        for table in _FTS_TABLES:
            try:
                conn.execute(f"INSERT INTO {table}({table}) VALUES('integrity-check')")
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc):
                    print(f"[optimize] {table} 索引自检失败:{exc}", file=sys.stderr)
                    return 3
        print("[optimize] integrity_check + FTS 索引自检通过")

        after_logical = logical_size_bytes(conn)
        print(
            f"[optimize] 逻辑大小:{_fmt_bytes(before_logical)} → {_fmt_bytes(after_logical)}"
            f"(省 {_fmt_bytes(before_logical - after_logical)})"
        )
        if not args.no_vacuum:
            print("[optimize] 主文件的 stat() 可能还没跟上 —— WAL checkpoint 回主文件后一致")
        print("[optimize] 注意:被改写的会话下一轮是一次完整的 prompt cache miss")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
