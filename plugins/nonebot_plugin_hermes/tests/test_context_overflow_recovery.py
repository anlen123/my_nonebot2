"""上下文溢出 + 上游自动压缩已关:识别、换 session key 重发一次、群里明说一句。

上游 `compression.enabled: false` 时,`agent/turn_recovery.py` 撞到上下文溢出会**放弃**
—— 禁掉包括会话轮换在内的所有自动恢复路径,直接把一句英文提示当作 `final_response`
返回。`gateway/platforms/api_server.py` 又把它原样塞进 `{"role":"assistant","content":…}`
以 HTTP 200 发回,所以插件看到的是一条"完全正常的助手回复"。

不管的话有两个后果:那句英文被当成模型发言转发进群(reactive 路解不出 JSON 会落到
pure-prose 兜底),以及窗口只涨不缩 —— 下一轮更大,该会话从此永久卡死。

换 session key 是对症的唯一手段:旧窗口本身就是病因。这一点与持久化失败**正好相反**
(那一类刻意不换 key —— 换了治不了锁也治不了满盘,却白白清空整群上下文)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest import MonkeyPatch

from nonebot_plugin_hermes import mcp as _mcp
from nonebot_plugin_hermes.core.active_session import ActiveSessionManager
from nonebot_plugin_hermes.core.bot_registry import BotRegistry
from nonebot_plugin_hermes.core.hermes_client import (
    ChatResult,
    HermesClient,
    is_context_overflow_text,
    is_persistence_error_text,
)
from nonebot_plugin_hermes.core.inflight import InflightRegistry
from nonebot_plugin_hermes.core.message_buffer import MessageBuffer
from nonebot_plugin_hermes.core.session import session_manager
from nonebot_plugin_hermes.core.storage.image_cache import ImageCache
from nonebot_plugin_hermes.core.storage.image_fetcher import ImageFetcher
from nonebot_plugin_hermes.core.storage.message_store import MessageStore
from nonebot_plugin_hermes.handlers.message import (
    _SESSION_RESET_NOTICE,
    _run_passive_turn,
    _run_reactive_turn,
)

# 上游 agent/turn_recovery.py 在 compression.enabled: false 下返回的 final_response 原文。
_OVERFLOW_TEXT = (
    "Context overflow and auto-compaction is disabled "
    "(compression.enabled: false). Run /compress to compact manually, "
    "/new to start fresh, or switch to a larger-context model."
)

# 同为「200 外壳 + 错误正文」但属于另一类,不能互相误判。
_PERSISTENCE_TEXT = (
    "No reply: the turn was stopped because session storage was busy "
    "(another Hermes process was writing to the state database). Your message should "
    "already be saved — please send it again in a moment."
)


def _overflow_result() -> ChatResult:
    return ChatResult(
        raw_text=_OVERFLOW_TEXT,
        parse_failed=True,
        is_transport_error=True,
        is_context_overflow=True,
    )


@dataclass
class _FakeTarget:
    id: str
    private: bool = False
    adapter: str = "ob11"


@pytest.fixture
def _runtime(tmp_path):
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


def _fake_bot():
    bot = MagicMock()
    bot.self_id = "999"
    return bot


# ── 识别 ────────────────────────────────────────────────────────────────────


def test_upstream_overflow_notice_is_recognized():
    assert is_context_overflow_text(_OVERFLOW_TEXT) is True


def test_empty_text_is_not_an_overflow_notice():
    assert is_context_overflow_text("") is False


def test_ordinary_reply_is_not_an_overflow_notice():
    assert is_context_overflow_text("你好,我是 Hermes AI 助手。") is False


def test_reply_merely_discussing_overflow_is_not_one():
    """群友聊到这个话题时 bot 的正经回答不能被误判 —— 误判的代价是清空整条会话。

    锚点必须落在开头:上游那段是整条回复的全文,而解释性的回答绝不会以它开头。
    """
    chatty = (
        "你说的那个报错叫 context overflow,意思是上下文超了模型窗口;"
        "如果 auto-compaction is disabled,就只能手动压缩或者开新会话喵~"
    )

    assert is_context_overflow_text(chatty) is False


def test_persistence_notice_is_not_classified_as_overflow():
    assert is_context_overflow_text(_PERSISTENCE_TEXT) is False


def test_overflow_notice_is_not_classified_as_persistence():
    assert is_persistence_error_text(_OVERFLOW_TEXT) is False


class _MockResponse:
    def __init__(self, status_code: int, body: dict[str, Any]):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)
        self.headers: dict[str, str] = {}

    def json(self) -> dict[str, Any]:
        return self._body


class _MockClient:
    def __init__(self, *, response: _MockResponse) -> None:
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def post(self, url, *, json, headers):
        return self._response


@pytest.mark.asyncio
async def test_client_flags_the_overflow_notice(monkeypatch: MonkeyPatch):
    """标成 transport_error + parse_failed,好让既有的静默兜底一并接住它。"""
    import httpx

    body = {"choices": [{"message": {"content": _OVERFLOW_TEXT}}]}
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _MockClient(response=_MockResponse(200, body)))

    res = await HermesClient().chat(
        text="test", session_key="s1", user_id="u1", group_id="g1", adapter_name="ob11", is_private=False
    )

    assert res.is_context_overflow is True
    assert res.is_transport_error is True
    assert res.parse_failed is True


# ── 兜底(passive 路)──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_overflow_rotates_the_session_key_and_retries(monkeypatch: MonkeyPatch):
    """旧窗口就是病因 —— 换 key 重发一次。"""
    session_manager.clear_session("ob11", False, "u_rot", "g_rot")
    stale_key = session_manager.get_session_key("ob11", False, "u_rot", "g_rot")

    answer = ChatResult(raw_text="重开之后的回答")
    chat_mock = AsyncMock(side_effect=[_overflow_result(), answer])
    monkeypatch.setattr("nonebot_plugin_hermes.handlers.message.hermes_client.chat", chat_mock)
    monkeypatch.setattr("nonebot_plugin_hermes.handlers.message.send_text_with_media", AsyncMock())

    res = await _run_passive_turn(
        bot=MagicMock(),
        target=_FakeTarget(id="g_rot"),
        adapter_name="ob11",
        user_id="u_rot",
        group_id="g_rot",
        is_private=False,
        text="hello",
        image_urls=[],
        now_ms=1000,
    )

    fresh_key = session_manager.get_session_key("ob11", False, "u_rot", "g_rot")
    assert chat_mock.call_count == 2
    assert chat_mock.call_args_list[0].kwargs["session_key"] == stale_key
    assert chat_mock.call_args_list[1].kwargs["session_key"] == fresh_key
    assert fresh_key != stale_key, "必须换掉 key,否则重发的还是那个撑爆的窗口"
    assert res == answer


@pytest.mark.asyncio
async def test_reset_is_announced_before_the_retried_answer(monkeypatch: MonkeyPatch):
    """先说再答:bot 突然接不上前文若不解释,会被当成变傻了。"""
    session_manager.clear_session("ob11", False, "u_say", "g_say")

    chat_mock = AsyncMock(side_effect=[_overflow_result(), ChatResult(raw_text="重开之后的回答")])
    monkeypatch.setattr("nonebot_plugin_hermes.handlers.message.hermes_client.chat", chat_mock)
    send_mock = AsyncMock()
    monkeypatch.setattr("nonebot_plugin_hermes.handlers.message.send_text_with_media", send_mock)

    await _run_passive_turn(
        bot=MagicMock(),
        target=_FakeTarget(id="g_say"),
        adapter_name="ob11",
        user_id="u_say",
        group_id="g_say",
        is_private=False,
        text="hello",
        image_urls=[],
        now_ms=1000,
    )

    sent = [c.kwargs["text"] for c in send_mock.call_args_list]
    assert sent == [_SESSION_RESET_NOTICE, "重开之后的回答"]


@pytest.mark.asyncio
async def test_second_overflow_stops_instead_of_rotating_again(monkeypatch: MonkeyPatch):
    """新会话还溢出说明病因不在历史,而在单轮本身 —— 再转一圈只会连着烧 generation。"""
    session_manager.clear_session("ob11", False, "u_twice", "g_twice")
    monkeypatch.setattr("nonebot_plugin_hermes.handlers.message.plugin_config.hermes_transport_error_fallback_text", "")

    chat_mock = AsyncMock(side_effect=[_overflow_result(), _overflow_result()])
    monkeypatch.setattr("nonebot_plugin_hermes.handlers.message.hermes_client.chat", chat_mock)
    send_mock = AsyncMock()
    monkeypatch.setattr("nonebot_plugin_hermes.handlers.message.send_text_with_media", send_mock)

    await _run_passive_turn(
        bot=MagicMock(),
        target=_FakeTarget(id="g_twice"),
        adapter_name="ob11",
        user_id="u_twice",
        group_id="g_twice",
        is_private=False,
        text="hello",
        image_urls=[],
        now_ms=1000,
    )

    assert chat_mock.call_count == 2, "只换一次 key,不做多轮"
    key_after = session_manager.get_session_key("ob11", False, "u_twice", "g_twice")
    assert chat_mock.call_args_list[1].kwargs["session_key"] == key_after, "第二次之后不再继续换 key"
    assert [c.kwargs["text"] for c in send_mock.call_args_list] == [_SESSION_RESET_NOTICE]


@pytest.mark.asyncio
async def test_upstream_english_notice_never_reaches_the_group(monkeypatch: MonkeyPatch):
    """这段英文让群友去敲 /compress /new —— 两个命令在 QQ 上都不存在。"""
    session_manager.clear_session("ob11", False, "u_leak", "g_leak")
    monkeypatch.setattr("nonebot_plugin_hermes.handlers.message.plugin_config.hermes_transport_error_fallback_text", "")

    chat_mock = AsyncMock(side_effect=[_overflow_result(), _overflow_result()])
    monkeypatch.setattr("nonebot_plugin_hermes.handlers.message.hermes_client.chat", chat_mock)
    send_mock = AsyncMock()
    monkeypatch.setattr("nonebot_plugin_hermes.handlers.message.send_text_with_media", send_mock)

    await _run_passive_turn(
        bot=MagicMock(),
        target=_FakeTarget(id="g_leak"),
        adapter_name="ob11",
        user_id="u_leak",
        group_id="g_leak",
        is_private=False,
        text="hello",
        image_urls=[],
        now_ms=1000,
    )

    for call in send_mock.call_args_list:
        assert "Context overflow" not in call.kwargs["text"]
        assert "/compress" not in call.kwargs["text"]


# ── 兜底(reactive 路)────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reactive_turn_recovers_the_same_way(monkeypatch: MonkeyPatch, _runtime):
    """两条出向路都要接上 —— 群聊默认走 reactive,漏掉它等于没修。"""
    from nonebot_plugin_hermes.handlers import message as handler_mod

    session_manager.clear_session("ob11", False, "u_re", "g_re")
    stale_key = session_manager.get_session_key("ob11", False, "u_re", "g_re")

    chat_mock = AsyncMock(
        side_effect=[_overflow_result(), ChatResult(raw_text="{}", structured={"should_reply": False})]
    )
    monkeypatch.setattr(handler_mod.hermes_client, "chat", chat_mock)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    now = 7_000_000
    _mcp.active_sessions.trigger("ob11", "g_re", "u_re", now_ms=now)
    await _run_reactive_turn(
        bot=_fake_bot(),
        target=_FakeTarget(id="g_re"),
        adapter_name="ob11",
        user_id="u_re",
        group_id="g_re",
        text="在吗",
        image_urls=[],
        is_explicit_trigger=True,
        now_ms=now,
    )

    assert chat_mock.call_count == 2
    assert chat_mock.call_args_list[1].kwargs["session_key"] != stale_key
