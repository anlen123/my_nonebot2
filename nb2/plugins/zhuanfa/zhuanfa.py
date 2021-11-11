from nonebot import on_command
from nonebot.rule import to_me,Rule 
from nonebot.adapters.cqhttp import Bot, Event, MessageSegment, Message

zhuanfa = on_command(cmd="fb")
@zhuanfa.handle()
async def zhuanfa_rev(bot: Bot, event: Event, state: dict):
    msg = event.message
    await bot.send_msg(message_type="group",message=msg,group_id=732370841)