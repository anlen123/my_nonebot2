#!/usr/bin/env python3
"""把消息库里内联的 base64 data URL 原地换成 [图片] 占位。

用途:早期版本的 reactive 路径把 bot 回复原文写进 messages.content,而 api_server 会在
生成之后把 MEDIA: 图片内联成 data:image/…;base64,… —— 于是 agent 每在群里发一次图,
库里就多一条 MB 级的行。写入端与渲染端现在都会挡住,本工具只清存量字节。

不删行:那会连带丢掉同一条消息里的正常文本,以及 bot 自己那条历史(decision_protocol
的「自我归因校验」要靠它定位自己说过什么)。只替换 payload,长度之外的语义不变。

默认 dry-run,只报告不改动;--db 不给时自动探测(见 _candidate_dbs)。

    hermes-purge-media                    # 只看:每群命中数、最大行、可回收字节
    hermes-purge-media --apply            # 清内容
    hermes-purge-media --vacuum           # 只收缩文件
    hermes-purge-media --apply --vacuum   # 一步到位

--apply 只改内容,不会让文件变小(SQLite 把腾出的页留着复用);要磁盘立刻降下来加
--vacuum,它与 --apply 相互独立,内容已经干净时也能单独跑。

故意不 import nonebot_plugin_hermes:`-m` / 直接 import 都会连带执行包的 __init__,
里面的 require() 在没有 NoneBot 进程时直接抛错(与 hermes_install_skill.py 同因)。
因此这里自带一份与 prompt_builder 同义的正则(见下方常量),改一处记得同步。
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path

# 与 core/prompt_builder 的 _MD_DATA_IMAGE_IN_HISTORY_RE / _BARE_DATA_URL_IN_HISTORY_RE
# 保持同义:先连 markdown 壳一起换,再兜裸 data URL。
_MD_DATA_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*data:[\w.+-]+/[\w.+-]+;base64,[A-Za-z0-9+/=\s]*\)?")
_BARE_DATA_URL_RE = re.compile(r"data:[\w.+-]+/[\w.+-]+;base64,[A-Za-z0-9+/=\s]+")
_PLACEHOLDER = "[图片]"


def _repl(m: re.Match[str]) -> str:
    """换成 [图片],但把匹配尾部的空白还回去 —— 字符类含 \\s(base64 可能折行),
    否则会顺手吃掉 payload 后面那个空格。"""
    matched = m.group(0)
    return _PLACEHOLDER + matched[len(matched.rstrip()) :]


def _strip_inline_media(content: str) -> str:
    return _BARE_DATA_URL_RE.sub(_repl, _MD_DATA_IMAGE_RE.sub(_repl, content))


# 只有内联 payload 才会把单条撑到这个量级;低于此值的长消息是正常的长回复,不碰。
_REPORT_THRESHOLD = 2000


_PLUGIN_NAME = "nonebot_plugin_hermes"


def _candidate_dbs() -> list[tuple[str, Path]]:
    """按优先级列出可能的 messages.db,返回 [(来源说明, 路径)]。

    覆盖插件实际用到的三条来源(见 mcp/__init__.py 的 _default_db_path):
      1. HERMES_STORAGE_DB_PATH — 插件配置直接指定
      2. localstore 的目录覆写 — LOCALSTORE_PLUGIN_DATA_DIR(按插件名的 dict)
         / LOCALSTORE_DATA_DIR(基目录),部署常用它把数据落到项目目录下,
         此时 DB 根本不在 ~/.local/share,不列出来就只能靠人手传 --db
      3. localstore 默认位置
    不 import localstore 解析:它是 nonebot 插件,离开 NoneBot 进程 import 会炸。
    """
    out: list[tuple[str, Path]] = []
    direct = os.environ.get("HERMES_STORAGE_DB_PATH")
    if direct:
        out.append(("HERMES_STORAGE_DB_PATH", Path(direct)))

    # dict 形态,形如 {"nonebot_plugin_hermes": "/some/dir"};只做宽松解析,
    # 取到本插件那一项即可,不引入 json/ast 之外的猜测。
    plugin_dirs = os.environ.get("LOCALSTORE_PLUGIN_DATA_DIR")
    if plugin_dirs and _PLUGIN_NAME in plugin_dirs:
        try:
            import json

            mapping = json.loads(plugin_dirs)
            if isinstance(mapping, dict) and mapping.get(_PLUGIN_NAME):
                out.append(("LOCALSTORE_PLUGIN_DATA_DIR", Path(mapping[_PLUGIN_NAME]) / "messages.db"))
        except (ValueError, TypeError):
            pass

    base = os.environ.get("LOCALSTORE_DATA_DIR")
    if base:
        out.append(("LOCALSTORE_DATA_DIR", Path(base) / _PLUGIN_NAME / "messages.db"))

    out.append(("localstore 默认位置", Path.home() / ".local" / "share" / "nonebot2" / _PLUGIN_NAME / "messages.db"))
    # 从 bot 工作目录跑时的常见相对布局
    for rel in ("localstorage", "data"):
        out.append((f"./{rel}/", Path(rel) / _PLUGIN_NAME / "messages.db"))
    return out


def _default_db() -> Path | None:
    for origin, path in _candidate_dbs():
        if path.exists():
            print(f"[purge] 自动定位到 messages.db({origin}): {path}")
            return path
    print("[purge] 自动定位失败,试过:", file=sys.stderr)
    for origin, path in _candidate_dbs():
        print(f"[purge]   [{origin}] {path}", file=sys.stderr)
    return None


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024.0
    return f"{n}B"


def _footprint(db_path: Path) -> int:
    """主库 + -wal + -shm 的磁盘占用。只看主库会漏掉被 WAL 顶起来的那几 MB。"""
    total = 0
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            total += p.stat().st_size
    return total


def _compact(conn: sqlite3.Connection, db_path: Path) -> None:
    """WAL checkpoint(TRUNCATE) + VACUUM,并报告磁盘占用变化。

    两步都要做:重写 MB 级 blob 会把 -wal 撑到几 MB,只 VACUUM 不 checkpoint 的话
    那部分占用还挂在 -wal 上;反过来只 checkpoint 不 VACUUM,主库里腾出的页仍然
    留在文件里等后续写入复用。
    """
    before = _footprint(db_path)
    # VACUUM 不能在事务里跑;Python sqlite3 默认会为 DML 隐式开事务,这里切自动提交。
    conn.isolation_level = None
    try:
        busy, log_pages, checkpointed = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if busy:
            print(
                f"[purge] WAL checkpoint 未完成(busy=1,还有别的连接在读写);已 checkpoint {checkpointed}/{log_pages} 页"
            )
            print("[purge] 想彻底收回这部分,停掉 bot 后再跑一次 --vacuum")
        else:
            print(f"[purge] WAL 已折回主库并截断({checkpointed} 页)")
    except sqlite3.Error as exc:
        print(f"[purge] WAL checkpoint 失败: {exc}")

    print("[purge] VACUUM 中(需要排它锁,视库大小可能要几十秒)…")
    try:
        conn.execute("VACUUM")
        # VACUUM 在 WAL 模式下是把整个新库写过 -wal 的,跑完那一刻 -wal 正鼓着几 MB。
        # 不再 checkpoint 一次就去量,报出来的数会比 `ls` 看到的大一大截。
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error as exc:
        print(f"[purge] VACUUM 失败: {exc}", file=sys.stderr)
        print("[purge] 通常是 bot 还占着库拿不到排它锁 —— 停掉 bot 后重跑 --vacuum", file=sys.stderr)
        return
    after = _footprint(db_path)
    print(f"[purge] VACUUM 完成:磁盘占用 {_human(before)} → {_human(after)}(含 -wal/-shm)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--db", default=None, help="messages.db 路径(默认取 HERMES_STORAGE_DB_PATH / localstore 常规位置)"
    )
    parser.add_argument("--apply", action="store_true", help="真的写回;缺省只报告")
    parser.add_argument(
        "--vacuum",
        action="store_true",
        help="WAL checkpoint + VACUUM 收缩文件(需要额外磁盘,期间要排它锁)。与 --apply 独立:内容已经干净时单独用它也能收缩",
    )
    parser.add_argument("--limit-preview", type=int, default=10, help="报告里最多列几条(默认 10)")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else _default_db()
    if db_path is None:
        print("[purge] 找不到 messages.db,请用 --db 指定路径", file=sys.stderr)
        return 2
    if not db_path.exists():
        print(f"[purge] 文件不存在: {db_path}", file=sys.stderr)
        return 2

    # timeout:插件是 WAL 模式常驻连接,单写者,拿不到锁时等一会而不是立刻炸。
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        total_rows, total_bytes = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(length(content)), 0) FROM messages"
        ).fetchone()
        print(f"[purge] {db_path}")
        print(f"[purge] 全库 {total_rows} 行,content 合计 {_human(total_bytes)}")

        # 命中判定用 SQL LIKE 粗筛(走不了正则),精确替换在 Python 侧做
        rows = conn.execute(
            "SELECT id, adapter, group_id, is_bot, length(content) AS len, content "
            "FROM messages WHERE content LIKE '%;base64,%' ORDER BY len DESC"
        ).fetchall()
        if not rows:
            print("[purge] 没有内联 base64 的行,内容无需处理")
            if args.vacuum:
                _compact(conn, db_path)
            return 0

        updates: list[tuple[str, int]] = []
        saved = 0
        for r in rows:
            cleaned = _strip_inline_media(r["content"])
            if cleaned == r["content"]:
                continue
            updates.append((cleaned, r["id"]))
            saved += r["len"] - len(cleaned)

        by_group: dict[str, list[sqlite3.Row]] = {}
        for r in rows:
            by_group.setdefault(f"{r['adapter']}/{r['group_id']}", []).append(r)
        print(f"[purge] 命中 {len(updates)} 行,可回收 {_human(saved)}")
        for scope, group_rows in sorted(by_group.items(), key=lambda kv: -sum(x["len"] for x in kv[1])):
            print(f"[purge]   {scope}: {len(group_rows)} 行,最大 {_human(max(x['len'] for x in group_rows))}")
        print(f"[purge] 最长的 {min(args.limit_preview, len(rows))} 条:")
        for r in rows[: args.limit_preview]:
            head = r["content"][:60].replace("\n", "\\n")
            print(f"[purge]   id={r['id']} is_bot={r['is_bot']} len={_human(r['len'])} head={head!r}")

        # 顺带报告:剥完仍然超长的行(真的是长文本,不是图),留给人判断
        still_big = [(cleaned, rid) for cleaned, rid in updates if len(cleaned) > _REPORT_THRESHOLD]
        if still_big:
            print(f"[purge] 注意:{len(still_big)} 行剥掉图片后仍 >{_REPORT_THRESHOLD} 字符(是长文本,本脚本不截断)")

        if args.apply:
            with conn:  # 单事务,失败整体回滚
                conn.executemany("UPDATE messages SET content = ? WHERE id = ?", updates)
            print(f"[purge] 已写回 {len(updates)} 行")
        else:
            print("[purge] dry-run 结束,内容未改动。确认后加 --apply")

        # 物理回收与内容清理解耦:--vacuum 单独判断,内容已经干净时也要能收缩
        if args.vacuum:
            _compact(conn, db_path)
        elif args.apply:
            print("[purge] 未 --vacuum:腾出的页会留在文件里给后续写入复用,文件大小不会立刻变小")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
