"""gemini_gpt 插件配置：图片存放根目录。"""

from nonebot import get_plugin_config
from pydantic import BaseModel


class Config(BaseModel):
    """插件配置，可由 .env 的 imgRoot 覆盖（名字大小写不敏感）。"""

    imgRoot: str = "D:\\nb2\\imgroot\\"  # 图片根目录，按 QQ 号在其下分文件夹


config = get_plugin_config(Config)
