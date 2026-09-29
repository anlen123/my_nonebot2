"""群活跃态(reactive 模式)的内存状态机。

每 (adapter, group_id) 至多一条活跃记录;TTL 滑动续期。
重启即清。
"""

from __future__ import annotations

import builtins
from dataclasses import dataclass

# turn 收尾时给窗口垫的最小剩余量。只需盖住「turn 结束 → pending 接力 refire」这次
# 交接(一次事件循环跳转,期间 cron sweep 可能扫过),不是再送一个 TTL。
_TURN_END_GRACE_MS = 10_000


@dataclass
class ActiveSession:
    adapter: str
    group_id: str
    triggered_by: str
    started_at: int  # ms
    last_active_at: int  # ms
    expires_at: int  # ms
    topic_hint: str | None = None
    last_bot_reply_at: int = 0
    """bot 最近一次在本群发出 reactive 回复的 ms 时间戳。0 = 本窗口期未回复过。
    用作 handlers 的 post-reply cooldown 判定。再次 trigger() 时清零,避免跨窗口
    残留状态把新一轮对话的第一条非显式消息直接 skip 掉。"""

    last_bot_reply_media: int = 0
    """上一次回复实际投出去的媒体数。同 turn 去重闸门据此区分「重复答案」与
    「文本已答但图还没出去」:push_message 拿主机本地路径当图时只发出文本,
    此时若把带图的 submit_decision 整条抑制,那张图就彻底丢了。"""

    last_bot_reply_text: str = ""
    """上一次回复(含 MCP push)的文本原文。同 turn 去重据此判断 submit_decision 是不是
    在**复述**刚发出去的话 —— 只有近乎逐字相同才抑制。空串 = 不知道原文,一律放行。"""

    inflight_turns: int = 0
    """正在跑的 turn 数(租约计数)。>0 时本 session 不判过期、也不被 sweep 清掉。

    一发 chat 可以跑得比 TTL 还长(上游工具调用 / 生图 / 上下文压缩 / 重试)。若窗口
    在 turn 中途按 TTL 死掉,收尾的 touch / mark_bot_replied 全打在空处,排在 pending
    里的下一发(哪怕是 explicit @)也会在 get_if_active 处被静默丢弃 —— 用户侧就是
    「@ 了 bot 但永远没回应」。租约把窗口存活与 turn 生命期绑起来,turn 结束再重新计时。

    租约挂在实例上而非 key 上,`begin_turn` 返回实例、`end_turn` 收实例:turn 跑期间
    若有新的显式触发调 trigger(),字典里换成全新实例(自带满窗、计数 0),旧实例连同
    它的租约一起被丢掉,语义正好。按 key 归还就会把租约还到那个新实例头上,眼下只因
    「每群同时最多一发 reactive turn」(InflightRegistry 保证)才没出事 —— 不要把
    正确性寄托在那条不变量加一个 max(0, …) 兜底上。
    """

    def is_alive(self, now_ms: int) -> bool:
        """未到期,或正被 in-flight turn 租住。"""
        return self.expires_at > now_ms or self.inflight_turns > 0


class ActiveSessionManager:
    """滑动 TTL 状态机,跟踪哪些 (adapter, group_id) 处于 reactive 监听窗口。

    线程安全:**否**。预设单线程 asyncio 事件循环,不要从背景线程访问。
    租约:`begin_turn` / `end_turn` 之间该 session 一律算活着(见 ActiveSession.
    inflight_turns),`is_active` / `get_if_active` / `touch` / `sweep_expired` 都尊重它。
    GC 策略:不在读路径自动剔除过期 session,由 Task 16 的 cron 调 sweep_expired()
    定期清理。读 API 中:`is_active` / `touch` / `get_if_active` 是 TTL 感知的;
    `get()` 是裸访问,**返回过期 session**——仅用于调试 / 日志,handler 应优先用
    is_active / get_if_active。
    """

    def __init__(self, default_ttl_sec: int = 300) -> None:
        self._ttl_ms = default_ttl_sec * 1000
        self._sessions: dict[tuple[str, str], ActiveSession] = {}

    def trigger(
        self,
        adapter: str,
        group_id: str,
        user_id: str,
        now_ms: int,
        topic_hint: str | None = None,
    ) -> ActiveSession:
        s = ActiveSession(
            adapter=adapter,
            group_id=group_id,
            triggered_by=user_id,
            started_at=now_ms,
            last_active_at=now_ms,
            expires_at=now_ms + self._ttl_ms,
            topic_hint=topic_hint,
        )
        self._sessions[(adapter, group_id)] = s
        return s

    def touch(self, adapter: str, group_id: str, now_ms: int) -> ActiveSession | None:
        s = self._sessions.get((adapter, group_id))
        if s is None or not s.is_alive(now_ms):
            return None
        s.last_active_at = now_ms
        s.expires_at = now_ms + self._ttl_ms
        return s

    def get(self, adapter: str, group_id: str) -> ActiveSession | None:
        """裸访问,**不检查 TTL**——可能返回已过期 session。

        多数 handler 应改用 `get_if_active` 或先 `is_active` 校验。本方法保留是为了
        调试 / 日志场景需要观测已过期但尚未被 sweep 的 session。
        """
        return self._sessions.get((adapter, group_id))

    def get_if_active(self, adapter: str, group_id: str, now_ms: int) -> ActiveSession | None:
        """TTL 感知的 get:只在 session 存在且未过期时返回。"""
        s = self._sessions.get((adapter, group_id))
        if s is None or not s.is_alive(now_ms):
            return None
        return s

    def is_active(self, adapter: str, group_id: str, now_ms: int) -> bool:
        s = self._sessions.get((adapter, group_id))
        return s is not None and s.is_alive(now_ms)

    def mark_bot_replied(
        self,
        adapter: str,
        group_id: str,
        now_ms: int,
        media_count: int = 0,
        text: str = "",
    ) -> bool:
        """记录 bot 在本群刚发出回复的时间戳、实际投出的媒体数与文本原文;session 缺失则 no-op。

        不滑动 expires_at(滑动续期由 touch 负责)。handlers 在 reactive 模式 send 成功后
        调用,push_message 也调,供 post-reply cooldown 与同 turn 去重判定。

        text 用于判断后续 submit_decision 是否在复述刚发出去的话;不传(空串)时同 turn
        去重会放行,宁可多发一条也不吞掉答案。

        返回是否真的写进去了。False = session 已经被 should_exit_active 收掉(`end()` 的
        唯一调用点),本群的 post-reply cooldown
        与同 turn 去重这一轮都失去依据 —— 调用方应当把它记出来,否则「冷却全程不生效」
        在日志里只表现为 last_bot_reply_at 永远是 0,查不到原因。
        """
        s = self._sessions.get((adapter, group_id))
        if s is None:
            return False
        s.last_bot_reply_at = now_ms
        s.last_bot_reply_media = media_count
        s.last_bot_reply_text = text
        return True

    def begin_turn(self, adapter: str, group_id: str) -> ActiveSession | None:
        """占用租约并返回被租住的那个实例;session 不存在则返回 None(不新建)。

        不新建是刻意的:窗口只会被 `should_exit_active` 主动收掉,那是"别再说话了"的
        明确表态,不该被一发排队的 turn 复活。

        返回实例而不是让调用方按 key 再查一次 —— `end_turn` 必须还给**同一个实例**,
        见 ActiveSession.inflight_turns 的说明。
        """
        s = self._sessions.get((adapter, group_id))
        if s is None:
            return None
        s.inflight_turns += 1
        return s

    def end_turn(self, session: ActiveSession | None, now_ms: int) -> None:
        """释放 `begin_turn` 拿到的租约;剩余窗口不足 `_TURN_END_GRACE_MS` 时垫到这个下限。

        `session=None`(当初没租到)→ no-op。

        为什么要垫:pending 里的消息紧接着就要在 refire 入口做 get_if_active,而这
        条路径上"窗口刚好在交接的这一瞬过期"是真实存在的 —— 交接跨一次事件循环跳转,
        期间 cron sweep 也可能扫过。按剩余量垫到一个**小**的下限,既盖住交接,又不会
        把「turn 结束后重新计时」变成「turn 结束后再送一整个 TTL」:后者对
        should_reply=false 的静默 turn 尤其错,那正是滑动窗该自然收口的时刻,却被
        续成了只要群里还有人说话就永不过期。真答了话的 turn 由 touch 给满窗续期。
        """
        if session is None:
            return
        session.inflight_turns = max(0, session.inflight_turns - 1)
        if session.inflight_turns > 0:
            return
        floor = now_ms + min(self._ttl_ms, _TURN_END_GRACE_MS)
        session.expires_at = max(session.expires_at, floor)

    def update_topic(self, adapter: str, group_id: str, topic_hint: str | None) -> None:
        """更新或清空 topic_hint。

        传 None 即清空(允许 Hermes 在话题漂移检测后主动收尾 topic)。
        若 (adapter, group_id) 不存在则 no-op。
        """
        s = self._sessions.get((adapter, group_id))
        if s is not None:
            s.topic_hint = topic_hint

    def end(self, adapter: str, group_id: str) -> None:
        """按 key 无条件收窗。turn 内表达"这轮谈完了"请用 `end_if_current`。"""
        self._sessions.pop((adapter, group_id), None)

    def end_if_current(self, session: ActiveSession) -> bool:
        """只在字典里仍是这个实例时收窗;返回是否真的收了。

        turn 跑期间可能有别人的显式触发调过 `trigger()`,字典里已经换成一个**新**窗口
        (还带着它自己排在 pending 里的那条 @)。本 turn 的 should_exit_active 说的是
        "我这轮谈完了",无权把后来那个窗口一起弹掉 —— 按 key 收窗会让那个人的 @ 在
        refire 入口的 get_if_active 处静默蒸发,用户侧只看到一个 busy 表情。
        """
        key = (session.adapter, session.group_id)
        if self._sessions.get(key) is not session:
            return False
        del self._sessions[key]
        return True

    def list(self, adapter: str | None = None) -> builtins.list[ActiveSession]:
        if adapter is None:
            return list(self._sessions.values())
        return [s for s in self._sessions.values() if s.adapter == adapter]

    def sweep_expired(self, now_ms: int) -> builtins.list[ActiveSession]:
        expired = [s for s in self._sessions.values() if not s.is_alive(now_ms)]
        for s in expired:
            self._sessions.pop((s.adapter, s.group_id), None)
        return expired
