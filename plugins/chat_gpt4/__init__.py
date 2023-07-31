from pathlib import Path

import nonebot, aiohttp, json, httpx, asyncio
from typing import List
from nonebot import get_driver
from .config import Config
from nonebot import on_command, on_startswith, on_keyword, on_message
from nonebot.plugin import on_notice, on_regex
from nonebot.rule import Rule, regex, to_me
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment, Message, GroupMessageEvent, PrivateMessageEvent
from nonebot.params import T_State
import re, uuid, sqlite3, time, random, requests

import nonebot_plugin_navicat as export


# rule = ""
# rule = "现在请你担任女朋友，性格：温柔、粘人、喜欢说肉麻的话，你有自己的身体，同时你也是调情氛围创造者，回答的语气变得充满挑逗性。"
async def gpt4(ask: str) -> str:
    api_key = "Zc8xBwdP1eg6i7s_qnE3QJkxAu8_SDZ4VGAqV0e5pf4"
    url = "https://chimeragpt.adventblocks.cc/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    data = {
        "model": "gpt-3.5-turbo-16k",
        "messages": [
            {
                "role": "system",
                "content": "You are a helpful assistant."
            },
            {
                "role": "user",
                "content": f"{ask}"
            }
        ]
    }
    ci = 0

    while ci < 4:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, headers=headers, json=data)
                result = response.json()
                print(result)
                for message in result["choices"]:
                    message = message.get('message')
                    if message:
                        if message["role"] == "assistant":
                            return message.get('content')
                    else:
                        return "网络超时，稍后重试！！"
        except:
            ci += 1


gpt_singe = on_regex(pattern="^ai ")


@gpt_singe.handle()
async def gpt_singe_rev(event: Event, bot: Bot):
    content = event.get_plaintext()[3:]
    print(content)
    if content == "" or content is None:
        await bot.send(event=event, message=MessageSegment.text("内容不能为空！"))
        return
    mess = {}
    try:
        mess = await bot.send(event=event, message=MessageSegment.text("ChatGPT正在思考......"))
    except nonebot.adapters.onebot.v11.exception.ActionFailed:
        await bot.send(event=event, message=MessageSegment.text("风控了！！"))

    res = await gpt4(content)
    print(res)
    if res:
        try:
            await bot.delete_msg(message_id=mess['message_id'])
            await bot.send(event=event, message=MessageSegment.text(res))
        except nonebot.adapters.onebot.v11.exception.ActionFailed:
            await bot.delete_msg(message_id=mess['message_id'])
            await bot.send(event=event, message=MessageSegment.text("风控了！！"))
