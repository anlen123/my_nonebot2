from nonebot import on_command, on_message, on_regex, on_startswith
from nonebot.rule import to_me, Rule
from nonebot.adapters.cqhttp import Bot, Event, MessageSegment, Message

null = on_command(cmd="", priority=5, block=True, rule=to_me())


# 识别参数 并且给state 赋值
@null.handle()
async def null_rev(bot: Bot, event: Event, state: dict):
    print('空参数')
    # await null.finish("我不能识别这个命令")
