"""按群路由到不同 Hermes 接入点。"""

from __future__ import annotations

from typing import ClassVar

import pytest


def test_group_endpoints_empty_by_default():
    from nonebot_plugin_hermes.config import plugin_config

    assert plugin_config.hermes_group_endpoints == {}


def test_endpoint_model_defaults_are_inheritable_sentinels():
    """key/timeout 省略即回落到全局配置,所以默认值必须是可判空的哨兵。"""
    from nonebot_plugin_hermes.config import HermesEndpoint

    ep = HermesEndpoint(url="http://127.0.0.1:8642/p/groupa")
    assert ep.url == "http://127.0.0.1:8642/p/groupa"
    assert ep.key == ""
    assert ep.timeout == 0


def test_endpoint_model_accepts_full_form():
    from nonebot_plugin_hermes.config import HermesEndpoint

    ep = HermesEndpoint(url="http://127.0.0.1:8643", key="k2", timeout=120)
    assert (ep.url, ep.key, ep.timeout) == ("http://127.0.0.1:8643", "k2", 120)


def _set_table(monkeypatch, table: dict):
    from nonebot_plugin_hermes.config import HermesEndpoint, plugin_config

    parsed = {k: HermesEndpoint(**v) for k, v in table.items()}
    monkeypatch.setattr(plugin_config, "hermes_group_endpoints", parsed)
    return plugin_config


def test_resolve_target_falls_back_to_default(monkeypatch):
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.core.routing import resolve_target

    monkeypatch.setattr(plugin_config, "hermes_api_url", "http://127.0.0.1:8642/")
    monkeypatch.setattr(plugin_config, "hermes_api_key", "sk-global")
    monkeypatch.setattr(plugin_config, "hermes_api_timeout", 300)
    _set_table(monkeypatch, {})

    tgt = resolve_target("ob11", False, "g1")
    assert tgt.base_url == "http://127.0.0.1:8642", "尾斜杠必须去掉,拼接时才不会出现 //v1"
    assert tgt.api_key == "sk-global"
    assert tgt.timeout == 300
    assert tgt.label == "default"


def test_resolve_target_matches_adapter_and_group(monkeypatch):
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.core.routing import resolve_target

    monkeypatch.setattr(plugin_config, "hermes_api_url", "http://127.0.0.1:8642")
    monkeypatch.setattr(plugin_config, "hermes_api_key", "sk-global")
    monkeypatch.setattr(plugin_config, "hermes_api_timeout", 300)
    _set_table(monkeypatch, {"ob11:g1": {"url": "http://127.0.0.1:8642/p/team-a/", "key": "sk-a", "timeout": 120}})

    hit = resolve_target("ob11", False, "g1")
    assert hit.base_url == "http://127.0.0.1:8642/p/team-a"
    assert hit.api_key == "sk-a"
    assert hit.timeout == 120
    assert hit.label == "ob11:g1"

    # 同群号不同 adapter 不能命中
    assert resolve_target("telegram", False, "g1").label == "default"
    # 未列出的群回落
    assert resolve_target("ob11", False, "g2").label == "default"


def test_resolve_target_private_always_default(monkeypatch):
    """私聊不参与按群路由——路由键的语义就是群。"""
    from nonebot_plugin_hermes.core.routing import resolve_target

    _set_table(monkeypatch, {"ob11:u1": {"url": "http://127.0.0.1:9999"}})
    assert resolve_target("ob11", True, None).label == "default"


def test_entry_inherits_global_key_and_timeout(monkeypatch):
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.core.routing import resolve_target

    monkeypatch.setattr(plugin_config, "hermes_api_key", "sk-global")
    monkeypatch.setattr(plugin_config, "hermes_api_timeout", 300)
    _set_table(monkeypatch, {"ob11:g1": {"url": "http://127.0.0.1:8643"}})

    tgt = resolve_target("ob11", False, "g1")
    assert tgt.api_key == "sk-global"
    assert tgt.timeout == 300


def test_all_targets_includes_default_and_entries(monkeypatch):
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.core.routing import all_targets

    monkeypatch.setattr(plugin_config, "hermes_api_url", "http://127.0.0.1:8642")
    _set_table(monkeypatch, {"ob11:g1": {"url": "http://127.0.0.1:8643"}})

    labels = [t.label for t in all_targets()]
    assert labels == ["default", "ob11:g1"]


def test_routing_adapter_normalization_matches_get_adapter_name():
    """routing 自己归一化 adapter 段(不 import utils),口径必须与 get_adapter_name 一致。"""
    from types import SimpleNamespace

    from nonebot_plugin_hermes.core.routing import _normalize_adapter
    from nonebot_plugin_hermes.utils import get_adapter_name

    for raw in ("OneBot V11.", "Telegram", "onebot.v11"):
        assert _normalize_adapter(raw) == get_adapter_name(SimpleNamespace(adapter=raw))


def test_validate_endpoints_flags_missing_key(monkeypatch):
    """指向别的接入点却没有自己的 key:既 401 又没有反向隔离 —— 启动时就要说。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.core.routing import validate_endpoints

    monkeypatch.setattr(plugin_config, "hermes_api_url", "http://127.0.0.1:8642")
    monkeypatch.setattr(plugin_config, "hermes_api_key", "sk-global-at-least-16")
    _set_table(monkeypatch, {"ob11:g1": {"url": "http://127.0.0.1:8642/p/team-a"}})

    problems = validate_endpoints()
    assert len(problems) == 1
    assert "ob11:g1" in problems[0]


def test_validate_endpoints_flags_bad_key_and_url(monkeypatch):
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.core.routing import validate_endpoints

    monkeypatch.setattr(plugin_config, "hermes_api_url", "http://127.0.0.1:8642")
    monkeypatch.setattr(plugin_config, "hermes_api_key", "sk-global-at-least-16")
    _set_table(
        monkeypatch,
        {
            "no-colon-key": {"url": "http://127.0.0.1:8643", "key": "k-at-least-16-chars"},
            "ob11:g2": {"url": "127.0.0.1:8643", "key": "k-at-least-16-chars"},
        },
    )

    problems = validate_endpoints()
    assert any("no-colon-key" in p for p in problems)
    assert any("ob11:g2" in p for p in problems)


def test_validate_endpoints_flags_unnormalized_adapter(monkeypatch):
    """get_adapter_name() 输出小写去空格去点,写成 OneBotV11:… 永远匹配不上。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.core.routing import validate_endpoints

    monkeypatch.setattr(plugin_config, "hermes_api_url", "http://127.0.0.1:8642")
    _set_table(monkeypatch, {"OneBotV11:g1": {"url": "http://127.0.0.1:8643", "key": "k-at-least-16-chars"}})

    assert any("OneBotV11" in p for p in validate_endpoints())


def test_validate_endpoints_flags_short_key(monkeypatch):
    """上游 has_usable_secret(min_length=16):短 key 在 profile 侧等于没配。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.core.routing import validate_endpoints

    monkeypatch.setattr(plugin_config, "hermes_api_url", "http://127.0.0.1:8642")
    _set_table(monkeypatch, {"ob11:g1": {"url": "http://127.0.0.1:8642/p/team-a", "key": "short"}})

    assert any("16" in p for p in validate_endpoints())


def test_validate_endpoints_flags_same_url_different_keys(monkeypatch):
    """同一个接入点两把不同的 key:其中一把必然 401。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.core.routing import validate_endpoints

    monkeypatch.setattr(plugin_config, "hermes_api_url", "http://127.0.0.1:8642")
    _set_table(
        monkeypatch,
        {
            "ob11:g1": {"url": "http://127.0.0.1:8642/p/team-a", "key": "key-one-at-least-16"},
            "ob11:g2": {"url": "http://127.0.0.1:8642/p/team-a", "key": "key-two-at-least-16"},
        },
    )

    assert any("team-a" in p for p in validate_endpoints())


def test_validate_endpoints_silent_when_clean(monkeypatch):
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.core.routing import validate_endpoints

    monkeypatch.setattr(plugin_config, "hermes_api_url", "http://127.0.0.1:8642")
    _set_table(monkeypatch, {"ob11:g1": {"url": "http://127.0.0.1:8643", "key": "sk-a-at-least-16-ch"}})
    assert validate_endpoints() == []


class _FakeResponse:
    status_code = 200
    headers: ClassVar[dict] = {}

    @staticmethod
    def json():
        return {"choices": [{"message": {"content": "ok"}}]}


class _CapturingAsyncClient:
    """记录 chat() 实际用了哪个 URL / header / timeout。"""

    calls: ClassVar[list[dict]] = []

    def __init__(self, timeout=None):
        self._timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        type(self).calls.append({"url": url, "headers": headers, "timeout": self._timeout})
        return _FakeResponse()


@pytest.mark.asyncio
async def test_chat_uses_target_url_key_and_timeout(monkeypatch):
    import httpx

    from nonebot_plugin_hermes.core import hermes_client as client_mod
    from nonebot_plugin_hermes.core.routing import HermesTarget

    _CapturingAsyncClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _CapturingAsyncClient)

    tgt = HermesTarget(base_url="http://127.0.0.1:8642/p/team-a", api_key="sk-a", timeout=42, label="ob11:g1")
    await client_mod.HermesClient().chat(
        text="hi",
        session_key="hermes-sid",
        user_id="u1",
        group_id="g1",
        adapter_name="ob11",
        is_private=False,
        target=tgt,
    )

    call = _CapturingAsyncClient.calls[0]
    assert call["url"] == "http://127.0.0.1:8642/p/team-a/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer sk-a"
    assert call["timeout"] == 42


@pytest.mark.asyncio
async def test_chat_without_target_uses_default(monkeypatch):
    import httpx

    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.core import hermes_client as client_mod

    _CapturingAsyncClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _CapturingAsyncClient)
    monkeypatch.setattr(plugin_config, "hermes_api_url", "http://127.0.0.1:8642")
    monkeypatch.setattr(plugin_config, "hermes_api_key", "sk-global")

    await client_mod.HermesClient().chat(
        text="hi",
        session_key="hermes-sid",
        user_id="u1",
        group_id=None,
        adapter_name="ob11",
        is_private=True,
    )

    call = _CapturingAsyncClient.calls[0]
    assert call["url"] == "http://127.0.0.1:8642/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer sk-global"


def test_get_headers_explicit_api_key_overrides_global(monkeypatch):
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.core.hermes_client import HermesClient

    monkeypatch.setattr(plugin_config, "hermes_api_key", "sk-global")
    h = HermesClient().get_headers("hermes-sid", api_key="sk-a")
    assert h["Authorization"] == "Bearer sk-a"


@pytest.mark.asyncio
async def test_health_check_probes_the_given_target(monkeypatch):
    """/hermes-status 逐接入点体检要靠这个:探的是 target 自己的 url 与 key。"""
    import httpx

    from nonebot_plugin_hermes.core.hermes_client import HermesClient
    from nonebot_plugin_hermes.core.routing import HermesTarget

    seen: dict = {}

    class _GetClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None):
            seen.update({"url": url, "headers": headers})
            return _FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _GetClient)
    ok = await HermesClient().health_check(
        HermesTarget(base_url="http://127.0.0.1:8643", api_key="sk-b", timeout=9, label="ob11:g2")
    )

    assert ok is True
    assert seen["url"] == "http://127.0.0.1:8643/v1/models"
    assert seen["headers"]["Authorization"] == "Bearer sk-b"


def test_ping_message_shapes():
    from nonebot_plugin_hermes.handlers.commands import build_ping_message

    assert "pong" in build_ping_message(True)
    assert "pong" not in build_ping_message(False)


def test_ping_message_never_leaks_endpoint_info():
    """/ping 任何被允许的用户都能打,回复里不能出现接入点标签或数量。"""
    from nonebot_plugin_hermes.handlers.commands import build_ping_message

    for msg in (build_ping_message(True), build_ping_message(False)):
        assert "接入点" not in msg
        assert ":" not in msg.replace("：", "")


@pytest.mark.asyncio
async def test_ping_probes_the_current_group_endpoint(monkeypatch):
    """群 g1 打 /ping 探的是 g1 自己的接入点,不是默认接入点。"""
    from nonebot_plugin_hermes.core.routing import resolve_target
    from nonebot_plugin_hermes.handlers import commands as cmd_mod

    _set_table(monkeypatch, {"ob11:g1": {"url": "http://127.0.0.1:8643", "key": "sk-a-at-least-16-ch"}})

    seen: list = []

    async def fake_health(target=None):
        seen.append(target)
        return True

    monkeypatch.setattr(cmd_mod.hermes_client, "health_check", fake_health)
    assert await cmd_mod.probe_current_endpoint("ob11", False, "g1") is True

    assert seen[0].label == "ob11:g1"
    assert seen[0] == resolve_target("ob11", False, "g1")


def test_endpoint_health_lines_lists_every_target():
    from nonebot_plugin_hermes.handlers.commands import build_endpoint_health_lines

    body = "\n".join(build_endpoint_health_lines([("default", True), ("ob11:g1", False)]))
    assert "default" in body
    assert "ob11:g1" in body
    assert "❌" in body


def test_endpoint_health_lines_empty_for_no_results():
    from nonebot_plugin_hermes.handlers.commands import build_endpoint_health_lines

    assert build_endpoint_health_lines([]) == []


def test_status_lines_annotate_endpoint_count(monkeypatch):
    from nonebot_plugin_hermes.handlers.commands import build_status_lines

    _set_table(monkeypatch, {"ob11:g1": {"url": "http://127.0.0.1:8643", "key": "sk-a-at-least-16-ch"}})
    api_line = next(ln for ln in build_status_lines(0) if ln.startswith("hermes_api:"))
    assert "1" in api_line and "接入点" in api_line


def test_status_lines_have_no_annotation_when_table_empty(monkeypatch):
    from nonebot_plugin_hermes.handlers.commands import build_status_lines

    _set_table(monkeypatch, {})
    api_line = next(ln for ln in build_status_lines(0) if ln.startswith("hermes_api:"))
    assert "接入点" not in api_line


def test_multiplex_notice_is_informational_not_a_problem(monkeypatch):
    """多路复用形态(/p/<profile> url)+ 反向通道开着 = scope 无法按接入点收敛,启动时要说。

    上游的 MCP 发现是进程级的:gateway 启动时在**没有 profile scope** 的情况下读一次默认
    profile 的 config.yaml,注册表全局共享。所以命名 profile 自己的 mcp_servers 不会被连接,
    所有 profile 的 agent 呈同一把 token。

    但插件**无法从自己这侧核实**对面配对没配对 —— 这条在正确配置上也必然触发,所以它是
    INFO 级提醒(走 `multiplex_reverse_channel_notices()`),**不进** `validate_endpoints()`
    那批 WARNING 级的真·配置错误。真失败由 push 时的 `拒绝越权` WARNING 精确报出。
    """
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.core.routing import (
        multiplex_reverse_channel_notices,
        validate_endpoints,
    )

    monkeypatch.setattr(plugin_config, "hermes_api_url", "http://127.0.0.1:8642")
    monkeypatch.setattr(plugin_config, "hermes_mcp_enabled", True)
    _set_table(monkeypatch, {"ob11:g1": {"url": "http://127.0.0.1:8642/p/team-a", "key": "k-at-least-16-chars"}})

    notices = multiplex_reverse_channel_notices()
    assert any("多路复用" in n and "反向通道" in n for n in notices)
    # 这条无法核实的提醒不得混进 WARNING 级的真错误里。
    assert not any("多路复用" in p for p in validate_endpoints())


def test_no_multiplex_notice_for_separate_processes(monkeypatch):
    """独立进程形态(各自端口)下反向通道能按接入点收敛,不该提醒。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.core.routing import (
        multiplex_reverse_channel_notices,
        validate_endpoints,
    )

    monkeypatch.setattr(plugin_config, "hermes_api_url", "http://127.0.0.1:8642")
    monkeypatch.setattr(plugin_config, "hermes_mcp_enabled", True)
    _set_table(monkeypatch, {"ob11:g1": {"url": "http://127.0.0.1:8643", "key": "k-at-least-16-chars"}})

    assert multiplex_reverse_channel_notices() == []
    assert validate_endpoints() == []


def test_no_multiplex_notice_when_mcp_off(monkeypatch):
    """没开反向通道就没这个问题。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.core.routing import (
        multiplex_reverse_channel_notices,
        validate_endpoints,
    )

    monkeypatch.setattr(plugin_config, "hermes_api_url", "http://127.0.0.1:8642")
    monkeypatch.setattr(plugin_config, "hermes_mcp_enabled", False)
    _set_table(monkeypatch, {"ob11:g1": {"url": "http://127.0.0.1:8642/p/team-a", "key": "k-at-least-16-chars"}})

    assert multiplex_reverse_channel_notices() == []
    assert validate_endpoints() == []
