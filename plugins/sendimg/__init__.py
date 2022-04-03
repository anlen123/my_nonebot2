from pathlib import Path

import nonebot
from nonebot.plugin import on_notice, on_regex
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment, Message, event, message
import os
import nonebot

global_config = nonebot.get_driver().config
imgRoot = global_config.dict().get('imgroot', "")

sendImg = on_regex(pattern="^send\ ")


@sendImg.handle()
async def love_rev(bot: Bot, event: Event):
    msg = f"{imgRoot}" + str(event.get_message())[5:]
    if os.path.exists(msg):
        await bot.send(event=event, message=MessageSegment.image(f"file:///{msg}"))
    else:
        await bot.send(event=event, message="错误: 文件不存在,或者不是图片")
