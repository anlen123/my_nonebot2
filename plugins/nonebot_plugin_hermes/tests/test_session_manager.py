"""SessionManager 单元测试。

精简后的 SessionManager 只负责 session key 生成与 generation 递增。
历史缓冲(record_history / get_history_context)已迁移 MessageBuffer。
"""

from __future__ import annotations

from nonebot_plugin_hermes.core.session import SessionManager


def test_get_session_key_group_unique_per_user():
    sm = SessionManager()
    k1 = sm.get_session_key("ob11", False, "u1", "g1")
    k2 = sm.get_session_key("ob11", False, "u2", "g1")
    assert k1 != k2
    assert k1 == "hermes-ob11+group+g1+u1"
    assert k2 == "hermes-ob11+group+g1+u2"


def test_get_session_key_private_format():
    sm = SessionManager()
    assert sm.get_session_key("ob11", True, "u1") == "hermes-ob11+private+u1"


def test_get_session_key_idempotent():
    sm = SessionManager()
    k1 = sm.get_session_key("ob11", False, "u1", "g1")
    k2 = sm.get_session_key("ob11", False, "u1", "g1")
    assert k1 == k2


def test_clear_session_increments_generation():
    sm = SessionManager()
    k0 = sm.get_session_key("ob11", False, "u1", "g1")
    assert k0 == "hermes-ob11+group+g1+u1"

    sm.clear_session("ob11", False, "u1", "g1")
    k1 = sm.get_session_key("ob11", False, "u1", "g1")
    assert k1 == "hermes-ob11+group+g1+u1-g1"

    sm.clear_session("ob11", False, "u1", "g1")
    k2 = sm.get_session_key("ob11", False, "u1", "g1")
    assert k2 == "hermes-ob11+group+g1+u1-g2"


def test_clear_session_does_not_affect_other_keys():
    sm = SessionManager()
    sm.get_session_key("ob11", False, "u1", "g1")
    sm.get_session_key("ob11", False, "u2", "g1")
    sm.clear_session("ob11", False, "u1", "g1")

    # u1 has new generation; u2 unchanged
    assert sm.get_session_key("ob11", False, "u1", "g1") == "hermes-ob11+group+g1+u1-g1"
    assert sm.get_session_key("ob11", False, "u2", "g1") == "hermes-ob11+group+g1+u2"


def _enable_memory_scope(monkeypatch):
    from nonebot_plugin_hermes.config import plugin_config

    monkeypatch.setattr(plugin_config, "hermes_honcho_enabled", True)
    return plugin_config


def test_get_memory_key_disabled_returns_none():
    sm = SessionManager()
    assert sm.get_memory_key("ob11", False, "u1", "g1") is None


def test_get_memory_key_group_shared_by_default(monkeypatch):
    """同群不同人 → 同一个记忆 key;不同群 → 不同 key。这就是 issue #2 要的隔离。"""
    _enable_memory_scope(monkeypatch)
    sm = SessionManager()
    k_u1 = sm.get_memory_key("ob11", False, "u1", "g1")
    k_u2 = sm.get_memory_key("ob11", False, "u2", "g1")
    k_other_group = sm.get_memory_key("ob11", False, "u1", "g2")

    assert k_u1 == "agent:main:nonebot-ob11:group:g1"
    assert k_u1 == k_u2
    assert k_other_group == "agent:main:nonebot-ob11:group:g2"
    assert k_other_group != k_u1


def test_get_memory_key_group_per_user(monkeypatch):
    cfg = _enable_memory_scope(monkeypatch)
    monkeypatch.setattr(cfg, "hermes_group_sessions_per_user", True)
    sm = SessionManager()
    assert sm.get_memory_key("ob11", False, "u1", "g1") == "agent:main:nonebot-ob11:group:g1:u1"
    assert sm.get_memory_key("ob11", False, "u2", "g1") == "agent:main:nonebot-ob11:group:g1:u2"


def test_get_memory_key_private_format(monkeypatch):
    _enable_memory_scope(monkeypatch)
    sm = SessionManager()
    assert sm.get_memory_key("ob11", True, "u1") == "agent:main:nonebot-ob11:dm:u1"


def test_get_memory_key_group_id_missing_falls_back(monkeypatch):
    _enable_memory_scope(monkeypatch)
    sm = SessionManager()
    assert sm.get_memory_key("ob11", False, "u1", None) == "agent:main:nonebot-ob11:group:unknown"


def test_get_memory_key_custom_template(monkeypatch):
    cfg = _enable_memory_scope(monkeypatch)
    monkeypatch.setattr(cfg, "hermes_group_session_key_format", "myprefix-{group_id}")
    sm = SessionManager()
    assert sm.get_memory_key("ob11", False, "u1", "g1") == "myprefix-g1"


def test_get_memory_key_bad_template_returns_none_not_raise(monkeypatch):
    """模板写错不能把整轮对话堵死——记不住比回不了话轻。"""
    cfg = _enable_memory_scope(monkeypatch)
    monkeypatch.setattr(cfg, "hermes_group_session_key_format", "{nonexistent_var}")
    sm = SessionManager()
    assert sm.get_memory_key("ob11", False, "u1", "g1") is None


def test_get_memory_key_unaffected_by_clear_and_rotation(monkeypatch):
    """/clear 与上游压缩轮换都只动 transcript,记忆 key 必须原地不动。"""
    _enable_memory_scope(monkeypatch)
    sm = SessionManager()
    before = sm.get_memory_key("ob11", False, "u1", "g1")

    session_key = sm.get_session_key("ob11", False, "u1", "g1")
    sm.adopt_session_key(session_key, "hermes-rotated-child-id")
    sm.clear_session("ob11", False, "u1", "g1")

    assert sm.get_memory_key("ob11", False, "u1", "g1") == before


def test_get_memory_key_orthogonal_to_share_group(monkeypatch):
    """SHARE_GROUP 只管 transcript 维度,不该改变记忆维度。"""
    cfg = _enable_memory_scope(monkeypatch)
    monkeypatch.setattr(cfg, "hermes_session_share_group", True)
    sm = SessionManager()
    assert sm.get_memory_key("ob11", False, "u1", "g1") == "agent:main:nonebot-ob11:group:g1"


def test_get_memory_key_over_length_returns_none(monkeypatch):
    """上游对 X-Hermes-Session-Key 超 256 字符直接 400 整轮打回。
    模板是用户可配的,渲染超长时必须与模板写错同策略:不发头,而不是让每轮对话都失败。"""
    cfg = _enable_memory_scope(monkeypatch)
    monkeypatch.setattr(cfg, "hermes_group_session_key_format", "x" * 300 + "-{group_id}")
    sm = SessionManager()
    assert sm.get_memory_key("ob11", False, "u1", "g1") is None


def test_get_memory_key_at_length_limit_still_renders(monkeypatch):
    """边界:正好 256 合法,不能误杀。"""
    cfg = _enable_memory_scope(monkeypatch)
    monkeypatch.setattr(cfg, "hermes_group_session_key_format", "x" * 254 + "{group_id}")
    sm = SessionManager()
    key = sm.get_memory_key("ob11", False, "u1", "gg")
    assert key is not None
    assert len(key) == 256


def test_validate_memory_key_templates_clean_by_default():
    from nonebot_plugin_hermes.core.session import validate_memory_key_templates

    assert validate_memory_key_templates() == []


def test_validate_memory_key_templates_flags_bad_placeholder(monkeypatch):
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.core.session import validate_memory_key_templates

    monkeypatch.setattr(plugin_config, "hermes_private_session_key_format", "{nope}")
    problems = validate_memory_key_templates()
    assert len(problems) == 1
    assert "HERMES_PRIVATE_SESSION_KEY_FORMAT" in problems[0]


def test_validate_memory_key_templates_flags_overlong(monkeypatch):
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.core.session import validate_memory_key_templates

    monkeypatch.setattr(plugin_config, "hermes_group_session_key_format", "y" * 300 + "{group_id}")
    problems = validate_memory_key_templates()
    assert len(problems) == 1
    assert "HERMES_GROUP_SESSION_KEY_FORMAT" in problems[0]
    assert "256" in problems[0]
