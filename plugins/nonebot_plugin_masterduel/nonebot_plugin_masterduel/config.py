from pydantic import BaseModel
from nonebot import get_plugin_config


class Config(BaseModel):
    nonebot_plugin_masterduel_root_dir: str = "D:\\nb2\\my_nonebot2\\plugins\\nonebot_plugin_masterduel\\nonebot_plugin_masterduel"
    nonebot_plugin_masterduel_img_dir: str = "D:\\nb2\\my_nonebot2\\plugins\\nonebot_plugin_masterduel\\nonebot_plugin_masterduel\\img"
    nonebot_plugin_masterduel_img_card_dir: str = "D:\\MyCardLibrary\\ygopro2\\picture\\card"


config = get_plugin_config(Config)
