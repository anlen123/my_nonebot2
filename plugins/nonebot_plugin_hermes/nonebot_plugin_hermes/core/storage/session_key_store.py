"""SQLite 后端的 session key 映射。

存 internal_id → (当前 session key, generation)。两件事都必须跨重启活着:

- **轮换后的 key**:上游 compression 会把旧 session 置为 closed 并回传新 id
  (响应头 ``X-Hermes-Session-Id``)。重启后若退回派生 key,就又钉回那个已关闭的
  父会话 —— 写全部失败,且每次压缩再分叉一个兄弟,直到血缘歧义无法自愈。
- **generation**:`/clear` 靠它产出 `-gN` 新 key。只存内存的话,重启等于把被清掉的
  会话原地复活。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from nonebot import logger

_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS session_keys (
        internal_id  TEXT    PRIMARY KEY,
        session_key  TEXT    NOT NULL,
        generation   INTEGER NOT NULL DEFAULT 0,
        updated_at   REAL    NOT NULL
    )
    """,
]


class SessionKeyStore:
    """internal_id → session key 的持久化映射。失败降级为不持久化,绝不抛。"""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False 的理由同 MessageStore:NoneBot driver 可能在多
        # thread 上访问 ASGI 应用,而写入方只有 event loop 一个。
        self._conn = sqlite3.connect(self._db_path, isolation_level=None, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
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

    def load_all(self) -> dict[str, tuple[str, int]]:
        """返回 internal_id → (session_key, generation)。读失败返回空表。"""
        try:
            rows = self._conn.execute("SELECT internal_id, session_key, generation FROM session_keys").fetchall()
        except Exception as exc:
            logger.warning(f"[SESSION] 读取 session key 映射失败,本次退化为纯内存: {exc}")
            return {}
        return {r["internal_id"]: (r["session_key"], r["generation"]) for r in rows}

    def put(self, internal_id: str, session_key: str, generation: int, *, now: float) -> None:
        try:
            self._conn.execute(
                "INSERT INTO session_keys (internal_id, session_key, generation, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(internal_id) DO UPDATE SET "
                "session_key=excluded.session_key, generation=excluded.generation, updated_at=excluded.updated_at",
                (internal_id, session_key, generation, now),
            )
        except Exception as exc:
            logger.warning(f"[SESSION] 持久化 session key 失败 ({internal_id}): {exc}")
