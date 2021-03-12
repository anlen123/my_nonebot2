from nonebot import on_command,on_startswith
from nonebot.rule import to_me
from nonebot.adapters.cqhttp import Bot, Event, MessageSegment, Message
love = on_startswith(msg="love", priority=4, rule=to_me())
from datetime import datetime

# 识别参数 并且给state 赋值

@love.handle()
async def love_rev(bot: Bot, event: Event, state: dict):
    await love.finish(message="我也爱你", at_sender=True)

