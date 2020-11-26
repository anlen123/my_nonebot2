from nonebot import on_command, on_startswith
from nonebot.adapters.cqhttp import Bot, Event, MessageSegment, Message
import sys
import os

sys.path.insert(0, os.getcwd())
print(sys.path)
import tiquzhilian

lanzouyun = on_startswith(msg="lanzouyun")


# 识别参数 并且给state 赋值

@lanzouyun.handle()
async def lanzouyun_rev(bot: Bot, event: Event, state: dict):
    msg = str(event.message).strip().split(" ")[1]
    print(msg)
    url = tiquzhilian.main(msg)
    # url = "12333"
    if url:
        await lanzouyun.finish(message=url, at_sender=True)
    else:
        await lanzouyun.finish(messages="解析出错", at_sender=True)
