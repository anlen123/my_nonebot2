"""验证 Phase B-0 配置项默认值——升级后行为零变化。"""

from __future__ import annotations


def test_ack_feedback_defaults_false():
    from nonebot_plugin_hermes.config import plugin_config

    assert plugin_config.hermes_ack_feedback_enabled is False


def test_ack_emoji_id_default():
    from nonebot_plugin_hermes.config import plugin_config

    assert plugin_config.hermes_ack_emoji_id == 341
