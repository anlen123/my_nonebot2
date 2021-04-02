from nonebot import on_command,on_startswith,on_keyword,on_message
from nonebot.plugin import on_regex
from nonebot.rule import regex, to_me
from nonebot.adapters.cqhttp import Bot, Event, MessageSegment, Message

# 识别参数 并且给state 赋值
# love = on_startswith(msg="love", priority=4, rule=to_me())
love = on_regex(pattern="^love$",rule=to_me())
@love.handle()
async def love_rev(bot: Bot, event: Event, state: dict):
    await love.finish(message="我也爱你"+Message("[CQ:face,id=214][CQ:face,id=66]"), at_sender=True)
    # await love.finish(message="我也爱你"+Message("[CQ:face,id=214]"), at_sender=True)
    # await love.finish(message="我也爱你"+MessageSegment.face(214),at_sender=True)
    # await bot.send(message="我也爱你"+Message("[CQ:face,id=214]"),at_sender=True)
    # await bot.send(message="我也爱你"+MessageSegment.face(214),at_sender=True)
