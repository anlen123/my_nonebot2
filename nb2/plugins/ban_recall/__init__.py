from pathlib import Path

import nonebot
from nonebot import get_driver
from nonebot.plugin import on_notice
from nonebot.rule import Rule
from nonebot.adapters.cqhttp import Bot, Event,  Message
from nonebot.typing import T_State
import time

from .config import Config

global_config = get_driver().config
config = Config(**global_config.dict())

# Export something for other plugin
# export = nonebot.export()
# export.foo = "bar"

# @export.xxx
# def some_function():
#     pass

_sub_plugins = set()
_sub_plugins |= nonebot.load_plugins(
    str((Path(__file__).parent / "plugins").
    resolve()))


def bool_recall() ->Rule:
    async def bool_chehui_(bot: "Bot",event: "Event",state: T_State) -> bool:
        if event.get_type() != "notice":
            return False
        msg_dict = event.dict()
        # if msg_dict['user_id'] in [1761512493,1928994748,1793268622]:
        if msg_dict['user_id'] in [1928994748,1793268622]:
            return False
        if msg_dict['notice_type'] in ['friend_recall','group_recall'] :
            if 'message_id' in msg_dict:
                msg_id =msg_dict['message_id']
            state['msg_id'] = msg_id
            return True
        return False
    return Rule(bool_chehui_)
recall = on_notice(rule=bool_recall())

@recall.handle()
async def chehui_test(bot: Bot, event: Event,state: dict):
    msg_id = state['msg_id']
    msg = await bot.get_msg(message_id=msg_id)
    timeArray = time.localtime(msg['time'])
    time_now = time.strftime("%Y-%m-%d %H:%M:%S", timeArray)
    print(msg)
    await bot.send(event,message=Message(msg['message'])+"\n发送时间: "+time_now+"\n发送人: "+ str(msg['sender']['nickname']))

