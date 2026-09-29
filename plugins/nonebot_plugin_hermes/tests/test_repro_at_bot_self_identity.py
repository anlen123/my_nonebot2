"""复现:群里直接 @ bot,LLM 却判成「在 @ 另一个 bot」并静默。

用户报告 (2026-08-20):
  - 新群里只有用户和 bot,用户发 `@bot 早`
  - decision 回 should_reply=false,topic_hint 写「主人在问候另一个bot」

成因:OneBot v11 的 _check_at_me 剥走 @bot 段后,342660b 的 At 回填把
`@<bot_self_id>` 拼回 plain text;而 reactive prompt 从不声明 bot 自己的账号
(persona 侧通常只写了主人的 id),于是当前消息里唯一的 @ 对模型来说是一串陌生
数字,decision_protocol 的「只 @ 了别人 → 不归你」规则据此判 should_reply=false。

本测试钉住:prompt 必须自带「你是谁」这条事实,且 bot 自己的历史行昵称用平台
账号名而不是字面 "Bot"(角色名不叫 Bot 时,那行本身就读成「另一个 bot」)。
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from nonebot_plugin_hermes import mcp as _mcp
from nonebot_plugin_hermes.core.active_session import ActiveSessionManager
from nonebot_plugin_hermes.core.bot_registry import BotRegistry
from nonebot_plugin_hermes.core.hermes_client import ChatResult
from nonebot_plugin_hermes.core.inflight import InflightRegistry
from nonebot_plugin_hermes.core.message_buffer import MessageBuffer
from nonebot_plugin_hermes.core.prompt_builder import build_reactive_user_content
from nonebot_plugin_hermes.core.storage.image_cache import ImageCache
from nonebot_plugin_hermes.core.storage.image_fetcher import ImageFetcher
from nonebot_plugin_hermes.core.storage.message_store import MessageStore

SELF_ID = "bot-self-1"
USER_ID = "u1"


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


def _fake_bot(self_id: str, nickname: str = "Tenten"):
    """带 get_login_info 的 bot mock。昵称缓存按 self_id 分桶,故各测试用独立 id。"""
    bot = MagicMock()
    bot.self_id = self_id
    bot.call_api = AsyncMock(return_value={"nickname": nickname})
    return bot


# --- prompt 侧:@<self_id> 必须能被绑回「我」 ---


def test_at_on_self_is_resolvable_from_runtime_state():
    """当前消息里的 `@<self_id>` 与 runtime_state.you 的 id 必须是同一串,模型才能认出是 @ 自己。"""
    content = build_reactive_user_content(
        adapter="ob11",
        group_id="g1",
        self_id=SELF_ID,
        self_nickname="Tenten",
        addressed_to_bot=True,
        triggered_by=USER_ID,
        triggered_by_nickname=None,
        topic_hint=None,
        recent_messages=[],
        current_user_id=USER_ID,
        current_nickname="ph",
        current_text=f"早 @{SELF_ID}",
        current_image_urls=[],
    )
    assert isinstance(content, str)
    runtime_block = content.split("</runtime_state>")[0]
    assert f"you: [user=Tenten id={SELF_ID}]" in runtime_block
    assert f"@{SELF_ID}" in content.split("<current_message>")[1]


# --- 接线:handler 必须把 bot 自己的身份传进 prompt,并按平台昵称回写历史 ---


async def _run_turn(monkeypatch, bot, *, reply_text: str | None) -> dict:
    from nonebot_plugin_hermes.handlers import message as handler_mod

    captured: dict = {}

    def capture_build(**kwargs):
        captured.update(kwargs)
        return "stub-user-content"

    structured: dict = {"should_reply": reply_text is not None}
    if reply_text is not None:
        structured["reply_text"] = reply_text

    async def fake_chat(**kwargs):
        return ChatResult(raw_text="ok", structured=structured)

    monkeypatch.setattr(handler_mod, "build_reactive_user_content", capture_build)
    monkeypatch.setattr(handler_mod.hermes_client, "chat", fake_chat)
    monkeypatch.setattr(handler_mod, "send_text_with_media", AsyncMock(return_value=True))

    now = 9_000_000
    _mcp.active_sessions.trigger("ob11", "g1", USER_ID, now_ms=now)
    await handler_mod._run_reactive_turn(
        bot=bot,
        target=_FakeTarget(id="g1"),
        adapter_name="ob11",
        user_id=USER_ID,
        group_id="g1",
        text=f"早 @{bot.self_id}",
        image_urls=[],
        is_explicit_trigger=True,
        addressed_to_bot=True,
        now_ms=now,
        nickname="ph",
    )
    return captured


@pytest.mark.asyncio
async def test_reactive_turn_passes_own_identity_into_prompt(_runtime, monkeypatch):
    bot = _fake_bot("bot-self-wiring", nickname="Tenten")
    captured = await _run_turn(monkeypatch, bot, reply_text=None)
    assert captured["self_id"] == bot.self_id
    assert captured["self_nickname"] == "Tenten"


@pytest.mark.asyncio
async def test_bot_reply_writeback_uses_platform_nickname(_runtime, monkeypatch):
    """自己的历史行不能标成字面 "Bot":角色名不叫 Bot 时那行会被读成另一个 bot。"""
    bot = _fake_bot("bot-self-writeback", nickname="Tenten")
    await _run_turn(monkeypatch, bot, reply_text="早呀~")

    rows = _mcp.message_buffer.get_recent(adapter="ob11", group_id="g1", limit=10)
    bot_lines = [m for m in rows if m.is_bot]
    assert bot_lines, "bot 的回复应回写进 buffer"
    assert bot_lines[0].nickname == "Tenten"


# --- 昵称取值:非 str 一律回落,不能把实现端的奇怪返回渲染进 prompt ---


@pytest.mark.parametrize(
    "login_info",
    [None, {}, {"nickname": None}, {"nickname": ""}, {"nickname": 12345}, "not-a-dict"],
)
@pytest.mark.asyncio
async def test_bot_nickname_falls_back_on_unusable_login_info(login_info):
    from nonebot_plugin_hermes.core import outbound

    bot = MagicMock()
    bot.self_id = f"self-{login_info!r}"
    bot.call_api = AsyncMock(return_value=login_info)
    try:
        assert await outbound.get_bot_nickname(bot) == "Bot"
    finally:
        outbound._bot_nickname_cache.pop(bot.self_id, None)


@pytest.mark.asyncio
async def test_bot_nickname_strips_and_caches():
    from nonebot_plugin_hermes.core import outbound

    bot = MagicMock()
    bot.self_id = "self-cached"
    bot.call_api = AsyncMock(return_value={"nickname": "  Tenten  "})
    try:
        assert await outbound.get_bot_nickname(bot) == "Tenten"
        bot.call_api.reset_mock()
        assert await outbound.get_bot_nickname(bot) == "Tenten"
        bot.call_api.assert_not_called()
    finally:
        outbound._bot_nickname_cache.pop(bot.self_id, None)


# --- 寻址断言:与触发正交的独立事实,在 handle_message 处按平台信号算 ---


@pytest.mark.parametrize(
    ("trigger_mode", "mention_bot", "expected_addressed"),
    [
        ("at", True, True),  # 真 @ → 寻址
        ("all", False, False),  # all 模式泛聊天:explicit 但不寻址
        ("all", True, True),  # all 模式下真 @ 照样寻址
        ("keyword", True, True),
    ],
)
@pytest.mark.asyncio
async def test_addressing_follows_platform_signal_not_trigger_mode(
    _runtime, monkeypatch, trigger_mode, mention_bot, expected_addressed
):
    """寻址由 @/引用/唤起词决定,不从 is_explicit_trigger 派生 —— 两者在 all 模式与 synth 下会背离。"""
    import nonebot_plugin_alconna as alconna

    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as handler_mod

    bot_self = f"bot-hm-{trigger_mode}-{mention_bot}"
    monkeypatch.setattr(plugin_config, "hermes_group_trigger", trigger_mode)
    monkeypatch.setattr(plugin_config, "hermes_active_session_enabled", True)
    monkeypatch.setattr(plugin_config, "hermes_ack_feedback_enabled", False)
    monkeypatch.setattr(plugin_config, "hermes_keywords", ["tenten"])
    monkeypatch.setattr(plugin_config, "hermes_allow_users", [])
    monkeypatch.setattr(plugin_config, "hermes_allow_groups", [])

    segments = [alconna.Text("tenten 早")] if trigger_mode == "keyword" else [alconna.Text("早")]
    if mention_bot and trigger_mode != "keyword":
        segments.insert(0, alconna.At(flag="user", target=bot_self))
    uni_msg = alconna.UniMessage(segments)

    monkeypatch.setattr(alconna.UniMessage, "generate_without_reply", MagicMock(return_value=uni_msg))
    target = _FakeTarget(id="g1")
    monkeypatch.setattr(alconna, "get_target", MagicMock(return_value=target))
    monkeypatch.setattr(handler_mod, "get_adapter_name", lambda t: "ob11")
    monkeypatch.setattr(handler_mod, "check_isolation", lambda e, t: True)

    captured: dict = {}

    async def capture_reactive(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(handler_mod, "_handle_reactive_path", capture_reactive)

    event = MagicMock()
    event.is_tome = MagicMock(return_value=bool(mention_bot and trigger_mode != "keyword"))
    event.reply = None
    event.message_id = 1
    bot = _fake_bot(bot_self)
    matcher = MagicMock()
    matcher.skip = MagicMock(side_effect=RuntimeError("skipped"))

    await handler_mod.handle_message(bot, event, matcher)
    assert captured, "应走到 reactive 派发"
    assert captured["addressed_to_bot"] is expected_addressed
    assert captured["is_explicit_trigger"] is True


@pytest.mark.asyncio
async def test_pending_entry_preserves_addressing(_runtime):
    """排队的 explicit @ 经 refire 接力时,寻址事实不能丢。"""
    from nonebot_plugin_hermes.core.message_buffer import BufferedMessage as BM

    msg = BM(ts=1, adapter="ob11", group_id="g1", user_id="u1", nickname="n", content="hi", image_urls=[])
    key = ("ob11", "group:g1")
    assert _mcp.inflight.try_enter(key, msg, is_explicit_trigger=True, original_msg_id=1, now_ms=1) == "entered"
    _mcp.inflight.try_enter(key, msg, is_explicit_trigger=True, original_msg_id=2, now_ms=2, addressed_to_bot=True)
    entry = _mcp.inflight.take_pending(key)
    assert entry is not None and entry.addressed_to_bot is True


@pytest.mark.asyncio
async def test_silence_after_addressing_assertion_warns(_runtime, monkeypatch):
    """被点名却静默 = 模型否掉了插件给的既定事实,必须是 warning 而不是 info。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as handler_mod

    monkeypatch.setattr(plugin_config, "hermes_group_trigger", "at")
    warnings: list[str] = []
    monkeypatch.setattr(handler_mod.logger, "warning", lambda msg, *a, **kw: warnings.append(str(msg)))

    bot = _fake_bot("bot-self-silent-warn")
    await _run_turn(monkeypatch, bot, reply_text=None)
    assert any("should_reply=false" in w and "addressed=True" in w for w in warnings), warnings


@pytest.mark.parametrize(("addressed", "expected"), [(True, True), (False, False)])
@pytest.mark.asyncio
async def test_synth_addressing_passes_through_verbatim(_runtime, monkeypatch, addressed, expected):
    """synth 是 explicit 但寻址由事件语义决定(戳 bot=True,入群=False),不得从 explicit 派生。"""
    from unittest.mock import AsyncMock

    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.handlers import message as handler_mod

    monkeypatch.setattr(plugin_config, "hermes_active_session_enabled", True)
    reactive_mock = AsyncMock()
    monkeypatch.setattr(handler_mod, "_handle_reactive_path", reactive_mock)

    await handler_mod.route_synthesized_input(
        bot=_fake_bot(f"bot-synth-{addressed}"),
        target=_FakeTarget(id="g1"),
        adapter_name="ob11",
        user_id="u1",
        group_id="g1",
        nickname="n",
        text="[event] synthetic",
        allow_passive=False,
        addressed_to_bot=addressed,
        now_ms=9_000_000,
    )
    assert reactive_mock.call_args.kwargs["addressed_to_bot"] is expected
    assert reactive_mock.call_args.kwargs["is_explicit_trigger"] is True
