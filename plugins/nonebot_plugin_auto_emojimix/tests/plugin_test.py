"""插件集成测试"""

import pytest
from nonebug import App


@pytest.mark.asyncio
async def test_plugin_metadata(app: App):
    """测试插件元数据加载是否正常"""
    from nonebot import require

    assert require("nonebot_plugin_auto_emojimix")

    from nonebot_plugin_auto_emojimix import __plugin_meta__

    assert __plugin_meta__.name == "自动合成emoji"
    assert (
        __plugin_meta__.description == "更好的emoji合成，包含自动触发合成，长期更新数据"
    )
    assert __plugin_meta__.type == "application"
    assert __plugin_meta__.supported_adapters is not None
    assert len(__plugin_meta__.supported_adapters) > 0


@pytest.mark.asyncio
async def test_handler_loaded(app: App):
    """测试命令处理器加载是否正常"""
    from nonebot import require

    require("nonebot_plugin_auto_emojimix")

    from nonebot_plugin_auto_emojimix.handler import (
        auto_emojimix_matcher,
        emojimix,
    )

    assert emojimix is not None
    assert auto_emojimix_matcher is not None


@pytest.mark.asyncio
async def test_config_loaded(app: App):
    """测试配置加载是否正常"""
    from nonebot import require

    require("nonebot_plugin_auto_emojimix")

    from nonebot_plugin_auto_emojimix.config import plugin_config

    assert plugin_config is not None
    assert plugin_config.emojimix_explicit is True
    assert plugin_config.emojimix_auto is True
    assert plugin_config.emojimix_cd == 60


@pytest.mark.asyncio
async def test_service_loaded(app: App):
    """测试服务加载是否正常"""
    from nonebot import require

    require("nonebot_plugin_auto_emojimix")

    from nonebot_plugin_auto_emojimix.service import emoji_mix_service

    assert emoji_mix_service is not None
    assert emoji_mix_service._db is not None
    assert len(emoji_mix_service._emoji_map) > 0
