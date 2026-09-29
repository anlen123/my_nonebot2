"""非显式 turn 上 structured parse 失败的兜底策略。

背景:reactive 路径期望模型回一个 JSON5 `submit_decision` 信封。模型偶尔会**完全
抛开协议**、直接用自然散文作答(尤其是被纠正后自我更正时)。此前非显式(旁观 /
续发)turn 的解析失败一律静默,于是那段本该发出去的散文被丢掉。

区分两种失败:
  - 纯散文(连 "should_reply" 字段都没有)→ 模型没进信封、散文就是它想说的话 → 照发。
  - 信封在场但破了("should_reply" 存在)→ 可能带着 should_reply=false 决定 → 仍静默。
transport / persistence 错误的 raw 是服务端报文,永不当散文转发。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest

from nonebot_plugin_hermes import mcp as _mcp
from nonebot_plugin_hermes.core.active_session import ActiveSessionManager
from nonebot_plugin_hermes.core.bot_registry import BotRegistry
from nonebot_plugin_hermes.core.hermes_client import ChatResult
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


async def _run_bystander_turn(monkeypatch, chat_result: ChatResult) -> AsyncMock:
    """在活跃窗内跑一发非显式 turn,chat 固定返回 chat_result;返回 send mock。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as handler_mod

    monkeypatch.setattr(plugin_config, "hermes_reactive_post_reply_cooldown_sec", 0)

    now = int(time.time() * 1000)
    _mcp.active_sessions.trigger("ob11", "g1", "seed", now_ms=now)

    async def fake_chat(**kwargs):
        return chat_result

    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(handler_mod.hermes_client, "chat", fake_chat)
    monkeypatch.setattr(handler_mod, "send_text_with_media", send_mock)

    await handler_mod._handle_reactive_path(
        bot=_fake_bot(),
        target=_FakeTarget(id="g1", private=False),
        adapter_name="ob11",
        user_id="user-A",
        group_id="g1",
        text="曾 应该读 zēng 不是 céng",
        image_urls=[],
        is_explicit_trigger=False,
        addressed_to_bot=False,
        now_ms=now,
    )
    return send_mock


@pytest.mark.asyncio
async def test_bystander_pure_prose_is_forwarded(monkeypatch):
    """非显式 turn + 模型回纯散文(无 JSON 信封)→ 散文照发,不静默吞掉。"""
    prose = "主人说得对，我标错读音了喵。「曾」在亲属称谓里读 zēng,不是 céng,感谢主人纠正喵~"
    result = ChatResult(raw_text=prose, structured=None, parse_failed=True, is_transport_error=False)

    send_mock = await _run_bystander_turn(monkeypatch, result)

    assert send_mock.await_count == 1, "纯散文兜底应发一次,而不是静默丢弃"
    sent = send_mock.await_args_list[-1]
    assert sent.kwargs.get("text") == prose
    # 非显式 turn 不 @ 用户(与成功回复路径同口径)
    assert sent.kwargs.get("at_user_id") is None


@pytest.mark.asyncio
async def test_bystander_malformed_envelope_stays_silent(monkeypatch):
    """非显式 turn + 破损 JSON 信封(含 "should_reply")→ 仍静默:可能带 should_reply=false。"""
    broken = '{"should_reply": false, "reply_text": "别人聊的,不关我事'  # 截断的信封
    result = ChatResult(raw_text=broken, structured=None, parse_failed=True, is_transport_error=False)

    send_mock = await _run_bystander_turn(monkeypatch, result)

    assert send_mock.await_count == 0, "破损信封可能藏着 should_reply=false,非显式 turn 必须静默"


@pytest.mark.asyncio
async def test_bystander_transport_error_never_forwarded_as_prose(monkeypatch):
    """非显式 turn + transport_error → raw 是服务端报文,即便无信封也绝不转发。"""
    result = ChatResult(
        raw_text="⚠️ 无法连接到 AI 服务",
        structured=None,
        parse_failed=True,
        is_transport_error=True,
    )

    send_mock = await _run_bystander_turn(monkeypatch, result)

    assert send_mock.await_count == 0, "transport 错误的 raw 是报文,不能当散文发到群里"


# 上游把 provider 的失败包成 200 + 报错正文当助手回复返回,于是这段报文没有 transport
# 标记、也不含 "should_reply",恰好落进"纯散文"那一档被发进群 —— 连带报文里那条带
# 时效凭据的媒体 URL。URL 用合成值,形状与真实报文一致即可。
_PROVIDER_ERROR = (
    "HTTP 400: Error from provider (Console Go): Upstream request failed: "
    "[invalid_request_error] .messages[9].image[0]: Failed to download image from "
    "https://media.example.invalid/download?appid=1407&fileid=SYNTHETIC-CREDENTIAL&sp"
)


class _ErrorBodyResponse:
    """上游 200,但 content 是 provider 的报错正文。"""

    status_code = 200
    headers: ClassVar[dict] = {}

    @staticmethod
    def json():
        return {"choices": [{"message": {"content": _PROVIDER_ERROR}}]}


class _ErrorBodyClient:
    def __init__(self, timeout=None):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        return _ErrorBodyResponse()


def test_provider_error_text_detection():
    """前缀与特征词必须同时命中,否则会误伤正经聊到状态码或报错的回复。"""
    from nonebot_plugin_hermes.core.hermes_client import is_provider_error_text

    assert is_provider_error_text(_PROVIDER_ERROR)
    assert not is_provider_error_text("")
    # 只有前缀:用户问 HTTP 状态码含义,模型正经作答
    assert not is_provider_error_text("HTTP 400: 这个状态码表示请求本身有问题喵")
    # 只有特征词:群里在讨论报错,不是报错本身
    assert not is_provider_error_text("刚才那条 invalid_request_error 是因为图拉不到喵")


@pytest.mark.asyncio
async def test_provider_error_returned_as_200_is_marked_transport(monkeypatch):
    """200 外壳 + 错误正文 → 按传输失败处理,而不是当成模型的回答。"""
    import httpx

    from nonebot_plugin_hermes.core import hermes_client as client_mod

    monkeypatch.setattr(httpx, "AsyncClient", _ErrorBodyClient)

    result = await client_mod.hermes_client.chat(
        text="在吗",
        session_key="s-1",
        user_id="u1",
        group_id="g1",
        adapter_name="ob11",
        is_private=False,
        expect_structured=True,
        structured_tool_name="submit_decision",
    )
    assert result.is_transport_error is True
    assert result.parse_failed is True
    # 不标成持久化失败:那条路会拿同一个 key 重试,而这里重试必然同样失败
    assert result.is_persistence_error is False


@pytest.mark.asyncio
async def test_bystander_provider_error_is_not_forwarded(monkeypatch):
    """整条链路:200 错误正文 → 不发进群,凭据 URL 也不外泄。"""
    import httpx

    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as handler_mod

    monkeypatch.setattr(httpx, "AsyncClient", _ErrorBodyClient)
    monkeypatch.setattr(plugin_config, "hermes_reactive_post_reply_cooldown_sec", 0)

    now = int(time.time() * 1000)
    _mcp.active_sessions.trigger("ob11", "g1", "seed", now_ms=now)
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(handler_mod, "send_text_with_media", send_mock)

    await handler_mod._handle_reactive_path(
        bot=_fake_bot(),
        target=_FakeTarget(id="g1", private=False),
        adapter_name="ob11",
        user_id="user-A",
        group_id="g1",
        text="这图是什么鸟",
        image_urls=[],
        is_explicit_trigger=False,
        addressed_to_bot=False,
        now_ms=now,
    )

    assert send_mock.await_count == 0, "provider 报错正文绝不能当散文发进群"
    assert "SYNTHETIC-CREDENTIAL" not in str(send_mock.await_args_list)
