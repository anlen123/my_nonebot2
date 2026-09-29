"""消息缓冲外观层。

外部调用方 (handlers / prompt_builder / mcp tools) 仍然只看到 `MessageBuffer`
这一类型,语义跟之前一致 (append / get_recent / known_groups + 私聊桶隔离)。
内部转调:
- `MessageStore` (持久化 + autoincrement msg_id)
- `ImageFetcher` (异步把消息里的 image_urls 抓回 ImageCache)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .storage.image_fetcher import ImageFetcher
    from .storage.message_store import MessageStore


@dataclass
class BufferedMessage:
    ts: int
    adapter: str
    group_id: str | None  # None = 私聊
    user_id: str
    nickname: str
    content: str
    image_urls: list[str] = field(default_factory=list)
    reply_to_ts: int | None = None
    is_bot: bool = False
    id: int | None = None
    """DB 主键。perception 构造时为 None,MessageStore.append 写入后回填。
    handlers 不应直接读写;由 MessageStore.append 管控。"""
    forward_content: str | None = None
    """合并转发的完整展开块(<forwarded_messages>…</forwarded_messages>)。
    content 里只存单行 preview;全文走 message_forwards 附表,由
    MessageStore.append 写入、get_recent 水合。无折叠消息时恒为 None。"""


_PRIVATE_KEY_PREFIX = "@private:"


def _bucket_key(adapter: str, group_id: str | None, user_id: str | None) -> tuple[str, str]:
    """私聊用 user_id 合成 scope_id,群聊用 group_id。

    保留这个 helper 主要是兼容 MessageStore.known_groups 返回的合成 scope
    以及 is_private_key 判别约定。
    """
    if group_id is None:
        return (adapter, f"{_PRIVATE_KEY_PREFIX}{user_id or '?'}")
    return (adapter, group_id)


def is_private_key(key: tuple[str, str]) -> bool:
    """判断 known_groups() 返回的 (adapter, scope_id) 是否为私聊桶。"""
    return key[1].startswith(_PRIVATE_KEY_PREFIX)


class MessageBuffer:
    """对外 API 不变;实现转调 MessageStore + ImageFetcher。"""

    def __init__(self, *, store: MessageStore, fetcher: ImageFetcher) -> None:
        self._store = store
        self._fetcher = fetcher

    def append(self, msg: BufferedMessage) -> None:
        msg_id = self._store.append(msg)
        if msg_id is not None and msg.image_urls:
            self._fetcher.submit(msg_id, msg.image_urls)

    def get_recent(
        self,
        adapter: str,
        group_id: str | None,
        limit: int,
        before_ts: int | None = None,
        owner_user_id: str | None = None,
    ) -> list[BufferedMessage]:
        return self._store.get_recent(
            adapter=adapter,
            group_id=group_id,
            limit=limit,
            before_ts=before_ts,
            owner_user_id=owner_user_id,
        )

    def known_groups(self) -> list[tuple[str, str]]:
        return self._store.known_groups()

    def counts_by_scope(self) -> dict[tuple[str, str], int]:
        return self._store.counts_by_scope()
