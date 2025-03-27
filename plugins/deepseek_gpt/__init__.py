from pathlib import Path

import nonebot
import aiohttp
import json
import httpx
import asyncio
from typing import List
from nonebot import get_driver
from nonebot import on_command, on_startswith, on_keyword, on_message
from nonebot.plugin import on_notice, on_regex
from nonebot.rule import Rule, regex, to_me
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment, Message, GroupMessageEvent, PrivateMessageEvent
from nonebot.params import T_State
import nonebot_plugin_navicat as export
from openai import AsyncOpenAI
import sqlite3

# 在启动时初始化数据库


# 初始化数据库
def init_db():
    conn = sqlite3.connect('chat_history.db')
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS conversations (
        user_id TEXT PRIMARY KEY,
        messages TEXT
    )
    ''')
    conn.commit()
    conn.close()


init_db()

# 获取用户历史记录


def get_conversation_history(user_id: str) -> list:
    conn = sqlite3.connect('chat_history.db')
    cursor = conn.cursor()
    cursor.execute(
        "SELECT messages FROM conversations WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    print(json.loads(result[0]) if result else [])
    return json.loads(result[0]) if result else []

# 更新用户历史记录


def update_conversation_history(user_id: str, messages: list):
    conn = sqlite3.connect('chat_history.db')
    cursor = conn.cursor()
    cursor.execute('''
    INSERT OR REPLACE INTO conversations (user_id, messages)
    VALUES (?, ?)
    ''', (user_id, json.dumps(messages)))
    conn.commit()
    conn.close()





# async def deepseek(message: str, user_id: str) -> list:
#     client = AsyncOpenAI(api_key="sk-4c68209adb754325aa5e70a617e7580c",
#                          base_url="https://api.deepseek.com")

#     # 获取历史对话
#     history = get_conversation_history(user_id)
#     if history:
#         messages = [{"role": "system", "content": "You are a helpful assistant"}
#                     ] + history + [{"role": "user", "content": message}]
#     else:
#         messages = [
#             {"role": "system", "content": "You are a helpful assistant"},
#             {"role": "user", "content": message}
#         ]
#     # 请求API
#     print(f"messages={messages}")
#     response = await client.chat.completions.create(
#         model="deepseek-chat",
#         messages=messages,
#         max_tokens=1024,
#         temperature=0.7,
#         stream=False
#     )

#     assistant_reply = response.choices[0].message.content

#     # 更新用户历史记录
#     history.append({"role": "user", "content": message})
#     history.append({"role": "assistant", "content": assistant_reply})
#     update_conversation_history(user_id, history)

#     return assistant_reply

async def deepseek(message: str, user_id: str, model: str) -> list:
    client = AsyncOpenAI(api_key="77b1cd88-22d3-4c9e-9091-8394d5bbfcab",
                         base_url="https://ark.cn-beijing.volces.com/api/v3")

    # 获取历史对话
    history = get_conversation_history(user_id)
    if history:
        messages = [{"role": "system", "content": "You are a helpful assistant"}
                    ] + history + [{"role": "user", "content": message}]
    else:
        messages = [
            {"role": "system", "content": "You are a helpful assistant"},
            {"role": "user", "content": message}
        ]
    # 请求API
    response = await client.chat.completions.create(
        model=f"{model}",
        messages=messages,
        max_tokens=1024,
        temperature=0.7,
        stream=False
    )

    assistant_reply = response.choices[0].message.content

    # 更新用户历史记录
    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": assistant_reply})
    update_conversation_history(user_id, history)

    return assistant_reply



deepseek_singe = on_regex(pattern="^ds3 ")
deepseek_singe_v2 = on_regex(pattern="^dsr ")


@deepseek_singe.handle()
@deepseek_singe_v2.handle()
async def deepseek_singe_rev(event: Event, bot: Bot):

    model = ""
    if event.get_plaintext().startswith("ds3 "):
        content = event.get_plaintext()[4:]
        model = "deepseek-v3-241226"
    if event.get_plaintext().startswith("dsr "):
        content = event.get_plaintext()[4:]
        model = "deepseek-r1-250120"
    if content == "" or content is None or content.strip() == "":
        await bot.send(event=event, message=MessageSegment.text("内容不能为空！"))
        return
    mess = {}
    try:
        mess = await bot.send(event=event, message=MessageSegment.text(f"{model}正在思考......"))
    except nonebot.adapters.onebot.v11.exception.ActionFailed:
        await bot.send(event=event, message=MessageSegment.text("风控了！！"))

    res = await deepseek(content, event.get_user_id(), model)
    if res:
        try:
            await bot.delete_msg(message_id=mess['message_id'])
            res = str(res).strip()
            if isinstance(event, GroupMessageEvent):
                await send_forward_msg_group(bot, event, 'qqbot', [res])
            else:
                await bot.send(event=event, message=MessageSegment.text(res))
        except nonebot.adapters.onebot.v11.exception.ActionFailed:
            await bot.delete_msg(message_id=mess['message_id'])
            await bot.send(event=event, message=MessageSegment.text("风控了！！"))


clear = on_regex(pattern="^dsclear$")


@clear.handle()
async def clear_conversation(event: Event, bot: Bot):
    user_id = str(event.user_id)

    # 从数据库中删除该用户的对话记录
    conn = sqlite3.connect('chat_history.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    

    # 返回清除成功的提示
    await bot.send(event=event, message=MessageSegment.text("您的deepseek对话记录已被清除！"))
    


# 合并消息
async def send_forward_msg_group(bot: Bot, event: GroupMessageEvent, name: str, msgs: List[str], ):
    def to_json(msg):
        return {"type": "node", "data": {"name": name, "uin": bot.self_id, "content": msg}}

    messages = [to_json(msg) for msg in msgs]
    await bot.call_api("send_group_forward_msg", group_id=event.group_id, messages=messages)

ds_fy_singe = on_regex(pattern="^(翻译|fy) ")


@ds_fy_singe.handle()
async def ds_fy_singe_rev(event: Event, bot: Bot):
    if event.get_plaintext().startswith("fy ") or event.get_plaintext().startswith("翻译 "):
        content = event.get_plaintext()[3:]
    else:
        return
    res = await deepseek_fy(content)
    msg = f"翻译句子：{content}\n\n翻译后：{res}"
    await bot.send(event=event, message=msg)


async def deepseek_fy(content: str) -> list:
    client = AsyncOpenAI(api_key="77b1cd88-22d3-4c9e-9091-8394d5bbfcab",
                         base_url="https://ark.cn-beijing.volces.com/api/v3")
    messages = [
        {"role": "system", "content": "你现在扮演翻译官，你可以准确的翻译日文，英文，韩文，英语为中文。当我输入中文的时候则翻译成英文，只需要给我结果即可，无需说多余的话。"},
        {"role": "user", "content": content}
    ]
    # 请求API
    response = await client.chat.completions.create(
        model="deepseek-r1-250120",
        messages=messages,
        max_tokens=1024,
        temperature=0.7,
        stream=False
    )

    assistant_reply = response.choices[0].message.content
    return str(assistant_reply).strip()
