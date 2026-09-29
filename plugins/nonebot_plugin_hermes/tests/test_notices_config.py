"""验证 Notice 触发相关配置项默认值——老用户升级后行为零变化。"""

from __future__ import annotations


def test_poke_trigger_defaults_false():
    from nonebot_plugin_hermes.config import plugin_config

    assert plugin_config.hermes_poke_trigger_enabled is False


def test_greet_on_join_defaults_false():
    from nonebot_plugin_hermes.config import plugin_config

    assert plugin_config.hermes_greet_on_join is False
