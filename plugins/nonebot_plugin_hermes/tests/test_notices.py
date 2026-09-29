"""验证 handlers/notices.py 的 OneBot v11 notice 分发逻辑。

每个 test 都把 `route_synthesized_input` 与 `get_adapter_name` mock 掉,
聚焦于 dispatch 选择正确 + 合成 text 正确。
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from nonebot_plugin_hermes import mcp as _mcp
from nonebot_plugin_hermes.core.active_session import ActiveSessionManager
from nonebot_plugin_hermes.core.inflight import InflightRegistry


def _make_poke_event(*, target_id, user_id, group_id):
    from nonebot.adapters.onebot.v11 import PokeNotifyEvent

    return PokeNotifyEvent(
        time=int(time.time()),
        self_id=999,
        post_type="notice",
        notice_type="notify",
        sub_type="poke",
        user_id=int(user_id),
        target_id=int(target_id),
        group_id=int(group_id) if group_id else None,
    )


def _make_join_event(*, user_id, group_id, sub_type="approve"):
    from nonebot.adapters.onebot.v11 import GroupIncreaseNoticeEvent

    return GroupIncreaseNoticeEvent(
        time=int(time.time()),
        self_id=999,
        post_type="notice",
        notice_type="group_increase",
        sub_type=sub_type,
        group_id=int(group_id),
        operator_id=999,
        user_id=int(user_id),
    )


@pytest.fixture(autouse=True)
def _runtime():
    _mcp.active_sessions = ActiveSessionManager(default_ttl_sec=300)
    _mcp.inflight = InflightRegistry()
    yield
    _mcp.active_sessions = None
    _mcp.inflight = None


@pytest.fixture
def _bot():
    b = MagicMock()
    b.self_id = "999"
    b.call_api = AsyncMock(return_value={"nickname": "Alice", "card": ""})
    return b


@pytest.fixture
def _route_mock(monkeypatch):
    """统一替换 route_synthesized_input + adapter_name=OneBot V11 + 关 isolation 限制。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import notices as nmod

    route_mock = AsyncMock()
    monkeypatch.setattr(nmod, "route_synthesized_input", route_mock)
    monkeypatch.setattr(nmod, "get_adapter_name", lambda _b: "onebotv11")
    monkeypatch.setattr(plugin_config, "hermes_allow_groups", set())
    monkeypatch.setattr(plugin_config, "hermes_allow_users", set())
    return route_mock


@pytest.mark.asyncio
async def test_both_switches_off_short_circuits(monkeypatch, _bot):
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import notices as nmod

    monkeypatch.setattr(plugin_config, "hermes_poke_trigger_enabled", False)
    monkeypatch.setattr(plugin_config, "hermes_greet_on_join", False)

    route_mock = AsyncMock()
    monkeypatch.setattr(nmod, "route_synthesized_input", route_mock)

    event = _make_poke_event(target_id=999, user_id=123, group_id=456)
    await nmod.dispatch(_bot, event)
    assert route_mock.await_count == 0


@pytest.mark.asyncio
async def test_poke_at_bot_in_group_routes(monkeypatch, _bot, _route_mock):
    from nonebot_plugin_hermes.config import plugin_config

    monkeypatch.setattr(plugin_config, "hermes_poke_trigger_enabled", True)

    event = _make_poke_event(target_id=999, user_id=123, group_id=456)
    from nonebot_plugin_hermes.handlers import notices as nmod

    await nmod.dispatch(_bot, event)

    assert _route_mock.await_count == 1
    kwargs = _route_mock.call_args.kwargs
    assert kwargs["text"] == "[poke] 戳了你一下"
    assert kwargs["allow_passive"] is True
    assert kwargs["user_id"] == "123"
    assert kwargs["group_id"] == "456"


@pytest.mark.asyncio
async def test_poke_in_private_routes(monkeypatch, _bot, _route_mock):
    from nonebot_plugin_hermes.config import plugin_config

    monkeypatch.setattr(plugin_config, "hermes_poke_trigger_enabled", True)

    event = _make_poke_event(target_id=999, user_id=123, group_id=None)
    from nonebot_plugin_hermes.handlers import notices as nmod

    await nmod.dispatch(_bot, event)

    assert _route_mock.await_count == 1
    kwargs = _route_mock.call_args.kwargs
    assert kwargs["group_id"] is None


@pytest.mark.asyncio
async def test_poke_at_other_user_skipped(monkeypatch, _bot, _route_mock):
    from nonebot_plugin_hermes.config import plugin_config

    monkeypatch.setattr(plugin_config, "hermes_poke_trigger_enabled", True)

    event = _make_poke_event(target_id=888, user_id=123, group_id=456)  # 888 != bot
    from nonebot_plugin_hermes.handlers import notices as nmod

    await nmod.dispatch(_bot, event)

    assert _route_mock.await_count == 0


@pytest.mark.asyncio
async def test_poke_disabled_skipped(monkeypatch, _bot, _route_mock):
    """poke_enabled=False (即使 greet_on_join=True) → poke 事件被忽略"""
    from nonebot_plugin_hermes.config import plugin_config

    monkeypatch.setattr(plugin_config, "hermes_poke_trigger_enabled", False)
    monkeypatch.setattr(plugin_config, "hermes_greet_on_join", True)

    event = _make_poke_event(target_id=999, user_id=123, group_id=456)
    from nonebot_plugin_hermes.handlers import notices as nmod

    await nmod.dispatch(_bot, event)

    assert _route_mock.await_count == 0


@pytest.mark.asyncio
async def test_member_join_with_greet_on_routes(monkeypatch, _bot, _route_mock):
    from nonebot_plugin_hermes.config import plugin_config

    monkeypatch.setattr(plugin_config, "hermes_greet_on_join", True)

    event = _make_join_event(user_id=777, group_id=456)
    from nonebot_plugin_hermes.handlers import notices as nmod

    await nmod.dispatch(_bot, event)

    assert _route_mock.await_count == 1
    kwargs = _route_mock.call_args.kwargs
    assert kwargs["text"].startswith("[event=member_join]")
    assert "Alice" in kwargs["text"]  # 来自 _bot.call_api mock
    assert kwargs["allow_passive"] is False
    assert kwargs["user_id"] == "777"


@pytest.mark.asyncio
async def test_member_join_greet_off_skipped(monkeypatch, _bot, _route_mock):
    from nonebot_plugin_hermes.config import plugin_config

    monkeypatch.setattr(plugin_config, "hermes_poke_trigger_enabled", True)
    monkeypatch.setattr(plugin_config, "hermes_greet_on_join", False)

    event = _make_join_event(user_id=777, group_id=456)
    from nonebot_plugin_hermes.handlers import notices as nmod

    await nmod.dispatch(_bot, event)

    assert _route_mock.await_count == 0


@pytest.mark.asyncio
async def test_bot_self_join_skipped(monkeypatch, _bot, _route_mock):
    """bot 被拉进新群,user_id == self_id → 跳过"""
    from nonebot_plugin_hermes.config import plugin_config

    monkeypatch.setattr(plugin_config, "hermes_greet_on_join", True)

    event = _make_join_event(user_id=999, group_id=456)
    from nonebot_plugin_hermes.handlers import notices as nmod

    await nmod.dispatch(_bot, event)

    assert _route_mock.await_count == 0


@pytest.mark.asyncio
async def test_non_onebot_adapter_skipped(monkeypatch, _bot):
    """adapter != OneBot V11 → 提前 return,根本不 import 适配器类型"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import notices as nmod

    monkeypatch.setattr(plugin_config, "hermes_poke_trigger_enabled", True)
    route_mock = AsyncMock()
    monkeypatch.setattr(nmod, "route_synthesized_input", route_mock)
    monkeypatch.setattr(nmod, "get_adapter_name", lambda _b: "Discord")

    event = MagicMock()
    await nmod.dispatch(_bot, event)
    assert route_mock.await_count == 0


@pytest.mark.asyncio
async def test_nickname_fallback_to_user_id(monkeypatch, _bot, _route_mock):
    """call_api 抛错时,合成 text 用 user_id 字符串作 nickname 兜底"""
    from nonebot_plugin_hermes.config import plugin_config

    monkeypatch.setattr(plugin_config, "hermes_greet_on_join", True)
    _bot.call_api = AsyncMock(side_effect=RuntimeError("network down"))

    event = _make_join_event(user_id=777, group_id=456)
    from nonebot_plugin_hermes.handlers import notices as nmod

    await nmod.dispatch(_bot, event)

    assert _route_mock.await_count == 1
    kwargs = _route_mock.call_args.kwargs
    assert "777" in kwargs["text"]


def test_notices_module_registered_on_handlers_import():
    """handlers/__init__.py import notices → notice_handler 已注册到 NoneBot。"""
    from nonebot_plugin_hermes import handlers as h_pkg
    from nonebot_plugin_hermes.handlers import notices as nmod

    assert hasattr(nmod, "notice_handler")
    assert "notices" in dir(h_pkg)
