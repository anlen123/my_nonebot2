from nonebot import on_command,on_startswith,on_keyword,on_message
from nonebot.plugin import on_notice, on_regex
from nonebot.rule import Rule, regex, to_me
from nonebot.adapters.cqhttp import Bot, Event, MessageSegment, Message, event, message
from nonebot.typing import T_State
import os 
sendImg = on_regex(pattern="^send\ ")
import nonebot
from .config import Config

global_config = nonebot.get_driver().config
imgRoot=global_config.dict()['imgroot']

@sendImg.handle()
async def love_rev(bot: Bot, event: Event, state: dict):
    msg=f"{imgRoot}"+str(event.get_message())[5:]
    print(msg)
    if os.path.exists(msg):
        await bot.send(event=event,message=MessageSegment.image(f"file:///{msg}"))
    else:
        await bot.send(event=event,message="错误: 文件不存在,或者不是图片")
