from pathlib import Path

import nonebot
from nonebot.plugin import on_notice, on_regex
from nonebot.adapters.cqhttp import Bot, Event, MessageSegment, Message, event, message
import os 
import nonebot

_sub_plugins = set()
_sub_plugins |= nonebot.load_plugins(
    str((Path(__file__).parent / "plugins").
    resolve()))


global_config = nonebot.get_driver().config
imgRoot=global_config.dict()['imgroot']

sendImg = on_regex(pattern="^send\ ")

@sendImg.handle()
async def love_rev(bot: Bot, event: Event, state: dict):
    msg=f"{imgRoot}"+str(event.get_message())[5:]
    print(msg)
    if os.path.exists(msg):
        await bot.send(event=event,message=MessageSegment.image(f"file:///{msg}"))
    else:
        await bot.send(event=event,message="错误: 文件不存在,或者不是图片")
