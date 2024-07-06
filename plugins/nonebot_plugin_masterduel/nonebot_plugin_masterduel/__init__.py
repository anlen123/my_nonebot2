from typing import List, Any
from .config import Config
from nonebot.plugin import on_notice, on_regex
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment, Message, GroupMessageEvent
import aiohttp, asyncio, json, nonebot, re, httpx
from nonebot import get_driver
from model.Card import Ygo_Card

global_config = get_driver().config
config = global_config.dict()

master_duel = on_regex(pattern="^ck ")


@master_duel.handle()
async def master_duel_rev(bot: Bot, event: Event):
    await bot.send(event, MessageSegment.text("没有查询到卡片"))

async def get_send_msg(card:Ygo_Card) -> str:
    await "str"
    
    
    
def is_int(n: str):
    try:
        float_n = float(n)
        int_n = int(float_n)
    except ValueError as e:
        return False
    else:
        return float_n == int_n

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