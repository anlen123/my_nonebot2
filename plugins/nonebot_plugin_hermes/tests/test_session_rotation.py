"""上游 compression 轮换 session 后,插件必须改用新 session id。

Hermes 的 /v1/chat/completions 在自动压缩时会轮换会话:旧 id 被置为
``ended_at=<t>, end_reason='compression'``,新建一个 continuation 子会话,新 id
放在**响应头** ``X-Hermes-Session-Id`` 里回传(api_server 注释:so callers can
track compression-triggered session rotations)。客户端不采纳的话,每轮都把会话
钉回已关闭的父会话:读仍能跟随 tip,写全部失败,而且每次压缩都从同一个父会话
再分叉一个兄弟——直到 live 子会话不止一个,上游的 find_live_compression_child()
判定歧义 fail-closed,该会话永久写不进去。
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pytest import MonkeyPatch

from nonebot_plugin_hermes.core.hermes_client import ChatResult, HermesClient
from nonebot_plugin_hermes.core.session import SessionManager
from nonebot_plugin_hermes.core.storage.session_key_store import SessionKeyStore
from nonebot_plugin_hermes.handlers.message import _run_passive_turn

_ROTATED = "20260518_190338_a03b4c"


class _MockResponse:
    def __init__(self, status_code: int, body: dict[str, Any], headers: dict[str, str] | None = None):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)
        self.headers = headers or {}

    def json(self) -> dict[str, Any]:
        return self._body


class _MockClient:
    def __init__(self, *, response: _MockResponse) -> None:
        self._response = response
        self.last_headers: dict[str, str] | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def post(self, url, *, json, headers):
        self.last_headers = headers
        return self._response


def _patch_httpx(monkeypatch: MonkeyPatch, response: _MockResponse) -> _MockClient:
    client = _MockClient(response=response)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: client)
    return client


async def _chat(session_key: str) -> ChatResult:
    return await HermesClient().chat(
        text="hello",
        session_key=session_key,
        user_id="u1",
        group_id="g1",
        adapter_name="ob11",
        is_private=False,
    )


# --- HermesClient:把响应头里的有效 session id 透出来 -----------------------


@pytest.mark.asyncio
async def test_chat_surfaces_rotated_session_id_from_response_header(monkeypatch: MonkeyPatch):
    body = {"choices": [{"message": {"content": "hi"}}]}
    _patch_httpx(monkeypatch, _MockResponse(200, body, {"X-Hermes-Session-Id": _ROTATED}))

    result = await _chat("hermes-ob11+group+g1+u1")

    assert result.effective_session_key == _ROTATED


@pytest.mark.asyncio
async def test_chat_reports_no_rotation_when_header_echoes_sent_key(monkeypatch: MonkeyPatch):
    """未压缩的普通一轮,上游原样回显请求里的 id —— 那不是轮换,不该触发采纳。"""
    body = {"choices": [{"message": {"content": "hi"}}]}
    sent = "hermes-ob11+group+g1+u1"
    _patch_httpx(monkeypatch, _MockResponse(200, body, {"X-Hermes-Session-Id": sent}))

    result = await _chat(sent)

    assert result.effective_session_key is None


@pytest.mark.asyncio
async def test_chat_reports_no_rotation_when_header_absent(monkeypatch: MonkeyPatch):
    """老版本 / 反代剥头时不能误判成轮换。"""
    body = {"choices": [{"message": {"content": "hi"}}]}
    _patch_httpx(monkeypatch, _MockResponse(200, body, {}))

    result = await _chat("hermes-ob11+group+g1+u1")

    assert result.effective_session_key is None


# --- SessionManager:采纳轮换后的 key ---------------------------------------


def test_adopt_session_key_redirects_subsequent_turns():
    sm = SessionManager()
    old = sm.get_session_key("ob11", False, "u1", "g1")

    assert sm.adopt_session_key(old, _ROTATED) is True

    assert sm.get_session_key("ob11", False, "u1", "g1") == _ROTATED


def test_adopt_session_key_ignores_unknown_previous_key():
    """/clear 与上游轮换撞在一起时,旧 key 已经不在映射里 —— 不能凭空建一条。"""
    sm = SessionManager()
    sm.get_session_key("ob11", False, "u1", "g1")

    assert sm.adopt_session_key("hermes-ob11+group+g9+u9", _ROTATED) is False

    assert sm.get_session_key("ob11", False, "u1", "g1") == "hermes-ob11+group+g1+u1"


def test_clear_session_after_adoption_starts_a_fresh_key():
    """/clear 语义不变:采纳过轮换 id 之后仍要能开新会话,且不退回旧 id。"""
    sm = SessionManager()
    old = sm.get_session_key("ob11", False, "u1", "g1")
    sm.adopt_session_key(old, _ROTATED)

    sm.clear_session("ob11", False, "u1", "g1")

    assert sm.get_session_key("ob11", False, "u1", "g1") == "hermes-ob11+group+g1+u1-g1"


# --- 持久化:重启后不能退回已关闭的父会话 -----------------------------------


def test_adopted_key_survives_restart(tmp_path):
    store = SessionKeyStore(db_path=tmp_path / "session_keys.db")
    sm = SessionManager()
    sm.bind_store(store)
    old = sm.get_session_key("ob11", False, "u1", "g1")
    sm.adopt_session_key(old, _ROTATED)

    restarted = SessionManager()
    restarted.bind_store(SessionKeyStore(db_path=tmp_path / "session_keys.db"))

    assert restarted.get_session_key("ob11", False, "u1", "g1") == _ROTATED


def test_generation_survives_restart(tmp_path):
    """/clear 之后重启,不能因为 generation 丢了而复活被清掉的会话。"""
    store = SessionKeyStore(db_path=tmp_path / "session_keys.db")
    sm = SessionManager()
    sm.bind_store(store)
    sm.get_session_key("ob11", False, "u1", "g1")
    sm.clear_session("ob11", False, "u1", "g1")

    restarted = SessionManager()
    restarted.bind_store(SessionKeyStore(db_path=tmp_path / "session_keys.db"))

    assert restarted.get_session_key("ob11", False, "u1", "g1") == "hermes-ob11+group+g1+u1-g1"
    restarted.clear_session("ob11", False, "u1", "g1")
    assert restarted.get_session_key("ob11", False, "u1", "g1") == "hermes-ob11+group+g1+u1-g2"


def test_unbound_session_manager_still_works():
    """store 未绑定(启动早期 / 测试)时退化成纯内存,不能抛。"""
    sm = SessionManager()
    old = sm.get_session_key("ob11", True, "u1")
    assert sm.adopt_session_key(old, _ROTATED) is True
    assert sm.get_session_key("ob11", True, "u1") == _ROTATED


# --- handler 接线:一轮结束就采纳,下一轮用新 key ---------------------------


@pytest.mark.asyncio
async def test_turn_adopts_rotation_so_next_turn_uses_new_key(monkeypatch: MonkeyPatch):
    from nonebot_plugin_hermes.core.session import session_manager

    bot, target = MagicMock(), MagicMock()
    target.id, target.private = "g1", False

    session_manager.clear_session("ob11", False, "u_rot", "g1")
    init_key = session_manager.get_session_key("ob11", False, "u_rot", "g1")

    chat_mock = AsyncMock(
        return_value=ChatResult(raw_text="回复", effective_session_key=_ROTATED),
    )
    monkeypatch.setattr("nonebot_plugin_hermes.handlers.message.hermes_client.chat", chat_mock)
    monkeypatch.setattr("nonebot_plugin_hermes.handlers.message.send_text_with_media", AsyncMock())

    await _run_passive_turn(
        bot=bot,
        target=target,
        adapter_name="ob11",
        user_id="u_rot",
        group_id="g1",
        is_private=False,
        text="hello",
        image_urls=[],
        now_ms=1000,
    )

    assert chat_mock.call_args.kwargs["session_key"] == init_key
    assert session_manager.get_session_key("ob11", False, "u_rot", "g1") == _ROTATED


# --- 启动接线:init_runtime_state 必须把持久化存储挂上 ------------------------


def test_startup_binds_session_key_store(tmp_path, monkeypatch: MonkeyPatch):
    """没接线的话持久化形同虚设:重启退回派生 key,又钉回已被压缩关闭的父会话。"""
    from nonebot_plugin_hermes import mcp as _mcp

    monkeypatch.setattr(_mcp.plugin_config, "hermes_storage_db_path", str(tmp_path / "messages.db"))
    monkeypatch.setattr(_mcp.plugin_config, "hermes_image_cache_dir", str(tmp_path / "images"))
    for name in ("message_store", "session_key_store", "image_cache", "image_fetcher", "message_buffer"):
        monkeypatch.setattr(_mcp, name, None)
    sm = SessionManager()
    monkeypatch.setattr(_mcp, "session_manager", sm)

    _mcp.init_runtime_state()

    old = sm.get_session_key("ob11", False, "u_boot", "g1")
    sm.adopt_session_key(old, _ROTATED)

    restarted = SessionManager()
    restarted.bind_store(SessionKeyStore(db_path=tmp_path / "session_keys.db"))
    assert restarted.get_session_key("ob11", False, "u_boot", "g1") == _ROTATED
