from nonebot import get_plugin_config
from pydantic import BaseModel, Field


class Config(BaseModel):
    emojimix_explicit: bool = Field(
        default=True, description="是否启用显式表情合成（如 😂+🥺）。"
    )
    emojimix_auto: bool = Field(
        default=True,
        description="是否自动触发表情合成。启用后，用户发送的纯文本中包含两个相邻的可合成 emoji 时，会自动发送合成图片。",
    )
    emojimix_cd: int = Field(
        default=60, description="每个用户的冷却时间（秒）。设为 0 则不限制。"
    )


plugin_config = get_plugin_config(Config)
