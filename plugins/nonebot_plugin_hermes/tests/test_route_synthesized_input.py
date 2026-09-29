"""验证 `route_synthesized_input` 的派发逻辑。

四个分支:
1. 私聊(target.private=True) + allow_passive=True → _handle_passive_path
2. 群 + active_session 开 → _handle_reactive_path (且触发 active session)
3. 群 + active_session 关 + allow_passive=True → _handle_passive_path
4. 群 + active_session 关 + allow_passive=False → 跳过(member_join 在 active 关时不开口)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from nonebot_plugin_hermes import mcp as _mcp
from nonebot_plugin_hermes.core.active_session import ActiveSessionManager
from nonebot_plugin_hermes.core.inflight import InflightRegistry


@dataclass
class _FakeTarget:
    id: str
    private: bool = False
    adapter: str = "ob11"


def _fake_bot(self_id: str = "999"):
    b = MagicMock()
    b.self_id = self_id
    return b


@pytest.fixture(autouse=True)
def _runtime():
    _mcp.active_sessions = ActiveSessionManager(default_ttl_sec=300)
    _mcp.inflight = InflightRegistry()
    yield
    _mcp.active_sessions = None
    _mcp.inflight = None


@pytest.mark.asyncio
async def test_private_with_allow_passive_routes_passive(monkeypatch):
    from nonebot_plugin_hermes.handlers import message as msg_mod

    passive_mock = AsyncMock()
    reactive_mock = AsyncMock()
    monkeypatch.setattr(msg_mod, "_handle_passive_path", passive_mock)
    monkeypatch.setattr(msg_mod, "_handle_reactive_path", reactive_mock)

    now = int(time.time() * 1000)
    await msg_mod.route_synthesized_input(
        bot=_fake_bot(),
        target=_FakeTarget(id="u1", private=True),
        adapter_name="OneBot V11",
        user_id="u1",
        group_id=None,
        nickname="Alice",
        text="[poke] 戳了你一下",
        addressed_to_bot=True,
        allow_passive=True,
        now_ms=now,
    )

    assert passive_mock.await_count == 1
    assert reactive_mock.await_count == 0
    kwargs = passive_mock.call_args.kwargs
    assert kwargs["text"] == "[poke] 戳了你一下"
    assert kwargs["is_private"] is True
    assert kwargs["nickname"] == "Alice"


@pytest.mark.asyncio
async def test_group_with_active_session_routes_reactive(monkeypatch):
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as msg_mod

    monkeypatch.setattr(plugin_config, "hermes_active_session_enabled", True)
    passive_mock = AsyncMock()
    reactive_mock = AsyncMock()
    monkeypatch.setattr(msg_mod, "_handle_passive_path", passive_mock)
    monkeypatch.setattr(msg_mod, "_handle_reactive_path", reactive_mock)

    now = int(time.time() * 1000)
    await msg_mod.route_synthesized_input(
        bot=_fake_bot(),
        target=_FakeTarget(id="g1", private=False),
        adapter_name="OneBot V11",
        user_id="u1",
        group_id="g1",
        nickname="Alice",
        text="[poke] 戳了你一下",
        addressed_to_bot=True,
        allow_passive=True,
        now_ms=now,
    )

    assert reactive_mock.await_count == 1
    assert passive_mock.await_count == 0
    assert _mcp.active_sessions.is_active("OneBot V11", "g1", now)
    kwargs = reactive_mock.call_args.kwargs
    assert kwargs["is_explicit_trigger"] is True
    assert kwargs["text"] == "[poke] 戳了你一下"


@pytest.mark.asyncio
async def test_group_active_off_with_allow_passive_routes_passive(monkeypatch):
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as msg_mod

    monkeypatch.setattr(plugin_config, "hermes_active_session_enabled", False)
    passive_mock = AsyncMock()
    reactive_mock = AsyncMock()
    monkeypatch.setattr(msg_mod, "_handle_passive_path", passive_mock)
    monkeypatch.setattr(msg_mod, "_handle_reactive_path", reactive_mock)

    now = int(time.time() * 1000)
    await msg_mod.route_synthesized_input(
        bot=_fake_bot(),
        target=_FakeTarget(id="g1", private=False),
        adapter_name="OneBot V11",
        user_id="u1",
        group_id="g1",
        nickname="Alice",
        text="[poke] 戳了你一下",
        addressed_to_bot=True,
        allow_passive=True,
        now_ms=now,
    )

    assert passive_mock.await_count == 1
    assert reactive_mock.await_count == 0


@pytest.mark.asyncio
async def test_group_active_off_disallow_passive_skips(monkeypatch):
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as msg_mod

    monkeypatch.setattr(plugin_config, "hermes_active_session_enabled", False)
    passive_mock = AsyncMock()
    reactive_mock = AsyncMock()
    monkeypatch.setattr(msg_mod, "_handle_passive_path", passive_mock)
    monkeypatch.setattr(msg_mod, "_handle_reactive_path", reactive_mock)

    now = int(time.time() * 1000)
    await msg_mod.route_synthesized_input(
        bot=_fake_bot(),
        target=_FakeTarget(id="g1", private=False),
        adapter_name="OneBot V11",
        user_id="u_new",
        group_id="g1",
        nickname="Newcomer",
        text="[event=member_join] Newcomer 加入了群",
        addressed_to_bot=True,
        allow_passive=False,
        now_ms=now,
    )

    assert passive_mock.await_count == 0
    assert reactive_mock.await_count == 0
