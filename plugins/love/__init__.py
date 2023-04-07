from pathlib import Path

import nonebot
from typing import List
from nonebot import get_driver
from .config import Config
from nonebot import on_command, on_startswith, on_keyword, on_message
from nonebot.plugin import on_notice, on_regex
from nonebot.rule import Rule, regex, to_me
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment, Message, GroupMessageEvent
from nonebot.params import T_State
import re


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

# love = on_message(rule=bool_img())


love = on_regex(pattern="^love$")
@love.handle()
async def love_rev(bot: Bot, event: Event):
    await bot.send(event, message="我也爱你")
    # with open("/root/QQbotFiles/pixivZip/97369334/97369334.gif", 'rb') as f:
    #     await bot.send(event, MessageSegment.image('base64://' + base64.b64encode(f.read()).decode()))

qqbot_des = on_regex(pattern="^菜单$", rule=to_me())

@qqbot_des.handle()
async def qqbot_des_rev(bot: Bot, event: Event):
    msg = """qqbot使用说明如下：
1.love, 会给你回复love
2.st, 会发一张色图
3.sx NB, 通过缩写查全意
4.xr https://baidu.com, 渲染网页成图片
5.yl, 发送上传过的语录，使用上传语录，可以上传图片
6.输入b站的av,或者BV号，给出视频的一些基本信息
7.搜图
8.pixiv pid, 懂的都懂
9.ygo 闪刀，游戏王查卡器
10.ai 你的问题，chatGPT回答你的问题
-------后续新加功能会补充
    """
    await bot.send(event, message=msg)
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
