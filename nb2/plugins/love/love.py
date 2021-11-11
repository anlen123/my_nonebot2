from nonebot import on_command,on_startswith,on_keyword,on_message
from nonebot.plugin import on_notice, on_regex
from nonebot.rule import Rule, regex, to_me
from nonebot.adapters.cqhttp import Bot, Event, MessageSegment, Message, message
from nonebot.typing import T_State
import re
# def bool_img() -> Rule:
#     async def bool_img_(bot: "Bot", event: "Event", state: T_State) -> bool:
#         if event.get_type() != "message":
#             return False
#         return [ s.data['file'] for s in event.get_message() if s.type=="image" and "file" in s.data]
#     return Rule(bool_img_)
# 识别参数 并且给state 赋值

# love = on_message(rule=bool_img())

import nonebot
from .config import Config

global_config = nonebot.get_driver().config
imgRoot=global_config.dict()['imgroot']
love = on_startswith(msg="love", rule=to_me())

# love = on_regex(pattern="为什么$",rule=to_me())
@love.handle()
async def love_rev(bot: Bot, event: Event, state: dict):
    # x = clien.hgetall("yulu")
    await love.finish(message="我也爱你"+Message("[CQ:face,id=214][CQ:face,id=66]"), at_sender=True)
