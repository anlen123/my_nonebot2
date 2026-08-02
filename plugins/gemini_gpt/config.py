from pydantic import BaseModel
from typing import List, Dict
from nonebot import get_plugin_config


class Config(BaseModel):
    imgRoot: str = "D:\\nb2\\imgroot\\"


config = get_plugin_config(Config)
