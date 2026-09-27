"""
nonebot-plugin-auto-emojimix

将两个emoji合成为一张图片
"""

from nonebot import require
from nonebot.plugin import PluginMetadata, inherit_supported_adapters

require("nonebot_plugin_alconna")

from . import handler as handler
from .config import Config

__plugin_meta__ = PluginMetadata(
    name="自动合成emoji",
    description="更好的emoji合成，包含自动触发合成，长期更新数据",
    usage="{emoji1}+{emoji2}，如：😎+😁",
    type="application",
    homepage="https://github.com/Misty02600/nonebot-plugin-auto-emojimix",
    config=Config,
    supported_adapters=inherit_supported_adapters("nonebot_plugin_alconna"),
)
