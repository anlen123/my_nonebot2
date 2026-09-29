"""长期记忆作用域配置项默认值——老用户升级后行为零变化。"""

from __future__ import annotations


def test_honcho_disabled_by_default():
    from nonebot_plugin_hermes.config import plugin_config

    assert plugin_config.hermes_honcho_enabled is False


def test_group_sessions_per_user_defaults_false():
    """默认群级共享:一个群一份记忆,群成员共用——这是 issue #2 要的隔离粒度。"""
    from nonebot_plugin_hermes.config import plugin_config

    assert plugin_config.hermes_group_sessions_per_user is False


def test_key_format_defaults_align_with_hermes_native():
    """默认模板对齐 Hermes 原生 build_session_key 4-level 格式 + nonebot- 前缀防撞。"""
    from nonebot_plugin_hermes.config import plugin_config

    assert plugin_config.hermes_group_session_key_format == "agent:main:nonebot-{adapter}:group:{group_id}"
    assert (
        plugin_config.hermes_group_per_user_session_key_format
        == "agent:main:nonebot-{adapter}:group:{group_id}:{user_id}"
    )
    assert plugin_config.hermes_private_session_key_format == "agent:main:nonebot-{adapter}:dm:{user_id}"


def test_headers_carry_memory_key_when_set(monkeypatch):
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.core.hermes_client import HermesClient

    monkeypatch.setattr(plugin_config, "hermes_api_key", "secret-key")
    h = HermesClient().get_headers("hermes-sid", "agent:main:nonebot-ob11:group:g1")

    assert h["X-Hermes-Session-Id"] == "hermes-sid"
    assert h["X-Hermes-Session-Key"] == "agent:main:nonebot-ob11:group:g1"


def test_headers_omit_memory_key_when_none(monkeypatch):
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.core.hermes_client import HermesClient

    monkeypatch.setattr(plugin_config, "hermes_api_key", "secret-key")
    assert "X-Hermes-Session-Key" not in HermesClient().get_headers("hermes-sid", None)


def test_headers_omit_memory_key_without_api_key(monkeypatch):
    """上游对这个头要求鉴权,没 key 时发出去只会换来 403 —— 宁可不发。"""
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.core.hermes_client import HermesClient

    monkeypatch.setattr(plugin_config, "hermes_api_key", "")
    h = HermesClient().get_headers("hermes-sid", "agent:main:nonebot-ob11:group:g1")

    assert "X-Hermes-Session-Key" not in h
    assert "Authorization" not in h


def test_missing_capabilities_detects_absent_header_support():
    from nonebot_plugin_hermes.core.hermes_client import missing_memory_capabilities

    assert missing_memory_capabilities({"features": {"session_key_header": "X-Hermes-Session-Key"}}) == []
    assert missing_memory_capabilities({"features": {"session_continuity_header": "X-Hermes-Session-Id"}}) == [
        "session_key_header"
    ]


def test_missing_capabilities_accepts_flat_shape():
    """老版本 / 代理可能把 features 平铺在顶层,两种形状都认。"""
    from nonebot_plugin_hermes.core.hermes_client import missing_memory_capabilities

    assert missing_memory_capabilities({"session_key_header": "X-Hermes-Session-Key"}) == []


def test_missing_capabilities_empty_payload_reports_missing():
    from nonebot_plugin_hermes.core.hermes_client import missing_memory_capabilities

    assert missing_memory_capabilities({}) == ["session_key_header"]
