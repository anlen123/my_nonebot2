#!/usr/bin/env python3
"""解开被 compression 血缘歧义卡死的 Hermes 会话。

症状:Hermes 日志刷

    run_agent: Session DB append_message failed: Session 'hermes-…' is closed by
    compression; adopt its live continuation before appending messages
    compression skipped: session=hermes-… was already rotated by another
    compression path, but no unique live child could be adopted

成因:上游自动压缩上下文时会轮换会话 —— 旧 id 置为 end_reason='compression',新建一个
continuation 子会话,新 id 走响应头 X-Hermes-Session-Id 回传。插件 0.4.4 及更早不采纳
这个头,每轮都把会话钉回那个已关闭的父会话:读还能跟随 tip,写全部失败,而且每压缩一次
就再分叉一个兄弟快照。live 子会话超过一个之后,上游 find_live_compression_child() 判定
歧义并 fail-closed,这个会话从此永久写不进去 —— 对话记录冻结在最后一次成功写入,上下文
还会无限膨胀(压缩永远跑不完)。

本脚本只改会话血缘,**不删任何消息行**:
  * 把父会话重新打开(ended_at / end_reason 清空)—— 真正的对话记录一直在它身上
  * 把那些快照子会话标记为 ended('orphan_cleanup')—— 消除歧义,下次压缩就能正常轮换

默认 dry-run,只报告不改动:

    hermes-repair-sessions                 # 只看:哪些会话卡住了、会动哪些行
    hermes-repair-sessions --apply         # 备份后执行修复

前置:插件必须先升级到会采纳 X-Hermes-Session-Id 的版本并重启,否则下一次压缩会
把父会话再次关闭,几轮之内又卡回去。执行前请先停掉 hermes gateway(`systemctl stop
hermes-gateway`),否则拿不到写锁,而且跑着的 agent 可能持有旧的会话状态。

安全阀:某个子会话若带有多个不同时间戳、且比父会话更新,说明它是真的被续写过的
continuation —— 重开父会话会把它甩掉。这种情况直接拒绝处理该会话并报告,交给人判断。

故意不 import nonebot_plugin_hermes:包的 __init__ 里的 require() 在没有 NoneBot
进程时直接抛错(与 hermes_install_skill.py / hermes_purge_media.py 同因)。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# 插件签发的 session key 形如 hermes-{adapter}+{private|group}+{ids}(见 core/session.py)。
# 只认这个前缀:桌面 / CLI / cron 会话有自己的生命周期,不在本脚本职责内。
_PLUGIN_SESSION_PREFIX = "hermes-"

# 与上游 hermes_state._NON_CONTINUATION_CHILD_FILTER_SQL 同义:带 _branched_from /
# _delegate_from 的子会话是分支与子代理,source='tool' 是工具会话,三者都不是
# compression continuation,既不算进歧义也不该被退休。
_CONTINUATION_CHILD_SQL = """
      AND COALESCE(json_extract(COALESCE(model_config, '{}'), '$._branched_from'), '') = ''
      AND COALESCE(json_extract(COALESCE(model_config, '{}'), '$._delegate_from'), '') = ''
      AND COALESCE(source, '') != 'tool'
"""

_RETIRED_REASON = "orphan_cleanup"

# 快照 vs 真会话的判据是**消息时间跨度**:一次压缩轮换把整段对话批量写进新会话,
# 几百行首尾也只差几秒;被真正续写过的会话则跨小时到跨天。线上实测两类相差六个
# 数量级(子会话 0.4~3.2 秒 vs 父会话 72~89 天),阈值取哪都行,取 60 秒留足余量。
_SNAPSHOT_MAX_SPAN_S = 60.0


@dataclass
class Candidate:
    """一个被压缩关闭、且有多个 live 子会话(= 上游无法自愈)的父会话。"""

    parent_id: str
    live_children: list[str] = field(default_factory=list)
    parent_messages: int = 0
    parent_last_ts: float | None = None
    blocked_reason: str | None = None


def _msg_stats(conn: sqlite3.Connection, session_id: str) -> tuple[int, float, float | None]:
    """返回 (消息数, 时间跨度秒, 最后一条时间戳)。"""
    row = conn.execute(
        "SELECT COUNT(*) n, MAX(timestamp) last, MIN(timestamp) first FROM messages WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    count, last, first = row[0], row[1], row[2]
    span = (last - first) if (last is not None and first is not None) else 0.0
    return count, span, last


def scan(conn: sqlite3.Connection) -> list[Candidate]:
    """列出确实卡死的会话。恰好一个 live 子会话的不算 —— 上游自己能 adopt。"""
    out: list[Candidate] = []
    parents = conn.execute(
        "SELECT id FROM sessions WHERE end_reason = 'compression' AND ended_at IS NOT NULL AND id LIKE ? ORDER BY id",
        (_PLUGIN_SESSION_PREFIX + "%",),
    ).fetchall()

    for parent in parents:
        parent_id = parent[0]
        kids = conn.execute(
            "SELECT id FROM sessions WHERE parent_session_id = ? AND ended_at IS NULL"
            + _CONTINUATION_CHILD_SQL
            + " ORDER BY started_at",
            (parent_id,),
        ).fetchall()
        if len(kids) < 2:
            continue

        p_count, _p_span, p_last = _msg_stats(conn, parent_id)
        cand = Candidate(
            parent_id=parent_id,
            live_children=[k[0] for k in kids],
            parent_messages=p_count,
            parent_last_ts=p_last,
        )
        for kid_id in cand.live_children:
            _k_count, k_span, k_last = _msg_stats(conn, kid_id)
            if k_span > _SNAPSHOT_MAX_SPAN_S and k_last is not None and (p_last is None or k_last > p_last):
                cand.blocked_reason = (
                    f"子会话 {kid_id} 的消息跨越 {k_span / 3600:.1f} 小时且比父会话更新,"
                    "像是被真正续写过的 continuation 而非一次性快照;"
                    "重开父会话会把它甩掉,已跳过"
                )
                break
        out.append(cand)
    return out


def repair(conn: sqlite3.Connection, candidates: list[Candidate], *, now: float) -> tuple[int, int]:
    """执行修复,返回 (重开的父会话数, 退休的子会话数)。blocked 的一律跳过。"""
    reopened = 0
    retired = 0
    for cand in candidates:
        if cand.blocked_reason is not None:
            continue
        conn.execute(
            "UPDATE sessions SET ended_at = NULL, end_reason = NULL WHERE id = ?",
            (cand.parent_id,),
        )
        cur = conn.execute(
            "UPDATE sessions SET ended_at = ?, end_reason = ? WHERE parent_session_id = ? AND ended_at IS NULL"
            + _CONTINUATION_CHILD_SQL,
            (now, _RETIRED_REASON, cand.parent_id),
        )
        reopened += 1
        retired += cur.rowcount
    return reopened, retired


def _candidate_dbs() -> list[tuple[str, Path]]:
    """按优先级列出可能的 state.db,返回 [(来源说明, 路径)]。"""
    out: list[tuple[str, Path]] = []
    home = os.environ.get("HERMES_HOME")
    if home:
        out.append(("HERMES_HOME", Path(home) / "state.db"))
    out.append(("~/.hermes 默认位置", Path.home() / ".hermes" / "state.db"))
    return out


def _default_db() -> Path | None:
    for origin, path in _candidate_dbs():
        if path.exists():
            print(f"[repair] 自动定位到 state.db({origin}): {path}")
            return path
    print("[repair] 自动定位失败,试过:", file=sys.stderr)
    for origin, path in _candidate_dbs():
        print(f"[repair]   [{origin}] {path}", file=sys.stderr)
    return None


def _fmt_ts(ts: float | None) -> str:
    if not ts:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _backup(db_path: Path) -> Path:
    """整库复制一份。改的是血缘元数据,错了没法靠反向 UPDATE 还原(旧 ended_at 已丢)。"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = Path(f"{db_path}.bak-{stamp}")
    shutil.copy2(db_path, dest)
    for suffix in ("-wal", "-shm"):
        side = Path(str(db_path) + suffix)
        if side.exists():
            shutil.copy2(side, Path(str(dest) + suffix))
    return dest


def _report(conn: sqlite3.Connection, candidates: list[Candidate]) -> None:
    for cand in candidates:
        print(f"[repair] 父会话 {cand.parent_id}")
        print(
            f"[repair]   消息 {cand.parent_messages} 条,最后一条 {_fmt_ts(cand.parent_last_ts)};"
            f" live 子会话 {len(cand.live_children)} 个"
        )
        for kid_id in cand.live_children[:3]:
            n, span, last = _msg_stats(conn, kid_id)
            print(f"[repair]     - {kid_id}: {n} 条,跨度 {span:.1f}s,最后 {_fmt_ts(last)}")
        if len(cand.live_children) > 3:
            print(f"[repair]     … 另有 {len(cand.live_children) - 3} 个")
        if cand.blocked_reason:
            print(f"[repair]   ⚠️ 跳过:{cand.blocked_reason}")
        else:
            print(f"[repair]   → 重开父会话,退休 {len(cand.live_children)} 个快照子会话(不删消息)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=None, help="Hermes state.db 路径(默认取 HERMES_HOME / ~/.hermes)")
    parser.add_argument("--apply", action="store_true", help="真的写回;缺省只报告")
    parser.add_argument("--no-backup", action="store_true", help="跳过整库备份(不建议)")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else _default_db()
    if db_path is None:
        print("[repair] 找不到 state.db,请用 --db 指定路径", file=sys.stderr)
        return 2
    if not db_path.exists():
        print(f"[repair] 文件不存在: {db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        candidates = scan(conn)
        print(f"[repair] {db_path}")
        if not candidates:
            print("[repair] 没有发现血缘歧义卡死的会话")
            return 0

        actionable = [c for c in candidates if c.blocked_reason is None]
        print(f"[repair] 发现 {len(candidates)} 个卡死会话,其中 {len(actionable)} 个可自动修复")
        _report(conn, candidates)

        if not args.apply:
            print("[repair] dry-run,未改动任何数据;确认无误后加 --apply")
            return 0
        if not actionable:
            print("[repair] 没有可自动修复的会话,未改动任何数据")
            return 1

        if not args.no_backup:
            dest = _backup(db_path)
            print(f"[repair] 已备份到 {dest}")

        with conn:
            reopened, retired = repair(conn, candidates, now=time.time())
        print(f"[repair] 完成:重开父会话 {reopened} 个,退休快照子会话 {retired} 个,消息行未删除")
        print("[repair] 记得重启 hermes gateway,并确认插件已升级到会采纳 X-Hermes-Session-Id 的版本")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
