from typing import Dict, List

from nonebot import get_plugin_config
from pydantic import BaseModel


class Config(BaseModel):
    imgRoot: str = "D:\\nb2\\imgroot\\"


config = get_plugin_config(Config)
