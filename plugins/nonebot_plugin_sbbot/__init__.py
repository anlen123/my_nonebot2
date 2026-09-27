"""sbbot —— 被骂「傻逼机器人」时，把称呼换成 @对方再复读一遍

用法：
  群消息里同时出现 ban 词与 name 词（例如「傻逼机器人」）时触发，
  回复内容 = 原消息，但把「机器人 / qqbot / bot / 群主」逐个替换成 @发送者。

说明：两个词表按下面的书写顺序做字符串替换，顺序会影响结果（例如先换掉 qqbot，
后面的 bot 就不会再命中），调整时请留意。
"""

from __future__ import annotations

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, Event, Message
from nonebot.rule import Rule

# 骂人词：命中任意一个即算
BAN_WORDS = ("傻逼", "sb", "煞笔", "傻B", "沙比", "笨b", "笨逼", "沙笔")
# 被骂的对象；回复时逐个替换成 @发送者，顺序会影响替换结果
NAME_WORDS = ("机器人", "qqbot", "bot", "群主")


def bool_sb_bot() -> Rule:
    """消息里同时出现骂人词与被骂对象时命中。"""

    async def bool_sb_bot_(event: Event) -> bool:
        if event.get_type() != "message":
            return False
        text = event.get_plaintext()
        return any(word in text for word in BAN_WORDS) and any(
            word in text for word in NAME_WORDS
        )

    return Rule(bool_sb_bot_)


sb_bot = on_message(rule=bool_sb_bot())


@sb_bot.handle()
async def sb_bot_rev(bot: Bot, event: Event) -> None:
    user_id = event.get_user_id()
    text = event.get_plaintext()
    for word in NAME_WORDS:
        text = text.replace(word, f"[CQ:at,qq={user_id}]")
    await bot.send(event=event, message=Message(text))
