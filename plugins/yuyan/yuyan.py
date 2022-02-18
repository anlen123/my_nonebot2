from nonebot import on_command
from nonebot.rule import to_me
from nonebot.adapters.cqhttp import Bot, Event, MessageSegment, Message
from pprint import pprint
import sys
import os

yuyan = on_command(cmd="super", priority=9, rule=to_me())


# 识别参数 并且给state 赋值
@yuyan.handle()
async def yuyan_rev(bot: Bot, event: Event, state: dict):
    msg = str(event.message).strip().split("\n")
    yu = msg[0].strip()
    if yu in ["py", "java", "c"]:
        state["yu"] = yu
        state['txt'] = "\n".join(msg[1:])
    else:
        await yuyan.finish("命令错误  例子: super py\nprint('233')")


# 调用
# @matcher.got(key, [prompt="请输入key"], [args_parser=function]):
# 指示 NoneBot 当 state 中不存在 key 时向用户发送 prompt 等待用户回复并赋值给 state[key]
@yuyan.got(key="yu", prompt="")
async def yuyan_do(bot: Bot, event: Event, state: dict):
    lanuage = state['yu']
    if lanuage == "py":
        s = state['txt']
        os.popen(f"echo {s}>a.py").read()
        print(os.popen("python a.py").read())


# 业务逻辑
async def yuyan_main(city: str):
    pass
