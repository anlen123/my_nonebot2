from nonebot import on_command
from nonebot.rule import to_me,Rule
from nonebot.adapters.cqhttp import Bot, Event, MessageSegment, Message
import os
testx = on_command(cmd="tt",rule=to_me())

@testx.handle()
async def test_rev(bot: Bot, event: Event, state: dict):
    pass

