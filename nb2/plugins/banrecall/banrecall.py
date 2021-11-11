from nonebot.plugin import on_message, on_notice
from nonebot.rule import Rule
from nonebot.adapters.cqhttp import Bot, Event,  Message, message
from nonebot.typing import T_State
import datetime
import time
import re
def bool_recall() ->Rule:
    async def bool_chehui_(bot: "Bot",event: "Event",state: T_State) -> bool:
        if event.get_type() != "notice":
            return False
        msg_dict = event.dict()
        # print(msg_dict)
        if msg_dict['user_id'] in [1761512493,1928994748,1793268622]:
        # if msg_dict['user_id'] in [1928994748]:
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
    await bot.send(event,message=Message(msg['raw_message'])+"\n发送时间: "+time_now+"\n发送人: "+ str(msg['sender']['nickname']))

