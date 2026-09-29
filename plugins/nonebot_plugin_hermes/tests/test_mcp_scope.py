"""反向通道 scope:身份从接入点 key 认,范围从路由表派生。"""

from __future__ import annotations

import pytest

_TEAM_A = "key-team-a-at-least-16"
_TEAM_B = "key-team-b-at-least-16"
_GLOBAL = "key-global-at-least-16"


def _table(monkeypatch, table: dict):
    from nonebot_plugin_hermes.config import HermesEndpoint, plugin_config

    monkeypatch.setattr(plugin_config, "hermes_group_endpoints", {k: HermesEndpoint(**v) for k, v in table.items()})


def _two_profiles(monkeypatch):
    from nonebot_plugin_hermes.config import plugin_config

    monkeypatch.setattr(plugin_config, "hermes_api_key", _GLOBAL)
    _table(
        monkeypatch,
        {
            "ob11:g1": {"url": "http://h:8642/p/team-a", "key": _TEAM_A},
            "ob11:g2": {"url": "http://h:8642/p/team-a", "key": _TEAM_A},
            "ob11:g3": {"url": "http://h:8643", "key": _TEAM_B},
        },
    )


def test_endpoint_key_scopes_to_all_groups_it_serves(monkeypatch):
    from nonebot_plugin_hermes.core.routing import resolve_caller_scope

    _two_profiles(monkeypatch)
    scope = resolve_caller_scope(f"Bearer {_TEAM_A}")

    assert scope is not None
    assert scope.allows("ob11", "g1")
    assert scope.allows("ob11", "g2")
    assert not scope.allows("ob11", "g3")
    assert not scope.allows("ob11", "g9")


def test_global_key_gets_the_complement(monkeypatch):
    """全局 key = 默认接入点 = 补集,不是"不受限"。"""
    from nonebot_plugin_hermes.core.routing import resolve_caller_scope

    _two_profiles(monkeypatch)
    scope = resolve_caller_scope(f"Bearer {_GLOBAL}")

    assert scope is not None
    assert scope.allows("ob11", "g9")
    assert not scope.allows("ob11", "g1")
    assert not scope.allows("ob11", "g3")


def test_entry_without_own_key_falls_into_the_complement(monkeypatch):
    """条目沿用全局 key = 沿用默认接入点的权限,不构成独立隔离。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.core.routing import resolve_caller_scope

    monkeypatch.setattr(plugin_config, "hermes_api_key", _GLOBAL)
    _table(monkeypatch, {"ob11:g1": {"url": "http://h:8643"}})

    scope = resolve_caller_scope(f"Bearer {_GLOBAL}")
    assert scope is not None and scope.allows("ob11", "g1")


def test_empty_table_means_global_key_can_touch_everything(monkeypatch):
    """v0.5.0 行为等价性:没配路由表 = 补集是全部群。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.core.routing import resolve_caller_scope

    monkeypatch.setattr(plugin_config, "hermes_api_key", _GLOBAL)
    _table(monkeypatch, {})

    scope = resolve_caller_scope(f"Bearer {_GLOBAL}")
    assert scope is not None and scope.allows("ob11", "anything")


def test_unknown_token_and_bad_scheme_and_missing_header(monkeypatch):
    from nonebot_plugin_hermes.core.routing import resolve_caller_scope

    _two_profiles(monkeypatch)
    assert resolve_caller_scope("Bearer nope") is None
    assert resolve_caller_scope(f"Basic {_GLOBAL}") is None
    assert resolve_caller_scope(_GLOBAL) is None, "裸 token 没有 scheme,不接受"
    assert resolve_caller_scope(None) is None
    assert resolve_caller_scope("") is None
    assert resolve_caller_scope("Bearer ") is None


def test_trailing_whitespace_in_token_is_tolerated(monkeypatch):
    from nonebot_plugin_hermes.core.routing import resolve_caller_scope

    _two_profiles(monkeypatch)
    assert resolve_caller_scope(f"Bearer {_TEAM_A} ") is not None


def test_dev_mode_when_no_key_configured_anywhere(monkeypatch):
    """既没有全局 key 也没有条目 key —— 维持 v0.5.0 的开发模式(不校验)。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.core.routing import resolve_caller_scope

    monkeypatch.setattr(plugin_config, "hermes_api_key", "")
    _table(monkeypatch, {})

    for header in (None, "Bearer whatever"):
        scope = resolve_caller_scope(header)
        assert scope is not None and scope.allows("ob11", "g1")


def test_entry_keys_still_authenticate_without_a_global_key(monkeypatch):
    """全局 key 空但表内有 key:不是开发模式,未知 token 照样拒。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.core.routing import resolve_caller_scope

    monkeypatch.setattr(plugin_config, "hermes_api_key", "")
    _table(monkeypatch, {"ob11:g1": {"url": "http://h:8643", "key": _TEAM_B}})

    assert resolve_caller_scope("Bearer nope") is None
    scope = resolve_caller_scope(f"Bearer {_TEAM_B}")
    assert scope is not None and scope.allows("ob11", "g1")


def test_assert_scope_allows_refuses_unresolvable_scope():
    """None = 认不出,必须拒 —— 决不能回落成"不限"。"""
    from nonebot_plugin_hermes.mcp.auth import PushContextError, assert_scope_allows

    with pytest.raises(PushContextError):
        assert_scope_allows("ob11", "g1", None)


def test_assert_scope_allows_permits_and_blocks(monkeypatch):
    from nonebot_plugin_hermes.core.routing import resolve_caller_scope
    from nonebot_plugin_hermes.mcp.auth import PushContextError, assert_scope_allows

    _two_profiles(monkeypatch)
    scope = resolve_caller_scope(f"Bearer {_TEAM_A}")

    assert_scope_allows("ob11", "g1", scope)  # 不抛
    with pytest.raises(PushContextError):
        assert_scope_allows("ob11", "g3", scope)


def test_scope_describe_names_the_endpoint_and_groups(monkeypatch):
    """拒绝日志要能一眼看出是哪把 token、范围是什么。"""
    from nonebot_plugin_hermes.core.routing import resolve_caller_scope

    _two_profiles(monkeypatch)
    listed = resolve_caller_scope(f"Bearer {_TEAM_A}").describe()
    assert "team-a" in listed
    assert "ob11:g1" in listed

    complement = resolve_caller_scope(f"Bearer {_GLOBAL}").describe()
    assert "default" in complement


def test_describe_never_contains_the_token(monkeypatch):
    """describe() 进日志,不能带 token 本身。"""
    from nonebot_plugin_hermes.core.routing import resolve_caller_scope

    _two_profiles(monkeypatch)
    for header in (f"Bearer {_TEAM_A}", f"Bearer {_GLOBAL}"):
        assert _TEAM_A not in resolve_caller_scope(header).describe()
        assert _GLOBAL not in resolve_caller_scope(header).describe()


def test_isolated_labels_only_counts_entries_with_own_key(monkeypatch):
    from nonebot_plugin_hermes.core.routing import isolated_labels

    _two_profiles(monkeypatch)
    assert isolated_labels() == {"ob11:g1", "ob11:g2", "ob11:g3"}

    _table(monkeypatch, {"ob11:g1": {"url": "http://h:8643"}})
    assert isolated_labels() == frozenset()


# --- 四个工具的收敛面 ------------------------------------------------------
#
# 少一个就是绕行口:push 是写,recent/images 是读,list 是发现(枚举别群本身就是泄露)。


class _FakePushTarget:
    private = False


@pytest.mark.asyncio
async def test_push_message_refuses_out_of_scope_group(monkeypatch):
    from nonebot_plugin_hermes.core.active_session import ActiveSessionManager
    from nonebot_plugin_hermes.core.bot_registry import BotRegistry
    from nonebot_plugin_hermes.core.routing import resolve_caller_scope
    from nonebot_plugin_hermes.mcp.tools.push_message import PushMessageInput, push_message_impl

    _two_profiles(monkeypatch)
    scope = resolve_caller_scope(f"Bearer {_TEAM_A}")

    am = ActiveSessionManager(default_ttl_sec=600)
    br = BotRegistry()
    # g3 完全就绪(活跃 + 有路由),唯一不满足的是它不属于这把 token。
    am.trigger("ob11", "g3", "u1", now_ms=0)
    br.upsert("ob11", "group", "g3", "bot", _FakePushTarget(), ts=0)

    result = await push_message_impl(
        PushMessageInput(adapter="ob11", group_id="g3", text="hi"),
        active_sessions=am,
        bot_registry=br,
        scope=scope,
    )

    assert result.ok is False
    assert "not authorized" in (result.error or "")


@pytest.mark.asyncio
async def test_get_recent_messages_refuses_out_of_scope_group(monkeypatch):
    """越权走返回值里的 error,**不抛异常** —— FastMCP 会把任何异常打成 traceback,
    而越权是预期内的判定结果,不该在 bot 日志里刷崩溃栈。"""
    from nonebot_plugin_hermes.core.routing import resolve_caller_scope
    from nonebot_plugin_hermes.mcp.tools.get_recent_messages import (
        GetRecentMessagesInput,
        get_recent_messages_impl,
    )

    _two_profiles(monkeypatch)
    scope = resolve_caller_scope(f"Bearer {_TEAM_A}")

    class _Buffer:
        def get_recent(self, **kwargs):  # pragma: no cover - 不该被调用到
            raise AssertionError("越权调用不该走到取数")

    result = await get_recent_messages_impl(
        GetRecentMessagesInput(adapter="ob11", group_id="g3"),
        message_buffer=_Buffer(),
        scope=scope,
    )

    assert result.messages == []
    assert result.error is not None
    assert "not authorized" in result.error
    assert "do not retry" in result.error


@pytest.mark.asyncio
async def test_get_recent_messages_refusal_does_not_leak_other_groups(monkeypatch):
    """回给调用方的话里不能出现别的群 —— 那是别人的路由配置。"""
    from nonebot_plugin_hermes.core.routing import resolve_caller_scope
    from nonebot_plugin_hermes.mcp.tools.get_recent_messages import (
        GetRecentMessagesInput,
        get_recent_messages_impl,
    )

    _two_profiles(monkeypatch)
    # 补集 scope(全局 key):它的 describe() 会列出被排除的群,describe_for_caller() 不能。
    scope = resolve_caller_scope(f"Bearer {_GLOBAL}")

    class _Buffer:
        def get_recent(self, **kwargs):  # pragma: no cover
            raise AssertionError("越权调用不该走到取数")

    result = await get_recent_messages_impl(
        GetRecentMessagesInput(adapter="ob11", group_id="g1"),
        message_buffer=_Buffer(),
        scope=scope,
    )

    assert result.error is not None
    for other in ("g2", "g3"):
        assert f"ob11:{other}" not in result.error
    # 被拒的那个群本身可以出现(调用方自己传的)
    assert "g1" in result.error


def test_describe_for_caller_never_lists_other_groups(monkeypatch):
    from nonebot_plugin_hermes.core.routing import resolve_caller_scope

    _two_profiles(monkeypatch)
    complement = resolve_caller_scope(f"Bearer {_GLOBAL}").describe_for_caller()
    for label in ("ob11:g1", "ob11:g2", "ob11:g3"):
        assert label not in complement

    listed = resolve_caller_scope(f"Bearer {_TEAM_A}").describe_for_caller()
    assert "ob11:g1" in listed and "ob11:g2" in listed
    assert "ob11:g3" not in listed


@pytest.mark.asyncio
async def test_get_recent_messages_allows_own_group(monkeypatch):
    from nonebot_plugin_hermes.core.routing import resolve_caller_scope
    from nonebot_plugin_hermes.mcp.tools.get_recent_messages import (
        GetRecentMessagesInput,
        get_recent_messages_impl,
    )

    _two_profiles(monkeypatch)
    scope = resolve_caller_scope(f"Bearer {_TEAM_A}")

    class _Buffer:
        def get_recent(self, **kwargs):
            return []

    result = await get_recent_messages_impl(
        GetRecentMessagesInput(adapter="ob11", group_id="g1"),
        message_buffer=_Buffer(),
        scope=scope,
    )
    assert result.messages == []


@pytest.mark.asyncio
async def test_list_active_sessions_filters_to_scope(monkeypatch):
    from nonebot_plugin_hermes.core.active_session import ActiveSessionManager
    from nonebot_plugin_hermes.core.routing import resolve_caller_scope
    from nonebot_plugin_hermes.mcp.tools.list_active_sessions import (
        ListActiveSessionsInput,
        list_active_sessions_impl,
    )

    _two_profiles(monkeypatch)
    scope = resolve_caller_scope(f"Bearer {_TEAM_A}")

    am = ActiveSessionManager(default_ttl_sec=600)
    for gid in ("g1", "g2", "g3", "g9"):
        am.trigger("ob11", gid, "u1", now_ms=0)

    result = await list_active_sessions_impl(
        ListActiveSessionsInput(adapter=None),
        active_sessions=am,
        scope=scope,
        now_ms=1_000,
    )

    assert {v.group_id for v in result.sessions} == {"g1", "g2"}


@pytest.mark.asyncio
async def test_list_active_sessions_empty_for_unresolvable_caller(monkeypatch):
    from nonebot_plugin_hermes.core.active_session import ActiveSessionManager
    from nonebot_plugin_hermes.mcp.tools.list_active_sessions import (
        ListActiveSessionsInput,
        list_active_sessions_impl,
    )

    _two_profiles(monkeypatch)
    am = ActiveSessionManager(default_ttl_sec=600)
    am.trigger("ob11", "g1", "u1", now_ms=0)

    result = await list_active_sessions_impl(
        ListActiveSessionsInput(adapter=None),
        active_sessions=am,
        scope=None,
        now_ms=1_000,
    )
    assert result.sessions == []


@pytest.mark.asyncio
async def test_get_message_images_hides_out_of_scope_ids(monkeypatch, tmp_path):
    """按 message_id 取图的工具:归属只能从 DB 反查,越权 id 按 not_found 处理。"""
    from nonebot_plugin_hermes.core.message_buffer import BufferedMessage
    from nonebot_plugin_hermes.core.routing import resolve_caller_scope
    from nonebot_plugin_hermes.core.storage.image_cache import ImageCache
    from nonebot_plugin_hermes.core.storage.message_store import MessageStore
    from nonebot_plugin_hermes.mcp.tools.get_message_images import (
        GetMessageImagesInput,
        get_message_images_impl,
    )

    _two_profiles(monkeypatch)
    scope = resolve_caller_scope(f"Bearer {_TEAM_A}")

    store = MessageStore(db_path=tmp_path / "m.db")
    cache = ImageCache(cache_dir=tmp_path / "imgs", quota_bytes=1024 * 1024)
    try:
        ids = {}
        for gid in ("g1", "g3"):
            msg = BufferedMessage(
                ts=100,
                adapter="ob11",
                group_id=gid,
                user_id="u1",
                nickname="u1",
                content="x",
                image_urls=[f"http://x/{gid}.jpg"],
            )
            store.append(msg)
            sha = cache.put(b"\xff\xd8\xff\xe0FAKE" + gid.encode(), "image/jpeg")
            store.update_image_sha(msg.id, 0, sha, "image/jpeg")
            ids[gid] = msg.id

        import json

        result = await get_message_images_impl(
            GetMessageImagesInput(message_ids=[ids["g1"], ids["g3"]]),
            store=store,
            cache=cache,
            scope=scope,
        )
        header = json.loads(result[0].text)
        by_id = {row["message_id"]: row for row in header["results"]}

        assert by_id[ids["g1"]]["available"] is True
        assert by_id[ids["g3"]]["available"] is False
        assert by_id[ids["g3"]]["reason"] == "not_found", "不能给越权方一个'这个 id 存在'的探测口"
        # 越权那张图的字节一定不在返回里:只应有 g1 那一个 ImageContent
        assert sum(1 for b in result if getattr(b, "type", "") == "image") == 1
    finally:
        store.close()


def test_get_message_owners_resolves_adapter_and_group(tmp_path):
    from nonebot_plugin_hermes.core.message_buffer import BufferedMessage
    from nonebot_plugin_hermes.core.storage.message_store import MessageStore

    store = MessageStore(db_path=tmp_path / "m.db")
    try:
        msg = BufferedMessage(
            ts=1, adapter="ob11", group_id="g1", user_id="u1", nickname="u1", content="x", image_urls=[]
        )
        store.append(msg)
        owners = store.get_message_owners([msg.id, msg.id + 999])

        assert owners[msg.id] == ("ob11", "g1")
        assert msg.id + 999 not in owners, "未找到的 id 不出现在返回里"
        assert store.get_message_owners([]) == {}
    finally:
        store.close()
