# import nonebot
from nonebot import get_driver, on_regex, on_message

from .config import Config
from nonebot.rule import Rule
from nonebot.adapters.onebot.v11 import Bot, Event, Message
from .data_source import get_av_data
import re

global_config = get_driver().config
config = Config(**global_config.dict())


def isAvBv() -> Rule:
    async def isisAvBv_(bot: "Bot", event: "Event") -> bool:
        if event.get_type() != "message":
            return False
        msg = str(event.get_plaintext())
        if re.findall("av(\d{1,12})|BV(1[A-Za-z0-9]{2}4.1.7[A-Za-z0-9]{2})", msg):
            return True
        return False

    return Rule(isisAvBv_)


biliav = on_message(rule=isAvBv())


@biliav.handle()
async def handle(bot: Bot, event: Event):
    print(f"{event.get_plaintext()=}")
    if not event.get_plaintext().strip():
        return

    avCode = re.search('av(\d{1,12})|BV(1[A-Za-z0-9]{2}4.1.7[A-Za-z0-9]{2})', str(event.get_message()))
    if not avCode:
        return
    rj = await get_av_data(avCode[0])
    await bot.send(event=event, message=rj)
