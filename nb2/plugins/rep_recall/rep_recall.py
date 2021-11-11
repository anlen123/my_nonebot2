from nonebot.plugin import on_message, on_notice
from nonebot.rule import Rule
from nonebot.adapters.cqhttp import Bot, Event,  Message, message
from nonebot.typing import T_State
import re
def test() -> Rule:
    async def bool_recall_rep(bot: "Bot", event: "Event", state: T_State) -> bool:
        if not event.is_tome():
            return False
        if event.get_type() != "message":
            return False
        msg_dict = event.dict()
        msg_id = msg_dict['raw_message']
        id = re.findall('\[CQ\:reply\,id\=(-{0,1}\d+)\]', msg_id)
        if id :
            state["id"] = id[0]
            msgg = msg_id.split(" ")[-1]
            if msgg=="撤回":
                return True
        return False
    return Rule(bool_recall_rep)


recall_rep = on_message(rule=test())


@recall_rep.handle()
async def recall_rep(bot: Bot, event: Event, state: dict):
    msg_id = state['id']
    try:
        await bot.delete_msg(message_id=msg_id,self_id=bot.self_id)
    except:
        await bot.send(message="撤回失败")
