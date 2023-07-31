import nonebot
from nonebot import on_message
from nonebot.rule import Rule
from nonebot.adapters.onebot.v11 import Bot, Event, Message

global_config = nonebot.get_driver().config
config = global_config.dict()

ban = ["傻逼", "sb", "煞笔", "傻B", "沙比", "笨b", "笨逼", "沙笔"]
name = ["机器人", "qqbot", "bot", "群主"]


def bool_sb_bot() -> Rule:
    async def bool_sb_bot_(event: "Event") -> bool:
        if event.get_type() != "message":
            return False
        msg = event.get_plaintext()
        if any(_ in msg for _ in ban) and any(_ in msg for _ in name):
            return True
        else:
            return False

    return Rule(bool_sb_bot_)


sb_bot = on_message(rule=bool_sb_bot())


@sb_bot.handle()
async def sb_bot_rev(bot: Bot, event: Event):
    user_id = event.get_user_id()
    msg = event.get_plaintext()
    for _ in name:
        msg = msg.replace(_, f"[CQ:at,qq={user_id}]")
    await bot.send(event=event, message=Message(msg))
