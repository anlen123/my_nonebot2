"""长 turn 跨过自身活跃窗时的行为。

生产事故形状:一发 reactive turn 跑了 5 分钟以上(上游生图),期间
  - 活跃窗按 TTL 静默到期
  - 排在 pending 里的 explicit @ 在 refire 时撞上 `get_if_active() is None`
    → `_run_reactive_turn` 直接 `return None`,既不进 chat 也不留日志
  - turn 收尾的 touch / mark_bot_replied 全部打在空 session 上,cooldown 闸门失效

两组断言:
  1. in-flight turn 期间活跃窗不过期(租约),turn 收尾时补窗 → 排队的 @ 仍能跑到 chat
  2. 上面那些静默路径各自留下可观测日志
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from nonebot_plugin_hermes import mcp as _mcp
from nonebot_plugin_hermes.core.active_session import ActiveSessionManager
from nonebot_plugin_hermes.core.bot_registry import BotRegistry
from nonebot_plugin_hermes.core.inflight import InflightRegistry
from nonebot_plugin_hermes.core.message_buffer import MessageBuffer
from nonebot_plugin_hermes.core.storage.image_cache import ImageCache
from nonebot_plugin_hermes.core.storage.image_fetcher import ImageFetcher
from nonebot_plugin_hermes.core.storage.message_store import MessageStore


@dataclass
class _FakeTarget:
    id: str
    private: bool = False
    adapter: str = "ob11"


def _fake_bot(self_id: str = "999"):
    bot = MagicMock()
    bot.self_id = self_id
    bot.call_api = AsyncMock()
    return bot


def _decision_result(text: str = "ok"):
    from nonebot_plugin_hermes.core.hermes_client import ChatResult

    return ChatResult(
        raw_text=text,
        media_urls=[],
        structured={"should_reply": True, "reply_text": text, "should_exit_active": False},
        parse_failed=False,
        is_transport_error=False,
    )


@pytest.fixture(autouse=True)
def _setup_runtime(tmp_path):
    store = MessageStore(db_path=tmp_path / "messages.db")
    cache = ImageCache(cache_dir=tmp_path / "imgs", quota_bytes=1024 * 1024)
    fetcher = ImageFetcher(store=store, cache=cache)
    _mcp.message_buffer = MessageBuffer(store=store, fetcher=fetcher)
    _mcp.active_sessions = ActiveSessionManager(default_ttl_sec=300)
    _mcp.bot_registry = BotRegistry()
    _mcp.inflight = InflightRegistry()
    yield
    _mcp.message_buffer = None
    _mcp.active_sessions = None
    _mcp.bot_registry = None
    _mcp.inflight = None
    store.close()


# --- 单元:租约语义 -------------------------------------------------------


def test_inflight_turn_keeps_window_alive_past_ttl():
    """begin_turn 期间窗口不判过期 —— TTL 到了也算活着。"""
    mgr = ActiveSessionManager(default_ttl_sec=60)
    mgr.trigger("ob11", "g1", "u1", now_ms=0)
    assert mgr.begin_turn("ob11", "g1") is mgr.get("ob11", "g1")

    after_ttl = 60_001
    assert mgr.is_active("ob11", "g1", after_ttl) is True
    assert mgr.get_if_active("ob11", "g1", after_ttl) is not None
    assert mgr.touch("ob11", "g1", now_ms=after_ttl) is not None


def test_inflight_turn_survives_sweep():
    """cron sweep 不能把正在跑 turn 的 session 清掉 —— 清掉就等于 turn 收尾时无处落笔。"""
    mgr = ActiveSessionManager(default_ttl_sec=60)
    mgr.trigger("ob11", "g1", "u1", now_ms=0)
    mgr.begin_turn("ob11", "g1")

    assert mgr.sweep_expired(now_ms=60_001) == []
    assert mgr.get("ob11", "g1") is not None


def test_end_turn_pads_expired_window_to_the_grace_floor_only():
    """turn 跑超了自己的窗 → 收尾只垫一个小的宽限下限,不是再送一整个 TTL。

    送满窗对 should_reply=false 的静默 turn 是错的:那正是滑动窗该自然收口的时刻。
    宽限只需盖住「turn 结束 → refire 接力」这次交接。
    """
    from nonebot_plugin_hermes.core.active_session import _TURN_END_GRACE_MS

    mgr = ActiveSessionManager(default_ttl_sec=60)
    mgr.trigger("ob11", "g1", "u1", now_ms=0)
    lease = mgr.begin_turn("ob11", "g1")

    mgr.end_turn(lease, now_ms=90_000)

    assert mgr.get("ob11", "g1").expires_at == 90_000 + _TURN_END_GRACE_MS
    assert mgr.is_active("ob11", "g1", 90_001) is True
    # 租约已释放:宽限一过就该真过期
    assert mgr.is_active("ob11", "g1", 90_000 + _TURN_END_GRACE_MS + 1) is False


def test_end_turn_pads_window_about_to_expire():
    """剩余窗口不足宽限量也要垫 —— 「turn 结束 → refire」交接跨一次事件循环跳转,
    期间 cron sweep 也可能扫过,只判「已过期」会漏掉这一瞬。"""
    from nonebot_plugin_hermes.core.active_session import _TURN_END_GRACE_MS

    mgr = ActiveSessionManager(default_ttl_sec=60)
    mgr.trigger("ob11", "g1", "u1", now_ms=0)
    lease = mgr.begin_turn("ob11", "g1")

    mgr.end_turn(lease, now_ms=59_900)  # 只剩 100ms

    assert mgr.get("ob11", "g1").expires_at == 59_900 + _TURN_END_GRACE_MS


def test_end_turn_leaves_comfortable_window_untouched():
    """剩余窗口宽裕时 end_turn 不动 expires_at —— 常规续期是 touch 的职责。"""
    mgr = ActiveSessionManager(default_ttl_sec=60)
    mgr.trigger("ob11", "g1", "u1", now_ms=0)
    lease = mgr.begin_turn("ob11", "g1")

    mgr.end_turn(lease, now_ms=10_000)

    assert mgr.get("ob11", "g1").expires_at == 60_000


def test_lease_is_returned_to_the_instance_not_the_key():
    """turn 跑期间新的显式触发换掉了字典里的实例 → 归还租约不能落到新实例头上。

    按 key 归还的话,新实例的计数会被减成负数(靠 max(0,…) 兜底)、expires_at 还可能
    被垫改;每群只跑一发 reactive turn 是 InflightRegistry 的不变量,正确性不该压在它上面。
    """
    mgr = ActiveSessionManager(default_ttl_sec=60)
    mgr.trigger("ob11", "g1", "u1", now_ms=0)
    lease = mgr.begin_turn("ob11", "g1")

    # turn 还在跑,user-B 的 explicit @ 触发新窗口(全新实例,自带满窗)
    fresh = mgr.trigger("ob11", "g1", "u2", now_ms=50_000)
    mgr.end_turn(lease, now_ms=90_000)

    assert mgr.get("ob11", "g1") is fresh
    assert fresh.inflight_turns == 0
    assert fresh.expires_at == 50_000 + 60_000  # 没被旧 turn 的归还动过


def test_turn_lease_on_missing_session_is_noop():
    """session 不存在(被 should_exit_active 收掉)→ 租不到,归还也不抛。"""
    mgr = ActiveSessionManager(default_ttl_sec=60)
    assert mgr.begin_turn("ob11", "ghost") is None
    mgr.end_turn(None, now_ms=1_000)
    assert mgr.get("ob11", "ghost") is None


def test_mark_bot_replied_reports_whether_it_landed():
    """返回值区分「写进去了」和「session 已经没了」,让调用方能把后者记出来。"""
    mgr = ActiveSessionManager(default_ttl_sec=60)
    mgr.trigger("ob11", "g1", "u1", now_ms=0)

    assert mgr.mark_bot_replied("ob11", "g1", now_ms=1_000) is True
    assert mgr.mark_bot_replied("ob11", "ghost", now_ms=1_000) is False


# --- 集成:排队的 explicit @ 不能被窗口过期吃掉 ---------------------------


@pytest.mark.asyncio
async def test_overrunning_turn_still_refires_queued_explicit_at(monkeypatch):
    """复现事故:turn 跑超活跃窗,期间排进 pending 的 explicit @ 必须仍然跑到 chat。

    窗口构造成 120ms 后到期,chat 故意跑 250ms —— 与线上「TTL 300s / turn 5min+」同形。
    """
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as handler_mod

    monkeypatch.setattr(plugin_config, "hermes_reactive_post_reply_cooldown_sec", 0)

    now = int(time.time() * 1000)
    # 窗口在 now+120ms 到期(等价于线上 18:04:55 触发、18:09:55 到期)
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now - 300_000 + 120)

    chat_args: list[dict] = []

    async def slow_chat(**kwargs):
        chat_args.append(kwargs)
        await asyncio.sleep(0.25)
        return _decision_result(f"reply-{len(chat_args)}")

    monkeypatch.setattr(handler_mod.hermes_client, "chat", slow_chat)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    target = _FakeTarget(id="g1")
    bot = _fake_bot()

    t1 = asyncio.create_task(
        handler_mod._handle_reactive_path(
            bot=bot,
            target=target,
            adapter_name="ob11",
            user_id="user-A",
            group_id="g1",
            text="重画一张",
            image_urls=[],
            is_explicit_trigger=True,
            now_ms=now,
            event_msg_id=1001,
        )
    )
    await asyncio.sleep(0.01)
    t2 = asyncio.create_task(
        handler_mod._handle_reactive_path(
            bot=bot,
            target=target,
            adapter_name="ob11",
            user_id="user-B",
            group_id="g1",
            text="你生成的图哪去了",
            image_urls=[],
            is_explicit_trigger=True,
            now_ms=now + 1,
            event_msg_id=1002,
        )
    )
    await asyncio.gather(t1, t2)
    await asyncio.sleep(0.5)

    assert len(chat_args) == 2, f"排队的 explicit @ 被窗口过期吃掉了,chat 只调用了 {len(chat_args)} 次"
    assert chat_args[1].get("user_id") == "user-B"


@pytest.mark.asyncio
async def test_reply_after_window_overrun_still_marks_bot_replied(monkeypatch):
    """turn 跑超窗后成功回复 → last_bot_reply_at 必须写得进去,否则 cooldown 闸门全程失效。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as handler_mod

    monkeypatch.setattr(plugin_config, "hermes_reactive_post_reply_cooldown_sec", 8)

    now = int(time.time() * 1000)
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now - 300_000 + 120)

    async def slow_chat(**kwargs):
        # 模拟 cron sweep 在 turn 跑到一半时扫过(线上就是这样丢掉 session 的:
        # 17:53:40 sweep,17:58:25 才回复 → 那次 mark 必然打空)
        await asyncio.sleep(0.15)
        swept = _mcp.active_sessions.sweep_expired(now_ms=int(time.time() * 1000))
        assert swept == [], f"in-flight turn 的 session 被 sweep 清掉了: {swept}"
        await asyncio.sleep(0.1)
        return _decision_result("图来啦")

    monkeypatch.setattr(handler_mod.hermes_client, "chat", slow_chat)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    await handler_mod._handle_reactive_path(
        bot=_fake_bot(),
        target=_FakeTarget(id="g1"),
        adapter_name="ob11",
        user_id="user-A",
        group_id="g1",
        text="重画一张",
        image_urls=[],
        is_explicit_trigger=True,
        now_ms=now,
        event_msg_id=1001,
    )

    sess = _mcp.active_sessions.get("ob11", "g1")
    assert sess is not None, "turn 收尾时 session 已经不在了"
    assert sess.last_bot_reply_at > 0, "回复发出去了但 last_bot_reply_at 没写上,cooldown 闸门会全程失效"


# --- 集成:静默路径必须留下痕迹 -------------------------------------------


@pytest.mark.asyncio
async def test_turn_without_active_window_logs_the_drop(monkeypatch):
    """窗口已被清掉(/clear、should_exit_active)时 turn 提前返回 —— 必须记一条,别当黑洞。"""
    from nonebot_plugin_hermes.handlers import message as handler_mod

    warns: list[str] = []
    monkeypatch.setattr(handler_mod.logger, "warning", lambda msg, *a, **kw: warns.append(str(msg)))
    chat_calls: list[dict] = []
    monkeypatch.setattr(handler_mod.hermes_client, "chat", AsyncMock(side_effect=lambda **kw: chat_calls.append(kw)))

    now = int(time.time() * 1000)
    result = await handler_mod._run_reactive_turn(
        bot=_fake_bot(),
        target=_FakeTarget(id="g1"),
        adapter_name="ob11",
        user_id="user-A",
        group_id="g1",
        text="有人吗",
        image_urls=[],
        is_explicit_trigger=True,
        now_ms=now,
    )

    assert result is None
    assert chat_calls == []
    assert any("g1" in m and "active window" in m for m in warns), f"窗口缺失导致的丢弃没留日志: {warns}"


@pytest.mark.asyncio
async def test_bystander_queued_behind_inflight_turn_is_logged(monkeypatch):
    """in-flight 期间旁观消息进 pending 是设计语义,但必须可观测 —— 线上五条消息集体消失
    就是因为这条分支一行日志都没有。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as handler_mod

    monkeypatch.setattr(plugin_config, "hermes_reactive_post_reply_cooldown_sec", 0)

    debugs: list[str] = []
    monkeypatch.setattr(handler_mod.logger, "debug", lambda msg, *a, **kw: debugs.append(str(msg)))

    now = int(time.time() * 1000)
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)

    async def slow_chat(**kwargs):
        await asyncio.sleep(0.2)
        return _decision_result("ok")

    monkeypatch.setattr(handler_mod.hermes_client, "chat", slow_chat)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    target = _FakeTarget(id="g1")
    bot = _fake_bot()

    t1 = asyncio.create_task(
        handler_mod._handle_reactive_path(
            bot=bot,
            target=target,
            adapter_name="ob11",
            user_id="user-A",
            group_id="g1",
            text="hello",
            image_urls=[],
            is_explicit_trigger=True,
            now_ms=now,
        )
    )
    await asyncio.sleep(0.01)
    await handler_mod._handle_reactive_path(
        bot=bot,
        target=target,
        adapter_name="ob11",
        user_id="user-B",
        group_id="g1",
        text="路过说一句",
        image_urls=[],
        is_explicit_trigger=False,
        now_ms=now + 1,
    )
    await t1
    await asyncio.sleep(0.3)

    assert any("pending_set" in m and "user-B" in m for m in debugs), f"旁观消息入队没留日志: {debugs}"


@pytest.mark.asyncio
async def test_should_exit_active_reply_does_not_warn(monkeypatch):
    """LLM 自己收窗(should_exit_active)后 mark 落空是预期的 —— 不该报 WARNING。

    日志洁净度也是契约:正常路径上刷 WARNING,运维就会学会忽略它,真出事那条也一起瞎了。
    """
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.core.hermes_client import ChatResult
    from nonebot_plugin_hermes.handlers import message as handler_mod

    monkeypatch.setattr(plugin_config, "hermes_reactive_post_reply_cooldown_sec", 0)

    warns: list[str] = []
    monkeypatch.setattr(handler_mod.logger, "warning", lambda msg, *a, **kw: warns.append(str(msg)))

    now = int(time.time() * 1000)
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)

    async def chat(**kwargs):
        return ChatResult(
            raw_text="那我先退下",
            media_urls=[],
            structured={"should_reply": True, "reply_text": "那我先退下", "should_exit_active": True},
            parse_failed=False,
            is_transport_error=False,
        )

    monkeypatch.setattr(handler_mod.hermes_client, "chat", chat)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    await handler_mod._handle_reactive_path(
        bot=_fake_bot(),
        target=_FakeTarget(id="g1"),
        adapter_name="ob11",
        user_id="user-A",
        group_id="g1",
        text="没事了",
        image_urls=[],
        is_explicit_trigger=True,
        now_ms=now,
    )

    assert warns == [], f"正常收窗路径不该报 WARNING: {warns}"


@pytest.mark.asyncio
async def test_list_active_sessions_shows_group_held_by_a_long_turn():
    """长 turn 租住的群必须仍出现在 list_active_sessions 里。

    该工具存在的理由就是与 push_message 的准入口径一致(见其 docstring)。租约让
    push 闸门(is_active)接受这个群,而这里若还按 expires_at 过滤,Hermes 会被告知
    「窗口关了」却依然可以 push —— SKILL.md 正是让它用这个列表判断延迟 push 是否还合适。
    """
    from nonebot_plugin_hermes.core.routing import CallerScope
    from nonebot_plugin_hermes.mcp.tools.list_active_sessions import (
        ListActiveSessionsInput,
        list_active_sessions_impl,
    )

    mgr = ActiveSessionManager(default_ttl_sec=60)
    mgr.trigger("ob11", "g1", "u1", now_ms=0)
    mgr.begin_turn("ob11", "g1")

    result = await list_active_sessions_impl(
        ListActiveSessionsInput(adapter=None),
        active_sessions=mgr,
        scope=CallerScope.dev(),
        now_ms=90_000,  # 早已过 TTL,但 turn 还在跑
    )

    assert [s.group_id for s in result.sessions] == ["g1"]


# --- should_exit_active 只收自己谈的那个窗口 -------------------------------


def test_end_if_current_pops_only_the_instance_it_was_given():
    mgr = ActiveSessionManager(default_ttl_sec=60)
    a = mgr.trigger("ob11", "g1", "u1", now_ms=0)

    # 同一实例 → 收掉
    assert mgr.end_if_current(a) is True
    assert mgr.get("ob11", "g1") is None

    # 已经被换成新实例 → 拒绝,新窗口活着
    a2 = mgr.trigger("ob11", "g1", "u1", now_ms=10_000)
    b = mgr.trigger("ob11", "g1", "u2", now_ms=20_000)
    assert mgr.end_if_current(a2) is False
    assert mgr.get("ob11", "g1") is b


@pytest.mark.asyncio
async def test_exit_active_does_not_swallow_a_newer_users_at(monkeypatch):
    """A 的 turn 决定收窗时,B 在 turn 期间 @bot 建的新窗口不能被一起弹掉。

    线上形状:A 的 turn 在飞 → B @bot(dispatcher 先 trigger 建新窗,再排进 pending)
    → A 回来带 should_exit_active=true → 按 key 收窗会让 B 的 @ 在 refire 入口蒸发,
    B 只看到一个 busy 表情。
    """
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.core.hermes_client import ChatResult
    from nonebot_plugin_hermes.handlers import message as handler_mod

    monkeypatch.setattr(plugin_config, "hermes_reactive_post_reply_cooldown_sec", 0)

    now = int(time.time() * 1000)
    _mcp.active_sessions.trigger("ob11", "g1", "user-A", now_ms=now)

    chat_args: list[dict] = []

    async def chat(**kwargs):
        chat_args.append(kwargs)
        # 第一发(A)决定收窗;refire(B)正常答
        exit_active = len(chat_args) == 1
        await asyncio.sleep(0.15)
        return ChatResult(
            raw_text="ok",
            media_urls=[],
            structured={"should_reply": True, "reply_text": "ok", "should_exit_active": exit_active},
            parse_failed=False,
            is_transport_error=False,
        )

    monkeypatch.setattr(handler_mod.hermes_client, "chat", chat)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    target = _FakeTarget(id="g1")
    bot = _fake_bot()

    t1 = asyncio.create_task(
        handler_mod._handle_reactive_path(
            bot=bot,
            target=target,
            adapter_name="ob11",
            user_id="user-A",
            group_id="g1",
            text="聊完了",
            image_urls=[],
            is_explicit_trigger=True,
            now_ms=now,
            event_msg_id=1001,
        )
    )
    await asyncio.sleep(0.01)
    # dispatcher 对显式触发的既有语义:先 trigger(换成全新实例),再进 reactive 路径
    _mcp.active_sessions.trigger("ob11", "g1", "user-B", now_ms=now + 1)
    await handler_mod._handle_reactive_path(
        bot=bot,
        target=target,
        adapter_name="ob11",
        user_id="user-B",
        group_id="g1",
        text="等下我还有个问题",
        image_urls=[],
        is_explicit_trigger=True,
        now_ms=now + 1,
        event_msg_id=1002,
    )
    await t1
    await asyncio.sleep(0.4)

    assert len(chat_args) == 2, f"B 的 @ 被 A 的 should_exit_active 吞了,chat 只调用了 {len(chat_args)} 次"
    assert chat_args[1].get("user_id") == "user-B"
