"""nonebot_plugin_biliav 的插件配置。

沿用插件模板的 Config：当前没有自定义字段，只是显式声明「.env 里出现的其它键一律忽略」，
避免 nonebot 把无关配置项当成校验错误。
"""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Config(BaseSettings):
    # Your Config Here

    class Config:
        extra = "ignore"
