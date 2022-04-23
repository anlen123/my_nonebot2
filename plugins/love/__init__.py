from pathlib import Path

import nonebot
from typing import List
from nonebot import get_driver
from .config import Config
from nonebot import on_command, on_startswith, on_keyword, on_message
from nonebot.plugin import on_notice, on_regex
from nonebot.rule import Rule, regex, to_me
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment, Message, GroupMessageEvent
from nonebot.params import T_State, State
import re

_sub_plugins = set()
_sub_plugins |= nonebot.load_plugins(
    str((Path(__file__).parent / "plugins").
        resolve()))

global_config = nonebot.get_driver().config
config = global_config.dict()


def bool_img() -> Rule:
    async def bool_img_(bot: "Bot", event: "Event", state: T_State) -> bool:
        return False
        print("=========")
        for x in event.get_message():
            if x.type == 'forward':
                print(x.data['id'])
                print(await bot.get_msg(message_id=x.data['id']))
                print(x.type)
        print("=========")
        if event.get_type() != "message":
            return False
        return [s.data['file'] for s in event.get_message() if s.type == "image" and "file" in s.data]

    return Rule(bool_img_)


# 识别参数 并且给state 赋值
imgRoot = config.get('imgroot', "")

love = on_regex(pattern="^love$")
# love = on_message(rule=bool_img())

import base64


@love.handle()
async def love_rev(bot: Bot, event: Event):
    await bot.send(event, message="我也爱你")
    # with open("/root/QQbotFiles/pixivZip/97369334/97369334.gif", 'rb') as f:
    #     await bot.send(event, MessageSegment.image('base64://' + base64.b64encode(f.read()).decode()))


# 合并消息
async def send_forward_msg_group(
        bot: Bot,
        event: GroupMessageEvent,
        name: str,
        msgs: List[str],
):
    def to_json(msg):
        return {"type": "node", "data": {"name": name, "uin": bot.self_id, "content": msg}}

    messages = [to_json(msg) for msg in msgs]
    await bot.call_api(
        "send_group_forward_msg", group_id=event.group_id, messages=messages
    )
