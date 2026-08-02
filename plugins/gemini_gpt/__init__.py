from google.genai import types
import httpx
import uuid
import os
import google.generativeai as genai_v1
import google.genai as genai_v2
from pathlib import Path

import nonebot
import json
from typing import List
from nonebot import get_driver
from nonebot import on_command, on_startswith, on_keyword, on_message
from nonebot.plugin import on_notice, on_regex
from nonebot.rule import Rule, regex, to_me
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment, Message, GroupMessageEvent, PrivateMessageEvent
from nonebot.params import T_State
import nonebot_plugin_navicat as export
import sqlite3
import PIL.Image
from .config import config
import requests
imgRoot = config.imgRoot
appkey = "AIzaSyAEwpfKamjMN70nxLofoC0rbXs84DQx9ak"


gemini_img = on_regex("^gmi$")


@gemini_img.handle()
async def gemini_img_hamdle(event: Event):
    msg = event.message
    if str(msg) != "gmi":
        await gemini_img.finish("后面不加参数,直接at我后,输入\"gmi\"即可.")


@gemini_img.got(key="url", prompt="请输入图片")
async def yulu_save_got_1(event: Event, tt: T_State):
    msg = event.message
    url = msg[0].data['url']
    tt['url'] = url
    user_id = event.user_id
    path_prefix = f"{imgRoot}QQbotFiles\gemini\{user_id}\\"
    if not os.path.exists(path_prefix):
        os.makedirs(path_prefix)
    if url:
        r = httpx.get(url)
        pa = f"{path_prefix}{uuid.uuid4()}.png"
        with open(pa, mode="wb") as f:
            f.write(r.content)
        tt["pa"] = pa
    else:
        await gemini_img.finish("好像出错了!!!")


@gemini_img.got(key="qu", prompt="请提出问题：")
async def yulu_save_got_2(bot: Bot, event: Event, tt: T_State):
    tt["qu"] = event.get_message()
    
    try:
        mess = await bot.send(event=event, message=MessageSegment.text("解析中..."))
    except nonebot.adapters.onebot.v11.exception.ActionFailed:
        await bot.send(event=event, message=MessageSegment.text("风控了！！"))
    res = genai_multi_turn_async_img(event.get_message(), tt['pa'])
    if res:
        try:
            await bot.delete_msg(message_id=mess['message_id'])
            if isinstance(event, GroupMessageEvent):
                await send_forward_msg_group(bot, event, 'qqbot', [res])
            else:
                await bot.send(event=event, message=MessageSegment.text(res))
        except nonebot.adapters.onebot.v11.exception.ActionFailed:
            await bot.delete_msg(message_id=mess['message_id'])
            await bot.send(event=event, message=MessageSegment.text("风控了！！"))


def genai_multi_turn_async_img(ms: str, image_path: str) -> list:
    global appkey
    image = PIL.Image.open(image_path)
    client_v2 = genai_v2.Client(
        api_key=appkey)
    response = client_v2.models.generate_content(
        model="gemini-2.0-flash-thinking-exp-01-21",
        contents=[f"{ms}", image])
    return response.text


async def genai_multi_turn_async(message: str, user_id: str, m: str = "gemini-2.0-pro-exp-02-05") -> list:
    global appkey
    genai_v1.configure(api_key=appkey)
    model = genai_v1.GenerativeModel(m)
    # 获取历史对话
    history = get_conversation_history(user_id)

    if history:
        messages = [{"role": "model", "parts": "You are a helpful assistant"}]
        messages += history
        messages.append({"role": "user", "parts": [message]})
    else:
        messages = [
            {"role": "model", "parts": "You are a helpful assistant"},
            {"role": "user", "parts": [message]}
        ]

    print(messages)
    response = await model.generate_content_async(messages)
    assistant_reply = response.text

    # 更新用户历史记录
    history.append({"role": "user", "parts": [message]})
    history.append({"role": "model", "parts": [assistant_reply]})
    update_conversation_history(user_id, history)

    return assistant_reply

gemini_singe = on_regex(pattern="^(gemini|gm|gmt) ")


@gemini_singe.handle()
async def deepseek_singe_rev(event: Event, bot: Bot):
    fmsg = ""
    is_gmt = False
    if event.get_plaintext().startswith("gemini "):
        content = event.get_plaintext()[7:]
        fmsg = "gemini-2.0-pro-exp-02-05正在思考......"
    if event.get_plaintext().startswith("gm "):
        content = event.get_plaintext()[3:]
        fmsg = "gemini-2.0-pro-exp-02-05正在思考......"
    if event.get_plaintext().startswith("gmt "):
        content = event.get_plaintext()[4:]
        fmsg = "gemini-2.5-pro-exp-03-25正在思考......"
        is_gmt = True
    if content == "" or content is None or content.strip() == "":
        await bot.send(event=event, message=MessageSegment.text("内容不能为空！"))
        return
    mess = {}
    try:
        mess = await bot.send(event=event, message=MessageSegment.text(fmsg))
    except nonebot.adapters.onebot.v11.exception.ActionFailed:
        await bot.send(event=event, message=MessageSegment.text("风控了！！"))

    if is_gmt:
        res = await genai_multi_turn_async(content, event.get_user_id(), "gemini-2.5-pro-exp-03-25")
    else:
        res = await genai_multi_turn_async(content, event.get_user_id())
    if res:
        try:
            await bot.delete_msg(message_id=mess['message_id'])
            if isinstance(event, GroupMessageEvent):
                await send_forward_msg_group(bot, event, 'qqbot', [res])
            else:
                await bot.send(event=event, message=MessageSegment.text(res))
        except nonebot.adapters.onebot.v11.exception.ActionFailed:
            await bot.delete_msg(message_id=mess['message_id'])
            await bot.send(event=event, message=MessageSegment.text("风控了！！"))

clear = on_regex(pattern="^gmclear$")


@clear.handle()
async def clear_conversation(event: Event, bot: Bot):
    user_id = str(event.user_id)

    # 从数据库中删除该用户的对话记录
    conn = sqlite3.connect('chat_gemini_history.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

    # 返回清除成功的提示
    await bot.send(event=event, message=MessageSegment.text("您的gnmini对话记录已被清除！"))

# 合并消息


async def send_forward_msg_group(bot: Bot, event: GroupMessageEvent, name: str, msgs: List[str], ):
    def to_json(msg):
        return {"type": "node", "data": {"name": name, "uin": bot.self_id, "content": msg}}

    messages = [to_json(msg) for msg in msgs]
    await bot.call_api("send_group_forward_msg", group_id=event.group_id, messages=messages)


# 初始化数据库
def init_db():
    conn = sqlite3.connect('chat_gemini_history.db')
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
    conn = sqlite3.connect('chat_gemini_history.db')
    cursor = conn.cursor()
    cursor.execute(
        "SELECT messages FROM conversations WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    print(json.loads(result[0]) if result else [])
    return json.loads(result[0]) if result else []

# 更新用户历史记录


def update_conversation_history(user_id: str, messages: list):
    conn = sqlite3.connect('chat_gemini_history.db')
    cursor = conn.cursor()
    cursor.execute('''
    INSERT OR REPLACE INTO conversations (user_id, messages)
    VALUES (?, ?)
    ''', (user_id, json.dumps(messages)))
    conn.commit()
    conn.close()


# gemini_fy_singe = on_regex(pattern="^(翻译|fy) ")


# @gemini_fy_singe.handle()
# async def gemini_fy_singe_rev(event: Event, bot: Bot):
#     if event.get_plaintext().startswith("fy ") or event.get_plaintext().startswith("翻译 "):
#         content = event.get_plaintext()[3:]
#     else:
#         return
#     res = await genai_fy_async(content)
#     if res:
#         if isinstance(event, GroupMessageEvent):
#             await send_forward_msg_group(bot, event, 'qqbot', [res])
#         else:
#             await bot.send(event=event, message=MessageSegment.text(res))
            

# async def genai_fy_async(message: str) -> list:
#     print(message)
#     global appkey
#     print(appkey)
#     genai_v1.configure(api_key=appkey)
#     model = genai_v1.GenerativeModel("gemini-2.0-pro-exp-02-05")
#     messages = [
#         {"role": "model", "parts": "You are a helpful assistant"},
#         {"role": "user", "parts": [
#             "你现在扮演翻译官，你可以准确的翻译日文，英文，韩文，英语为中文。当我输入中文的时候则翻译成英文，只需要给我结果即可，无需说多余的话。"]},
#         {"role": "model", "parts": "好的，请您输入您想要翻译的内容。"}
#     ]
#     messages.append({"role": "user", "parts": [message]})
#     print(messages)
#     response = await model.generate_content_async(messages)
#     assistant_reply = response.text
#     return assistant_reply
