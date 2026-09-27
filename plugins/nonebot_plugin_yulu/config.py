r"""nonebot_plugin_yulu 的插件配置。

imgRoot —— 语录图片的根目录，沿用与其它插件一致的「D:\nb2\imgroot\」约定。
"""

from __future__ import annotations

from nonebot import get_plugin_config
from pydantic import BaseModel


class Config(BaseModel):
    """字段名即 .env 里的配置项名（imgRoot）。"""

    imgRoot: str = "D:\\nb2\\imgroot\\"


config = get_plugin_config(Config)
