"""SQLite 后端的消息日志。

负责:
- 自增 id 分配(BufferedMessage.id 回填)
- 多图存储(message_images 表)
- 按 (adapter, group_id [, user_id]) 查询最近 N 条
- vacuum:按 retention 天数 + 行数硬上限淘汰
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from nonebot import logger

from ..message_buffer import BufferedMessage

# 写法上分两段:CREATE TABLE 之间用分号分隔的多 statement,
# sqlite3 单次 execute 只接一条,逐条执行更稳。
_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS messages (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        ts           INTEGER NOT NULL,
        adapter      TEXT    NOT NULL,
        group_id     TEXT,
        user_id      TEXT    NOT NULL,
        nickname     TEXT    NOT NULL,
        content      TEXT    NOT NULL,
        is_bot       INTEGER NOT NULL,
        reply_to_ts  INTEGER
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_msgs_group_ts ON messages (adapter, group_id, ts DESC)",
    """
    CREATE TABLE IF NOT EXISTS message_images (
        message_id   INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        idx          INTEGER NOT NULL,
        url          TEXT    NOT NULL,
        sha256       TEXT,
        mime_type    TEXT,
        PRIMARY KEY (message_id, idx)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_imgs_message ON message_images (message_id)",
    """
    CREATE TABLE IF NOT EXISTS message_forwards (
        message_id   INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
        content      TEXT    NOT NULL
    )
    """,
]


class MessageStore:
    """SQLite WAL 模式的消息日志,单进程内多线程读写安全。"""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False:NoneBot driver 可能在多 thread 上访问 ASGI 应用。
        # SQLite WAL 模式天然支持并发读 + 单写;我们应用层目前都是单 event loop,
        # 不存在跨线程写竞争,本参数只为放宽 sqlite3 模块自身的线程检查。
        self._conn = sqlite3.connect(self._db_path, isolation_level=None, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.row_factory = sqlite3.Row
        for stmt in _SCHEMA_STATEMENTS:
            self._conn.execute(stmt)
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._conn.close()
        except Exception:
            pass
        self._closed = True

    def append(self, msg: BufferedMessage) -> int | None:
        """写入消息,回填 msg.id。失败返回 None 但不抛。"""
        try:
            cur = self._conn.execute(
                "INSERT INTO messages "
                "(ts, adapter, group_id, user_id, nickname, content, is_bot, reply_to_ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    msg.ts,
                    msg.adapter,
                    msg.group_id,
                    msg.user_id,
                    msg.nickname,
                    msg.content,
                    1 if msg.is_bot else 0,
                    msg.reply_to_ts,
                ),
            )
            msg_id = cur.lastrowid
            for i, url in enumerate(msg.image_urls):
                self._conn.execute(
                    "INSERT INTO message_images (message_id, idx, url, sha256, mime_type) VALUES (?, ?, ?, NULL, NULL)",
                    (msg_id, i, url),
                )
            if msg.forward_content:
                self._conn.execute(
                    "INSERT INTO message_forwards (message_id, content) VALUES (?, ?)",
                    (msg_id, msg.forward_content),
                )
            msg.id = msg_id
            return msg_id
        except sqlite3.Error as exc:
            logger.error(f"[message_store] append failed: {exc}; msg dropped (ts={msg.ts})")
            return None

    def get_recent(
        self,
        adapter: str,
        group_id: str | None,
        limit: int,
        before_ts: int | None = None,
        owner_user_id: str | None = None,
    ) -> list[BufferedMessage]:
        """新→旧顺序返回最近 limit 条;同 MessageBuffer.get_recent 语义。"""
        if group_id is None and owner_user_id is None:
            raise ValueError("owner_user_id is required when group_id is None (private chat lookup)")
        params: list[Any] = [adapter]
        where = "adapter = ?"
        if group_id is None:
            where += " AND group_id IS NULL AND user_id = ?"
            params.append(owner_user_id)
        else:
            where += " AND group_id = ?"
            params.append(group_id)
        if before_ts is not None:
            where += " AND ts < ?"
            params.append(before_ts)
        params.append(limit)
        rows = self._conn.execute(
            "SELECT id, ts, adapter, group_id, user_id, nickname, content, is_bot, reply_to_ts "
            f"FROM messages WHERE {where} ORDER BY ts DESC LIMIT ?",
            params,
        ).fetchall()
        if not rows:
            return []
        ids = [r["id"] for r in rows]
        placeholders = ",".join("?" for _ in ids)
        img_rows = self._conn.execute(
            "SELECT message_id, idx, url FROM message_images "
            f"WHERE message_id IN ({placeholders}) ORDER BY message_id, idx",
            ids,
        ).fetchall()
        urls_by_msg: dict[int, list[str]] = defaultdict(list)
        for ir in img_rows:
            urls_by_msg[ir["message_id"]].append(ir["url"])
        fwd_rows = self._conn.execute(
            f"SELECT message_id, content FROM message_forwards WHERE message_id IN ({placeholders})",
            ids,
        ).fetchall()
        fwd_by_msg = {fr["message_id"]: fr["content"] for fr in fwd_rows}
        return [
            BufferedMessage(
                ts=r["ts"],
                adapter=r["adapter"],
                group_id=r["group_id"],
                user_id=r["user_id"],
                nickname=r["nickname"],
                content=r["content"],
                image_urls=urls_by_msg.get(r["id"], []),
                reply_to_ts=r["reply_to_ts"],
                is_bot=bool(r["is_bot"]),
                id=r["id"],
                forward_content=fwd_by_msg.get(r["id"]),
            )
            for r in rows
        ]

    def get_message_images_meta(self, message_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
        """按 message_id 拉对应的 image 元数据(url + sha256 + mime)。

        未找到的 message_id 不出现在返回 dict 里(不是 KeyError)。
        """
        if not message_ids:
            return {}
        placeholders = ",".join("?" for _ in message_ids)
        rows = self._conn.execute(
            "SELECT message_id, idx, url, sha256, mime_type FROM message_images "
            f"WHERE message_id IN ({placeholders}) ORDER BY message_id, idx",
            message_ids,
        ).fetchall()
        out: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            out[r["message_id"]].append(
                {
                    "idx": r["idx"],
                    "url": r["url"],
                    "sha256": r["sha256"],
                    "mime_type": r["mime_type"],
                }
            )
        return dict(out)

    def get_message_owners(self, message_ids: list[int]) -> dict[int, tuple[str, str | None]]:
        """message_id → (adapter, group_id)。未找到的 id 不出现在返回里。

        反向通道的范围判定要它:按 id 取内容的工具(get_message_images)拿到的是自增
        小整数,不解析归属就等于把全库暴露给任何一把 token。
        """
        if not message_ids:
            return {}
        placeholders = ",".join("?" for _ in message_ids)
        rows = self._conn.execute(
            f"SELECT id, adapter, group_id FROM messages WHERE id IN ({placeholders})",
            message_ids,
        ).fetchall()
        return {r["id"]: (r["adapter"], r["group_id"]) for r in rows}

    def update_image_sha(self, message_id: int, idx: int, sha256: str, mime_type: str) -> None:
        try:
            self._conn.execute(
                "UPDATE message_images SET sha256=?, mime_type=? WHERE message_id=? AND idx=?",
                (sha256, mime_type, message_id, idx),
            )
        except sqlite3.Error as exc:
            logger.warning(f"[message_store] update_image_sha m={message_id} idx={idx} failed: {exc}")

    def vacuum(self, min_ts: int, max_rows: int) -> int:
        """删 ts < min_ts 的行,以及超 max_rows 上限的最老的行。返回删除行数。

        FK + ON DELETE CASCADE 会自动级联清理 message_images。
        """
        deleted = 0
        cur = self._conn.execute("DELETE FROM messages WHERE ts < ?", (min_ts,))
        deleted += cur.rowcount
        total = self._conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
        if total > max_rows:
            extra = total - max_rows
            self._conn.execute(
                "DELETE FROM messages WHERE id IN (SELECT id FROM messages ORDER BY ts ASC LIMIT ?)",
                (extra,),
            )
            deleted += extra
        return deleted

    def counts_by_scope(self) -> dict[tuple[str, str], int]:
        """返回 (adapter, scope_id) → 消息条数。scope_id 口径同 known_groups。

        群按 group_id 合并(不按发言人拆),私聊折成 `@private:<user_id>` —— 这个
        折叠在 SQL 里做,省得把整表拉回来再在 Python 里分组。
        """
        rows = self._conn.execute(
            "SELECT adapter, COALESCE(group_id, '@private:' || user_id) AS scope, COUNT(*) AS n "
            "FROM messages GROUP BY adapter, scope"
        ).fetchall()
        return {(r["adapter"], r["scope"]): r["n"] for r in rows}

    def known_groups(self) -> list[tuple[str, str]]:
        """返回所有已有消息的 (adapter, scope_id) 唯一集合。

        scope_id:群聊 = group_id,私聊 = `@private:<user_id>`(与
        MessageBuffer.is_private_key 同口径)。
        """
        rows = self._conn.execute("SELECT DISTINCT adapter, group_id, user_id FROM messages").fetchall()
        seen: set[tuple[str, str]] = set()
        out: list[tuple[str, str]] = []
        for r in rows:
            if r["group_id"] is not None:
                k = (r["adapter"], r["group_id"])
            else:
                k = (r["adapter"], f"@private:{r['user_id']}")
            if k not in seen:
                seen.add(k)
                out.append(k)
        return out
