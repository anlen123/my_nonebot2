"""Session 持久化失败:拦截、按 cause 决定是否重试、静默屏蔽。

上游三类 cause 的可恢复性不同(见 run_agent.py 的 session_persistence_failed 分支):
locked 是瞬时写锁冲突,上游明说「消息已存下,过一会再发一次」;disk / unknown
要人介入(清盘 / 改权限 / hermes doctor),重发也写不进去。
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest import MonkeyPatch

from nonebot_plugin_hermes.core.hermes_client import (
    ChatResult,
    HermesClient,
    classify_persistence_error,
    is_persistence_error_text,
)
from nonebot_plugin_hermes.core.session import session_manager
from nonebot_plugin_hermes.handlers.message import _run_passive_turn

_LOCKED_TEXT = (
    "No reply: the turn was stopped because session storage was busy "
    "(another Hermes process was writing to the state database). Your message should "
    "already be saved — please send it again in a moment."
)
_DISK_TEXT = (
    "No reply: the turn was stopped because session storage could not be written "
    "(the transcript would have been lost on restart). This is often a full disk — "
    "free some space (or fix state.db permissions), then send your message again."
)
_UNKNOWN_TEXT = (
    "No reply: the turn was stopped because session storage could not be written "
    "(the transcript would have been lost on restart). Check the state database health "
    "(`hermes doctor`), then send your message again."
)


def _err_result(text: str) -> ChatResult:
    return ChatResult(
        raw_text=text,
        parse_failed=True,
        is_transport_error=True,
        is_persistence_error=True,
        persistence_cause=classify_persistence_error(text),
    )


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch: MonkeyPatch):
    """重试补偿等待在测试里没有意义,置 0 免得每个用例真睡 1s。"""
    monkeypatch.setattr("nonebot_plugin_hermes.handlers.message._PERSISTENCE_RETRY_DELAY_S", 0)


class _MockResponse:
    def __init__(self, status_code: int, body: dict[str, Any], headers: dict[str, str] | None = None):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)
        # 真实 httpx.Response 一定带 headers;客户端要从 X-Hermes-Session-Id 读
        # 上游本轮实际使用的 session id(compression 轮换后会变)。
        self.headers = headers or {}

    def json(self) -> dict[str, Any]:
        return self._body


class _MockClient:
    def __init__(self, *, response: _MockResponse) -> None:
        self._response = response
        self.last_payload: dict[str, Any] | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def post(self, url, *, json, headers):
        self.last_payload = json
        return self._response


def test_is_persistence_error_text():
    normal = "你好,我是 Hermes AI 助手。"

    assert is_persistence_error_text(_UNKNOWN_TEXT) is True
    assert is_persistence_error_text(_LOCKED_TEXT) is True
    assert is_persistence_error_text(_DISK_TEXT) is True
    assert is_persistence_error_text(normal) is False
    assert is_persistence_error_text("") is False


def test_classify_persistence_error():
    assert classify_persistence_error(_LOCKED_TEXT) == "locked"
    assert classify_persistence_error(_DISK_TEXT) == "disk"
    assert classify_persistence_error(_UNKNOWN_TEXT) == "unknown"


@pytest.mark.asyncio
async def test_hermes_client_flags_persistence_error(monkeypatch: MonkeyPatch):
    err_text = _LOCKED_TEXT
    body = {"choices": [{"message": {"content": err_text}}]}

    def factory(*args: Any, **kwargs: Any) -> _MockClient:
        return _MockClient(response=_MockResponse(200, body))

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", factory)

    client = HermesClient()
    res = await client.chat(
        text="test",
        session_key="s1",
        user_id="u1",
        group_id="g1",
        adapter_name="ob11",
        is_private=False,
    )

    assert res.is_persistence_error is True
    assert res.is_transport_error is True
    assert res.parse_failed is True
    assert res.raw_text == err_text
    assert res.persistence_cause == "locked"


def _passive_bot_and_target():
    bot = MagicMock()
    target = MagicMock()
    target.id = "g1"
    target.private = False
    return bot, target


@pytest.mark.asyncio
async def test_locked_error_retries_with_same_session_key(monkeypatch: MonkeyPatch):
    """写锁冲突 → 用**原** session_key 重试一次。

    换 key 等于把整个群的 Hermes 侧上下文清零,而新 session 写的还是同一个
    state.db,治不了锁 —— 上游对这一类的指示就是「过一会再发一次」。
    """
    bot, target = _passive_bot_and_target()

    session_manager.clear_session("ob11", False, "u1", "g1")
    init_key = session_manager.get_session_key("ob11", False, "u1", "g1")

    success_result = ChatResult(raw_text="这是重试成功后的模型回复", media_urls=[])
    chat_mock = AsyncMock(side_effect=[_err_result(_LOCKED_TEXT), success_result])
    monkeypatch.setattr("nonebot_plugin_hermes.handlers.message.hermes_client.chat", chat_mock)

    send_mock = AsyncMock()
    monkeypatch.setattr("nonebot_plugin_hermes.handlers.message.send_text_with_media", send_mock)

    res = await _run_passive_turn(
        bot=bot,
        target=target,
        adapter_name="ob11",
        user_id="u1",
        group_id="g1",
        is_private=False,
        text="hello",
        image_urls=[],
        now_ms=1000,
    )

    assert chat_mock.call_count == 2
    assert chat_mock.call_args_list[0].kwargs["session_key"] == init_key
    assert chat_mock.call_args_list[1].kwargs["session_key"] == init_key, "重试不能换 session_key"
    assert session_manager.get_session_key("ob11", False, "u1", "g1") == init_key, "会话不该被重置"

    # 发给用户的是重试成功后的回复,不是上游报错原文
    send_mock.assert_called_once()
    assert send_mock.call_args.kwargs["text"] == "这是重试成功后的模型回复"
    assert res == success_result


@pytest.mark.asyncio
async def test_locked_error_retried_once_then_silenced(monkeypatch: MonkeyPatch):
    """重试后仍冲突:只补偿一次,报错原文静默屏蔽(fallback_text 为空时完全不发)。"""
    bot, target = _passive_bot_and_target()
    monkeypatch.setattr("nonebot_plugin_hermes.handlers.message.plugin_config.hermes_transport_error_fallback_text", "")

    err_result = _err_result(_LOCKED_TEXT)
    chat_mock = AsyncMock(side_effect=[err_result, err_result])
    monkeypatch.setattr("nonebot_plugin_hermes.handlers.message.hermes_client.chat", chat_mock)

    send_mock = AsyncMock()
    monkeypatch.setattr("nonebot_plugin_hermes.handlers.message.send_text_with_media", send_mock)

    res = await _run_passive_turn(
        bot=bot,
        target=target,
        adapter_name="ob11",
        user_id="u1",
        group_id="g1",
        is_private=False,
        text="hello",
        image_urls=[],
        now_ms=1000,
    )

    assert chat_mock.call_count == 2, "只补偿一次,不做多轮重试"
    send_mock.assert_not_called()
    assert res == err_result


@pytest.mark.parametrize("err_text", [_DISK_TEXT, _UNKNOWN_TEXT])
@pytest.mark.asyncio
async def test_unrecoverable_error_neither_retries_nor_resets_session(monkeypatch: MonkeyPatch, err_text: str):
    """磁盘满 / 库不健康:重发也写不进去,而持久化失败是在 turn 收尾判定的
    (工具已经跑过),白重试一次等于让副作用再来一遍。既不重试也不动 session。"""
    bot, target = _passive_bot_and_target()
    monkeypatch.setattr("nonebot_plugin_hermes.handlers.message.plugin_config.hermes_transport_error_fallback_text", "")

    session_manager.clear_session("ob11", False, "u1", "g1")
    init_key = session_manager.get_session_key("ob11", False, "u1", "g1")

    chat_mock = AsyncMock(side_effect=[_err_result(err_text)])
    monkeypatch.setattr("nonebot_plugin_hermes.handlers.message.hermes_client.chat", chat_mock)

    send_mock = AsyncMock()
    monkeypatch.setattr("nonebot_plugin_hermes.handlers.message.send_text_with_media", send_mock)

    await _run_passive_turn(
        bot=bot,
        target=target,
        adapter_name="ob11",
        user_id="u1",
        group_id="g1",
        is_private=False,
        text="hello",
        image_urls=[],
        now_ms=1000,
    )

    assert chat_mock.call_count == 1, "不可恢复的 cause 不该重试"
    assert session_manager.get_session_key("ob11", False, "u1", "g1") == init_key
    send_mock.assert_not_called()


@pytest.mark.asyncio
async def test_locked_error_after_rotation_retries_with_adopted_key(monkeypatch: MonkeyPatch):
    """同一轮里既发生上游轮换又撞上写锁:重试必须用上游回传的新 key。

    钉回已被 end_reason='compression' 关闭的父会话,写一定失败;而且第二次轮换
    回传的新 key 会因为 adopt_session_key(旧 key, …) 找不到映射而被丢弃,
    血缘从此对不上。采纳过就得把它带进重试。
    """
    bot, target = _passive_bot_and_target()

    session_manager.clear_session("ob11", False, "u1", "g1")
    init_key = session_manager.get_session_key("ob11", False, "u1", "g1")
    rotated_key = "hermes-compression-child-1"

    err = _err_result(_LOCKED_TEXT)
    err.effective_session_key = rotated_key
    success_result = ChatResult(raw_text="重试成功", media_urls=[])
    chat_mock = AsyncMock(side_effect=[err, success_result])
    monkeypatch.setattr("nonebot_plugin_hermes.handlers.message.hermes_client.chat", chat_mock)
    monkeypatch.setattr("nonebot_plugin_hermes.handlers.message.send_text_with_media", AsyncMock())

    await _run_passive_turn(
        bot=bot,
        target=target,
        adapter_name="ob11",
        user_id="u1",
        group_id="g1",
        is_private=False,
        text="hello",
        image_urls=[],
        now_ms=1000,
    )

    assert chat_mock.call_count == 2
    assert chat_mock.call_args_list[0].kwargs["session_key"] == init_key
    assert chat_mock.call_args_list[1].kwargs["session_key"] == rotated_key, "重试必须用采纳后的新 key"
    assert session_manager.get_session_key("ob11", False, "u1", "g1") == rotated_key
