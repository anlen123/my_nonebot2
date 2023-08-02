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

async def gpt3(content: str):
    url = "https://beta.chatmindai.net/api/chat-process"

    payload = json.dumps({
        "message": f"{content}。Let's think step by step。",
        "chatid": "",
        "roleid": "7ssk4771icjwm3jyebj1690584450298",
        "isContextEnabled": 0
    })

    headers = {
        'Accept': '*/*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Authorization': 'Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJwaG9uZW51bSI6IjEzNjc2NTkxNTg5IiwiaWQiOjM1MDQyLCJzdWIiOiJBdXRoZW50aWNhdGlvbiIsImV4cCI6MTY5MTQwODA1NiwianRpIjoiMTZkYTRjMDBkZGYwNGIzYTllOTA2YjJhY2Y5MDcxYmMifQ.n4pQ0szJZSk-q2tp554RAZhGX8Ty0L73opJjBqH1PX4',
        'Connection': 'keep-alive',
        'Content-Type': 'application/json',
        'Origin': 'https://beta.chatmindai.net',
        'Referer': 'https://beta.chatmindai.net/chat',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
        'sec-ch-ua': '"Not.A/Brand";v="8", "Chromium";v="114", "Google Chrome";v="114"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"macOS"'
    }

    msg = ""

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, data=payload) as response:
            while True:
                line = await response.content.readline()
                if not line:
                    break
                msg += str(line.decode('utf-8'))
    print(msg)
    return msg


async def gpt4(content: str):
    url = "https://beta.chatmindai.net/api/chat-process"

    payload = json.dumps({
        "message": f"{content}",
        "chatid": "",
        "roleid": "1cmcz8o06s5t0x5ag941690583530866",
        "isContextEnabled": 0
    })

    headers = {
        'Accept': '*/*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Authorization': 'Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJwaG9uZW51bSI6IjEzNjc2NTkxNTg5IiwiaWQiOjM1MDQyLCJzdWIiOiJBdXRoZW50aWNhdGlvbiIsImV4cCI6MTY5MTQwODA1NiwianRpIjoiMTZkYTRjMDBkZGYwNGIzYTllOTA2YjJhY2Y5MDcxYmMifQ.n4pQ0szJZSk-q2tp554RAZhGX8Ty0L73opJjBqH1PX4',
        'Connection': 'keep-alive',
        'Content-Type': 'application/json',
        'Origin': 'https://beta.chatmindai.net',
        'Referer': 'https://beta.chatmindai.net/chat',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
        'sec-ch-ua': '"Not.A/Brand";v="8", "Chromium";v="114", "Google Chrome";v="114"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"macOS"'
    }

    msg = ""

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, data=payload) as response:
            while True:
                line = await response.content.readline()
                if not line:
                    break
                msg += str(line.decode('utf-8'))
    print(msg)
    return msg


# async def gpt3(ask: str) -> str:
#     api_key = "Zc8xBwdP1eg6i7s_qnE3QJkxAu8_SDZ4VGAqV0e5pf4"
#     url = "https://chimeragpt.adventblocks.cc/api/v1/chat/completions"
#     headers = {
#         "Authorization": f"Bearer {api_key}",
#         "Content-Type": "application/json",
#     }
#     data = {
#         "model": "gpt-3.5-turbo-16k",
#         "messages": [
#             {
#                 "role": "system",
#                 "content": f"You are an advanced AI language model that can generate human-like text responses based on the prompts you receive. Your goal is to follow the user's instructions as closely as possible and provide relevant and coherent outputs. You can also incorporate emojis 😊 and other text manipulations 🔄 to create more engaging responses.Let's think step by step"
#             },
#             {
#                 "role": "user",
#                 "content": f"{ask}"
#             }
#         ]
#     }
#     ci = 0
#
#     while ci < 4:
#         try:
#             async with httpx.AsyncClient() as client:
#                 response = await client.post(url, headers=headers, json=data)
#                 result = response.json()
#                 for message in result["choices"]:
#                     message = message.get('message')
#                     if message:
#                         if message["role"] == "assistant":
#                             ret = message.get('content')
#                             print(ret)
#                             return ret
#                     else:
#                         return "网络超时，稍后重试！！"
#         except:
#             ci += 1


gpt3_singe = on_regex(pattern="^gpt3 ")


@gpt3_singe.handle()
async def gpt3_singe_rev(event: Event, bot: Bot):
    content = event.get_plaintext()[5:]
    print(content)
    if content == "" or content is None:
        await bot.send(event=event, message=MessageSegment.text("内容不能为空！"))
        return
    mess = {}
    try:
        mess = await bot.send(event=event, message=MessageSegment.text("ChatGPT正在思考......"))
    except nonebot.adapters.onebot.v11.exception.ActionFailed:
        await bot.send(event=event, message=MessageSegment.text("风控了！！"))

    res = await gpt3(content)
    print(res)
    if res:
        try:
            await bot.delete_msg(message_id=mess['message_id'])
            await bot.send(event=event, message=MessageSegment.text(res))
        except nonebot.adapters.onebot.v11.exception.ActionFailed:
            await bot.delete_msg(message_id=mess['message_id'])
            await bot.send(event=event, message=MessageSegment.text("风控了！！"))


gpt4_singe = on_regex(pattern="^gpt4 ")


@gpt4_singe.handle()
async def gpt4_singe_rev(event: Event, bot: Bot):
    content = event.get_plaintext()[5:]
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


gpt_count = on_regex(pattern="^gpt_count$")


@gpt_count.handle()
async def gpt_count_rev(event: Event, bot: Bot):
    data3 = await gpt_count(3)
    count3 = json.loads(data3).get('data').get('remain')

    data4 = await gpt_count(4)
    count4 = json.loads(data4).get('data').get('remain')
    if count3 and count4:
        try:
            await bot.send(event=event, message=MessageSegment.text(f"gpt3剩余次数:{count3}\ngpt4剩余次数:{count4}"))
        except nonebot.adapters.onebot.v11.exception.ActionFailed:
            await bot.send(event=event, message=MessageSegment.text("风控了！！"))


async def gpt_count(n: int):
    url = f"https://beta.chatmindai.net/api/apiCount/gpt{str(n)}"
    print(url)
    headers = {
        'Authorization': 'Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJwaG9uZW51bSI6IjEzNjc2NTkxNTg5IiwiaWQiOjM1MDQyLCJzdWIiOiJBdXRoZW50aWNhdGlvbiIsImV4cCI6MTY5MTQwODA1NiwianRpIjoiMTZkYTRjMDBkZGYwNGIzYTllOTA2YjJhY2Y5MDcxYmMifQ.n4pQ0szJZSk-q2tp554RAZhGX8Ty0L73opJjBqH1PX4',
        'Referer': 'https://beta.chatmindai.net/profile',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            print(await response.text())
            return await response.text()
