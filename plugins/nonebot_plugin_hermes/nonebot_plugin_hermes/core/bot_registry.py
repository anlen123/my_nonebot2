"""(adapter, scope, scope_id) → (Bot, Target) 的内存路由表。

Target 永远不序列化:重启即清,首次 perception 事件重新填表。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ScopeT = Literal["private", "group"]
"""push_message 路由表的 scope 维度。typo 会被 type checker 拦下,避免运行期静默 routing miss。"""


@dataclass
class BotEntry:
    bot_self_id: str
    target: Any  # nonebot_plugin_alconna.Target,只存内存
    last_seen_at: int  # ms;M1-mem 暂不读,M2 持久化判定时用


class BotRegistry:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, ScopeT, str], BotEntry] = {}

    def upsert(
        self,
        adapter: str,
        scope: ScopeT,
        scope_id: str,
        bot_self_id: str,
        target: Any,
        ts: int,
    ) -> None:
        self._entries[(adapter, scope, scope_id)] = BotEntry(
            bot_self_id=bot_self_id,
            target=target,
            last_seen_at=ts,
        )

    def get(self, adapter: str, scope: ScopeT, scope_id: str) -> BotEntry | None:
        return self._entries.get((adapter, scope, scope_id))

    def remove(self, adapter: str, scope: ScopeT, scope_id: str) -> None:
        self._entries.pop((adapter, scope, scope_id), None)

    def known(self, adapter: str | None = None) -> list[tuple[str, ScopeT, str]]:
        if adapter is None:
            return list(self._entries.keys())
        return [k for k in self._entries if k[0] == adapter]
