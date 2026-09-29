"""In-flight 调用追踪 + coalesce 重燃支持。

修同一 (adapter, group_id|user_id) 上事件 task 并发调 chat() 的 bug:
in-flight 时新消息只更新 pending 单元,等当前一发完成后再合并跑一次。

线程安全:**否**。预设单线程 asyncio 事件循环,与 ActiveSessionManager 一致。

Pending 优先级:explicit-trigger 不被 bystander 覆盖,以保证 @bot 一旦排进
pending 就一定能在前一发完成后被 _refire 跑到 chat()。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .message_buffer import BufferedMessage

# Refire 链最大深度。超过则丢 pending、warn,等下一个新触发。
# 一次 burst 最多产出 1(主回) + MAX_REFIRE_DEPTH(链尾)= 4 发回复。
MAX_REFIRE_DEPTH = 3


@dataclass
class PendingEntry:
    """In-flight 期间排队的消息及触发元数据。

    pending 不再裸存 BufferedMessage——单独抽 PendingEntry 是为了跨 turn 边界
    保留几个 plumbing 决策所需要的事实:
      - 这条排队消息原本是不是 explicit-trigger
      - 它是否被平台确认为「冲 bot 说的」(寻址与触发是两个事实,不互相派生:
        synth 事件可以 explicit 而不寻址,trigger=all 下每条都 explicit 也不寻址)
      - 它的原始 adapter 侧 message_id(用于失败时贴 emoji notice)
    """

    msg: BufferedMessage
    is_explicit_trigger: bool
    original_msg_id: str | int | None = None
    # 默认 False 是安全方向:漏传只会让模型自己判归属,不会错误断言「这条冲你说的」。
    addressed_to_bot: bool = False


@dataclass
class InflightSlot:
    started_at: int
    pending: PendingEntry | None = None


class InflightRegistry:
    """per-target 非阻塞 busy 标记 + pending 单元。

    Key 约定:
      - 群: ("adapter", "group:" + group_id)
      - 私聊: ("adapter", "private:" + user_id)

    Pending 覆盖规则:
      - 空 pending → 写入,返回 'pending_set'
      - 已存 bystander pending,新到 explicit → 覆盖升级,返回 'pending_set'
      - 已存 bystander pending,新到 bystander → latest wins 覆盖,返回 'pending_set'
      - 已存 explicit pending,新到 explicit → latest wins 覆盖,返回 'pending_set'
      - 已存 explicit pending,新到 bystander → 不覆盖,返回 'pending_kept'

    不持有任何 asyncio.Task 引用 —— 重燃由 caller 用 create_task 自己接手,
    registry 只负责「现在有没有人在跑」+「跑完后是否要再跑一次」两个状态。
    """

    def __init__(self) -> None:
        self._slots: dict[tuple[str, str], InflightSlot] = {}

    def try_enter(
        self,
        key: tuple[str, str],
        current_msg: BufferedMessage,
        *,
        is_explicit_trigger: bool,
        original_msg_id: str | int | None,
        now_ms: int,
        addressed_to_bot: bool = False,
    ) -> Literal["entered", "pending_set", "pending_kept"]:
        """无 slot → 占位 started_at=now_ms,返回 'entered'。
        有 slot 且 pending 是 explicit 而新到 bystander → 'pending_kept'。
        其它情况 → 写 pending,返回 'pending_set'。
        """
        slot = self._slots.get(key)
        if slot is None:
            self._slots[key] = InflightSlot(started_at=now_ms)
            return "entered"
        # explicit 不被 bystander 覆盖,保护用户明示意图
        if not is_explicit_trigger and slot.pending is not None and slot.pending.is_explicit_trigger:
            return "pending_kept"
        slot.pending = PendingEntry(
            msg=current_msg,
            is_explicit_trigger=is_explicit_trigger,
            original_msg_id=original_msg_id,
            addressed_to_bot=addressed_to_bot,
        )
        return "pending_set"

    def take_pending(self, key: tuple[str, str]) -> PendingEntry | None:
        """Destructive read。无 slot 或 pending 为 None 都返回 None。"""
        slot = self._slots.get(key)
        if slot is None:
            return None
        entry = slot.pending
        slot.pending = None
        return entry

    def exit(self, key: tuple[str, str]) -> None:
        """释放 slot。pending 仍在的话由调用方自行先 take_pending。
        slot 不存在则 no-op。
        """
        self._slots.pop(key, None)
