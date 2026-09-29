"""Unit tests for mcp/tools/list_active_sessions.py and get_recent_messages.py.

Uses real ActiveSessionManager / MessageBuffer — no mocking needed since
both tools are pure read functions that accept manager objects as kwargs.
"""

from __future__ import annotations

import pytest

from nonebot_plugin_hermes.core.active_session import ActiveSessionManager
from nonebot_plugin_hermes.core.message_buffer import BufferedMessage, MessageBuffer
from nonebot_plugin_hermes.core.routing import CallerScope
from nonebot_plugin_hermes.core.storage.image_cache import ImageCache
from nonebot_plugin_hermes.core.storage.image_fetcher import ImageFetcher
from nonebot_plugin_hermes.core.storage.message_store import MessageStore
from nonebot_plugin_hermes.mcp.tools.get_recent_messages import (
    GetRecentMessagesInput,
    GetRecentMessagesResult,
    RecentMessageView,
    get_recent_messages_impl,
)
from nonebot_plugin_hermes.mcp.tools.list_active_sessions import (
    ActiveSessionView,
    ListActiveSessionsInput,
    ListActiveSessionsResult,
    list_active_sessions_impl,
)

# 这些用例测的是工具自身的行为,不是反向通道的范围收敛(那在 test_mcp_scope.py),
# 所以统一给开发模式 scope。scope 是必填参数:漏传是 TypeError 而不是静默放行。
_ANY_SCOPE = CallerScope.dev()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session_mgr(*entries) -> ActiveSessionManager:
    """Build an ActiveSessionManager with the given (adapter, group_id, user_id) tuples.

    All sessions trigger at now_ms=0 → expires_at=300_000ms. Tests passing
    now_ms < 300_000 to list_active_sessions_impl will see them as active.
    """
    mgr = ActiveSessionManager(default_ttl_sec=300)
    for adapter, group_id, user_id in entries:
        mgr.trigger(adapter, group_id, user_id, now_ms=0)
    return mgr


# 共享时间锚:tests 中所有 list_active_sessions_impl 调用使用此值,
# 既小于 _make_session_mgr 的 expires_at=300_000,又能让"已过期"测试用 400_000 区分
_NOW_MS = 1_000  # 1s 后,session 仍在 5min TTL 内


def _msg(
    ts: int,
    adapter: str = "ob11",
    group_id: str = "g1",
    user_id: str = "u1",
    content: str = "hello",
    image_urls: list[str] | None = None,
    is_bot: bool = False,
) -> BufferedMessage:
    return BufferedMessage(
        ts=ts,
        adapter=adapter,
        group_id=group_id,
        user_id=user_id,
        nickname=user_id,
        content=content,
        image_urls=image_urls or [],
        reply_to_ts=None,
        is_bot=is_bot,
    )


@pytest.fixture
def buffer_factory(tmp_path):
    """每次调用建一个 SQLite-backed MessageBuffer,fixture cleanup 关 DB。"""
    created: list[MessageStore] = []

    def factory(*msgs: BufferedMessage) -> MessageBuffer:
        idx = len(created)
        store = MessageStore(db_path=tmp_path / f"m_{idx}.db")
        cache = ImageCache(cache_dir=tmp_path / f"imgs_{idx}", quota_bytes=1024 * 1024)
        fetcher = ImageFetcher(store=store, cache=cache)
        created.append(store)
        buf = MessageBuffer(store=store, fetcher=fetcher)
        for m in msgs:
            buf.append(m)
        return buf

    yield factory
    for s in created:
        s.close()


# ---------------------------------------------------------------------------
# list_active_sessions_impl tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_active_sessions_empty():
    """Returns empty list when no sessions exist."""
    mgr = ActiveSessionManager(default_ttl_sec=300)
    inp = ListActiveSessionsInput(adapter=None)
    result = await list_active_sessions_impl(inp, active_sessions=mgr, scope=_ANY_SCOPE)
    assert isinstance(result, ListActiveSessionsResult)
    assert result.sessions == []


@pytest.mark.asyncio
async def test_list_active_sessions_no_filter_returns_all():
    """With adapter=None, returns all sessions regardless of adapter."""
    mgr = _make_session_mgr(("ob11", "g1", "u1"), ("kook", "g2", "u2"), ("ob11", "g3", "u3"))
    inp = ListActiveSessionsInput(adapter=None)
    result = await list_active_sessions_impl(inp, active_sessions=mgr, now_ms=_NOW_MS, scope=_ANY_SCOPE)
    assert isinstance(result, ListActiveSessionsResult)
    assert len(result.sessions) == 3
    group_ids = {v.group_id for v in result.sessions}
    assert group_ids == {"g1", "g2", "g3"}


@pytest.mark.asyncio
async def test_list_active_sessions_with_adapter_filter():
    """With adapter='ob11', returns only ob11 sessions."""
    mgr = _make_session_mgr(("ob11", "g1", "u1"), ("kook", "g2", "u2"), ("ob11", "g3", "u3"))
    inp = ListActiveSessionsInput(adapter="ob11")
    result = await list_active_sessions_impl(inp, active_sessions=mgr, now_ms=_NOW_MS, scope=_ANY_SCOPE)
    assert len(result.sessions) == 2
    for v in result.sessions:
        assert v.adapter == "ob11"


@pytest.mark.asyncio
async def test_list_active_sessions_view_fields_match_session():
    """ActiveSessionView fields are correctly mapped from ActiveSession."""
    mgr = ActiveSessionManager(default_ttl_sec=300)
    session = mgr.trigger("ob11", "g1", "u42", now_ms=5_000, topic_hint="rust async")
    inp = ListActiveSessionsInput(adapter=None)
    result = await list_active_sessions_impl(inp, active_sessions=mgr, now_ms=10_000, scope=_ANY_SCOPE)
    assert len(result.sessions) == 1
    v = result.sessions[0]
    assert isinstance(v, ActiveSessionView)
    assert v.adapter == session.adapter
    assert v.group_id == session.group_id
    assert v.triggered_by == session.triggered_by
    assert v.started_at == session.started_at
    assert v.last_active_at == session.last_active_at
    assert v.expires_at == session.expires_at
    assert v.topic_hint == "rust async"


@pytest.mark.asyncio
async def test_list_active_sessions_topic_hint_none_allowed():
    """topic_hint=None is valid (str | None field)."""
    mgr = ActiveSessionManager(default_ttl_sec=300)
    mgr.trigger("ob11", "g1", "u1", now_ms=0, topic_hint=None)
    inp = ListActiveSessionsInput(adapter=None)
    result = await list_active_sessions_impl(inp, active_sessions=mgr, now_ms=_NOW_MS, scope=_ANY_SCOPE)
    assert result.sessions[0].topic_hint is None


@pytest.mark.asyncio
async def test_list_active_sessions_filters_expired():
    """Expired sessions(expires_at <= now_ms)不应出现在 view——保持与
    push_message 的 validate_push_context 同口径。Task 16 cron sweep 之前的
    陈旧条目对外不可见。"""
    mgr = ActiveSessionManager(default_ttl_sec=300)
    # session A 在 now_ms=0 触发,expires_at=300_000ms
    mgr.trigger("ob11", "alive", "u1", now_ms=0)
    # session B 触发但已过期(注:无法直接构造,改为推时间)
    mgr.trigger("ob11", "dying", "u2", now_ms=0)

    inp = ListActiveSessionsInput(adapter=None)

    # 推 now_ms 到 400_000(>300_000):两个 session 都应被过滤
    result = await list_active_sessions_impl(inp, active_sessions=mgr, now_ms=400_000, scope=_ANY_SCOPE)
    assert result.sessions == []

    # 推到 200_000(<300_000):两个都活
    result = await list_active_sessions_impl(inp, active_sessions=mgr, now_ms=200_000, scope=_ANY_SCOPE)
    assert len(result.sessions) == 2


# ---------------------------------------------------------------------------
# get_recent_messages_impl tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_recent_messages_empty_bucket(buffer_factory):
    """Returns empty list for unknown (adapter, group_id)."""
    buf = buffer_factory()
    inp = GetRecentMessagesInput(adapter="ob11", group_id="g_unknown")
    result = await get_recent_messages_impl(inp, message_buffer=buf, scope=_ANY_SCOPE)
    assert isinstance(result, GetRecentMessagesResult)
    assert result.messages == []


@pytest.mark.asyncio
async def test_get_recent_messages_returns_newest_first(buffer_factory):
    """Messages are returned newest-first (matching MessageBuffer.get_recent ordering)."""
    buf = buffer_factory(_msg(100), _msg(200), _msg(300))
    inp = GetRecentMessagesInput(adapter="ob11", group_id="g1", limit=10)
    result = await get_recent_messages_impl(inp, message_buffer=buf, scope=_ANY_SCOPE)
    assert [v.ts for v in result.messages] == [300, 200, 100]


@pytest.mark.asyncio
async def test_get_recent_messages_limit_clamped_to_config_cap(monkeypatch, buffer_factory):
    """limit is clamped to hermes_mcp_recent_limit_max even when Pydantic max (100) allows more.

    We monkeypatch plugin_config.hermes_mcp_recent_limit_max to 3 and verify
    that a request with limit=100 only returns 3 messages.
    """
    import nonebot_plugin_hermes.mcp.tools.get_recent_messages as mod

    buf = buffer_factory(*[_msg(ts) for ts in range(1, 11)])  # 10 messages
    monkeypatch.setattr(mod.plugin_config, "hermes_mcp_recent_limit_max", 3)
    inp = GetRecentMessagesInput(adapter="ob11", group_id="g1", limit=100)
    result = await get_recent_messages_impl(inp, message_buffer=buf, scope=_ANY_SCOPE)
    assert len(result.messages) == 3


@pytest.mark.asyncio
async def test_get_recent_messages_before_ts_filter(buffer_factory):
    """before_ts is passed through to MessageBuffer.get_recent (exclusive upper bound)."""
    buf = buffer_factory(_msg(100), _msg(200), _msg(300), _msg(400))
    inp = GetRecentMessagesInput(adapter="ob11", group_id="g1", limit=10, before_ts=300)
    result = await get_recent_messages_impl(inp, message_buffer=buf, scope=_ANY_SCOPE)
    # Should only include ts < 300: ts=200, ts=100
    assert [v.ts for v in result.messages] == [200, 100]


@pytest.mark.asyncio
async def test_get_recent_messages_view_fields_mapped_correctly(buffer_factory):
    """RecentMessageView fields are correctly mapped from BufferedMessage."""
    buf = buffer_factory(
        _msg(
            ts=999,
            adapter="ob11",
            group_id="g1",
            user_id="u42",
            content="hello world",
            image_urls=["https://example.com/img.png"],
            is_bot=False,
        )
    )
    inp = GetRecentMessagesInput(adapter="ob11", group_id="g1", limit=1)
    result = await get_recent_messages_impl(inp, message_buffer=buf, scope=_ANY_SCOPE)
    assert len(result.messages) == 1
    v = result.messages[0]
    assert isinstance(v, RecentMessageView)
    assert v.ts == 999
    assert v.user_id == "u42"
    assert v.nickname == "u42"
    assert v.content == "hello world"
    # 新结构:image_urls 字段不再外露,只暴露 count
    assert v.image_count == 1
    assert not hasattr(v, "image_urls")
    assert v.is_bot is False


@pytest.mark.asyncio
async def test_get_recent_messages_id_field_populated(buffer_factory):
    """id 字段必填(来自 MessageStore.append 回填的 autoincrement)。"""
    buf = buffer_factory(_msg(100), _msg(200))
    inp = GetRecentMessagesInput(adapter="ob11", group_id="g1", limit=10)
    result = await get_recent_messages_impl(inp, message_buffer=buf, scope=_ANY_SCOPE)
    assert all(v.id > 0 for v in result.messages)
    # 两条消息 id 互不相同
    assert result.messages[0].id != result.messages[1].id


@pytest.mark.asyncio
async def test_get_recent_messages_image_count_is_length(buffer_factory):
    """image_count 准确反映多图消息中的图数。"""
    buf = buffer_factory(
        _msg(ts=100, image_urls=["http://x/a.jpg", "http://x/b.jpg", "http://x/c.jpg"]),
        _msg(ts=200, image_urls=[]),
    )
    inp = GetRecentMessagesInput(adapter="ob11", group_id="g1", limit=10)
    result = await get_recent_messages_impl(inp, message_buffer=buf, scope=_ANY_SCOPE)
    by_ts = {v.ts: v for v in result.messages}
    assert by_ts[100].image_count == 3
    assert by_ts[200].image_count == 0
