from pydantic import BaseModel
from nonebot import get_plugin_config


class Config(BaseModel):
    pokemon_path: str = "D:\nb2\my_nonebot2\data\pokemon\data"
    pokemon_img_path: str = "D:\nb2\my_nonebot2\data\pokemon\img"
    pokemon_texing_path: str = "D:\nb2\my_nonebot2\data\pokemon\texing"


config = get_plugin_config(Config)
