# -*- coding: utf-8 -*-
import traceback
from typing import Dict
import json

from aiohttp.client_exceptions import ClientError

from nonebot import get_driver
from nonebot.params import ArgPlainText, Arg, CommandArg
from nonebot.plugin import on_command, on_message
from nonebot.rule import to_me
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, GroupMessageEvent, PrivateMessageEvent, Message
from nonebot.typing import T_State
from nonebot.utils import DataclassEncoder

from .ex import get_des as get_des_ex
from .iqdb import get_des as get_des_iqdb
from .saucenao import get_des as get_des_sau
from .ascii2d import get_des as get_des_asc
from .trace import get_des as get_des_trace
from .yandex import get_des as get_des_yandex

from .utils import limiter

global_config = get_driver().config
record_priority = getattr(global_config, "record_priority", 99)

async def get_des(url: str, mode: str):
    """
    :param url: 图片链接
    :param mode: 图源
    :return:
    """
    if mode == "iqdb":
        async for msg in get_des_iqdb(url):
            yield msg
    elif mode == "ex":
        async for msg in get_des_ex(url):
            yield msg
    elif mode == "trace":
        async for msg in get_des_trace(url):
            yield msg
    elif mode == "yandex":
        async for msg in get_des_yandex(url):
            yield msg
    elif mode.startswith("asc"):
        async for msg in get_des_asc(url):
            yield msg
    else:
        async for msg in get_des_sau(url):
            yield msg


setu = on_command("搜图", aliases={"search"})


@setu.handle()
async def handle_first_receive(event: MessageEvent, state: T_State, setu: Message = CommandArg()):
    if setu:
        state["setu"] = setu


@setu.got("setu", prompt="图呢？")
async def get_setu(bot: Bot,
                   event: MessageEvent,
                   msg: Message = Arg("setu")):
    mods = ["sau"]
    """
    发现没有的时候要发问
    :return:
    """
    for mod in mods:
        try:
            if msg[0].type == "image":
                await bot.send(event=event, message=f"{mod} 正在处理图片")
                url = msg[0].data["url"]  # 图片链接
                if not getattr(bot.config, "risk_control", None) or isinstance(event, PrivateMessageEvent):  # 安全模式
                    async for msgx in limiter(get_des(url, mod), getattr(bot.config, "search_limit", None) or 2):
                        await bot.send(event=event, message=msgx)
                else:
                    msgs: Message = sum(
                        [msg if isinstance(msg, Message) else Message(msgx) async for msgx in get_des(url, mod)])
                    dict_data = json.loads(json.dumps(msgs, cls=DataclassEncoder))
                    await bot.send_group_forward_msg(group_id=event.group_id,
                                                    messages=[
                                                        {
                                                            "type": "node",
                                                            "data": {
                                                                "name": event.sender.nickname,
                                                                "uin": event.user_id,
                                                                "content": [
                                                                    content
                                                                ]
                                                            }
                                                        }
                                                        for content in dict_data
                                                    ]
                                                    )

                # image_data: List[Tuple] = await get_pic_from_url(url)
                await bot.send(event=event,message=f"{mod}引擎 搜索完毕 hso")
            else:
                await setu.reject("这不是图,重来!")
        except (IndexError, ClientError):
            # await bot.send(event, traceback.format_exc())
            # await setu.finish("参数错误")
            await bot.send(event=event,message=f"{mod}引擎发生错误")
