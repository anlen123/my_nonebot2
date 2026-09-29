"""MCP 鉴权与上下文校验测试。

HTTP 层的身份判定从 check_bearer 换成了 resolve_caller_scope:身份与"能操作哪些群"
是同一件事(呈上哪个接入点的 key 就是哪个接入点),两处口径分开维护迟早漂移。
这里保留原 check_bearer 那一组的等价覆盖(缺 header / 错 scheme / 错 token /
开发模式 / 尾随空白),断言从"抛不抛"改成"是不是 None"。范围派生本身在
tests/test_mcp_scope.py。
"""

from __future__ import annotations

import pytest

from nonebot_plugin_hermes.core.active_session import ActiveSessionManager
from nonebot_plugin_hermes.core.bot_registry import BotRegistry
from nonebot_plugin_hermes.core.routing import CallerScope, resolve_caller_scope
from nonebot_plugin_hermes.mcp.auth import (
    PushContextError,
    validate_push_context,
)

# validate_push_context 的这一组用例测的是 session / target 前置,不是范围收敛,
# 所以给它们一个开发模式 scope(允许一切),范围本身在 test_mcp_scope.py 里测。
_ANY_SCOPE = CallerScope.dev()


class _FakeTarget:
    private = False


@pytest.fixture
def _single_key(monkeypatch):
    """只配全局 key、没有路由表 —— v0.5.0 的部署形态。"""
    from nonebot_plugin_hermes.config import plugin_config

    monkeypatch.setattr(plugin_config, "hermes_api_key", "secret-xyz")
    monkeypatch.setattr(plugin_config, "hermes_group_endpoints", {})


def test_bearer_accepts_matching_token(_single_key):
    assert resolve_caller_scope("Bearer secret-xyz") is not None


def test_bearer_rejects_missing_header(_single_key):
    assert resolve_caller_scope(None) is None


def test_bearer_rejects_wrong_token(_single_key):
    assert resolve_caller_scope("Bearer wrong") is None


def test_bearer_rejects_non_bearer_scheme(_single_key):
    assert resolve_caller_scope("Basic abc") is None


def test_bearer_dev_mode_when_no_key_configured(monkeypatch):
    """全局 key 与条目 key 都没配 = 开发模式,不鉴权(与 v0.5.0 同口径)。"""
    from nonebot_plugin_hermes.config import plugin_config

    monkeypatch.setattr(plugin_config, "hermes_api_key", "")
    monkeypatch.setattr(plugin_config, "hermes_group_endpoints", {})

    assert resolve_caller_scope(None) is not None
    # 开发模式下即使 header 带残留 token 也不该被拒。
    assert resolve_caller_scope("Bearer some-leftover-token") is not None


def test_bearer_tolerates_trailing_whitespace_in_token(_single_key):
    r"""客户端偶尔在 token 尾追加空格(\r\n 或转义),应容忍而非 401。"""
    assert resolve_caller_scope("Bearer secret-xyz ") is not None
    assert resolve_caller_scope("Bearer secret-xyz\t") is not None


def test_validate_push_context_active_with_known_target():
    am = ActiveSessionManager(default_ttl_sec=60)
    br = BotRegistry()
    am.trigger("ob11", "g1", "u1", now_ms=0)
    br.upsert("ob11", "group", "g1", "bot", _FakeTarget(), ts=0)

    # 不抛
    validate_push_context(
        adapter="ob11",
        group_id="g1",
        active_sessions=am,
        bot_registry=br,
        now_ms=30_000,
        scope=_ANY_SCOPE,
    )


def test_validate_push_context_no_active_session_raises():
    am = ActiveSessionManager(default_ttl_sec=60)
    br = BotRegistry()
    br.upsert("ob11", "group", "g1", "bot", _FakeTarget(), ts=0)
    with pytest.raises(PushContextError):
        validate_push_context(
            adapter="ob11",
            group_id="g1",
            active_sessions=am,
            bot_registry=br,
            now_ms=30_000,
            scope=_ANY_SCOPE,
        )


def test_validate_push_context_unknown_target_raises():
    am = ActiveSessionManager(default_ttl_sec=60)
    br = BotRegistry()
    am.trigger("ob11", "g1", "u1", now_ms=0)
    with pytest.raises(PushContextError):
        validate_push_context(
            adapter="ob11",
            group_id="g1",
            active_sessions=am,
            bot_registry=br,
            now_ms=30_000,
            scope=_ANY_SCOPE,
        )


def test_validate_push_context_expired_session_raises():
    am = ActiveSessionManager(default_ttl_sec=60)
    br = BotRegistry()
    am.trigger("ob11", "g1", "u1", now_ms=0)
    br.upsert("ob11", "group", "g1", "bot", _FakeTarget(), ts=0)
    with pytest.raises(PushContextError):
        validate_push_context(
            adapter="ob11",
            group_id="g1",
            active_sessions=am,
            bot_registry=br,
            now_ms=999_999,
            scope=_ANY_SCOPE,
        )


def test_validate_push_context_both_fail_reports_session_first():
    """session + target 都缺时,session 错优先报(更常见,TTL 过期是日常)。"""
    am = ActiveSessionManager(default_ttl_sec=60)
    br = BotRegistry()
    # 两边都空
    with pytest.raises(PushContextError, match="no active reactive session"):
        validate_push_context(
            adapter="ob11",
            group_id="g1",
            active_sessions=am,
            bot_registry=br,
            now_ms=30_000,
            scope=_ANY_SCOPE,
        )


def test_validate_push_context_checks_scope_before_session(monkeypatch):
    """范围必须先判:先查 session 再查范围,会让越权方通过报错差异探知别群有没有活跃会话。"""
    from nonebot_plugin_hermes.config import HermesEndpoint, plugin_config

    monkeypatch.setattr(plugin_config, "hermes_api_key", "global-key-at-least-16")
    monkeypatch.setattr(
        plugin_config,
        "hermes_group_endpoints",
        {"ob11:g1": HermesEndpoint(url="http://h:8643", key="team-key-at-least-16")},
    )
    scope = resolve_caller_scope("Bearer team-key-at-least-16")

    am = ActiveSessionManager(default_ttl_sec=60)
    br = BotRegistry()
    am.trigger("ob11", "g2", "u1", now_ms=0)
    br.upsert("ob11", "group", "g2", "bot", _FakeTarget(), ts=0)

    # g2 有活跃 session 且有路由,但不在这把 token 的范围内 → 报的必须是范围,
    # 而不是暴露 g2 的 session 状态。
    with pytest.raises(PushContextError, match="not authorized"):
        validate_push_context(
            adapter="ob11",
            group_id="g2",
            active_sessions=am,
            bot_registry=br,
            now_ms=30_000,
            scope=scope,
        )


def test_validate_push_context_missing_scope_is_refused():
    """scope=None(认不出调用方)一律拒,不能回落成"不限"。"""
    am = ActiveSessionManager(default_ttl_sec=60)
    br = BotRegistry()
    am.trigger("ob11", "g1", "u1", now_ms=0)
    br.upsert("ob11", "group", "g1", "bot", _FakeTarget(), ts=0)

    with pytest.raises(PushContextError, match="not authorized"):
        validate_push_context(
            adapter="ob11",
            group_id="g1",
            active_sessions=am,
            bot_registry=br,
            now_ms=30_000,
            scope=None,
        )
