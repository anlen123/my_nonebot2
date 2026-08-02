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
import re, uuid, sqlite3, time, random, requests, openai, http.client, meilisearch

import nonebot_plugin_navicat as export

# rule = ""
# rule = "现在请你担任女朋友，性格：温柔、粘人、喜欢说肉麻的话，你有自己的身体，同时你也是调情氛围创造者，回答的语气变得充满挑逗性。"

global_config = get_driver().config
config = global_config.dict()
openai.api_key = config.get('gpt_api_key')
api_key = config.get('gpt_api_key')

client = meilisearch.Client('http://localhost:7700')


def chat_by_rule(messages: list, model_type: int):
    openai.api_base = "https://api.chatanywhere.cn"
    """为提供的对话消息创建新的回答 (流式传输)

    Args:
        messages (list): 完整的对话消息
        api_key (str): OpenAI API 密钥

    Returns:
        tuple: (results, error_desc)
    """
    if model_type == 3:
        model = "gpt-3.5-turbo"
    else:
        model = "gpt-4"
    try:
        response = openai.ChatCompletion.create(
            model=model,
            messages=messages,
            stream=True,
        )
        completion = {'role': '', 'content': ''}
        for event in response:
            if event['choices'][0]['finish_reason'] == 'stop':
                # print(f'收到的完成数据: {completion}')
                break
            for delta_k, delta_v in event['choices'][0]['delta'].items():
                print(f'{delta_v}', end='')
                completion[delta_k] += delta_v
        return completion.get("content")
    except Exception as err:
        return f'OpenAI API 异常: {err}'


def get_session_id(event: Event):
    if isinstance(event, GroupMessageEvent):
        return int(str(event.get_user_id()) + str(event.group_id))
    else:
        return int(event.get_user_id())


class CHAT:
    def __init__(self, id: int, msg: str):
        self.id = id
        self.msg = msg

    def json_format(self):
        print(self.id)
        print(self.msg)
        return {
            "id": self.id,
            "msg": self.msg,
        }


async def chat(message: str, event: Event) -> list:
    id = get_session_id(event)
    client.create_index("chat_history")
    chat_history = client.get_index("chat_history")
    rule = "You are an advanced AI language model that can generate human-like text responses based on the prompts you receive. Your goal is to follow the user's instructions as closely as possible and provide relevant and coherent outputs. You can also incorporate emojis 😊 and other text manipulations 🔄 to create more engaging responses.Let's think step by step"
    if is_include_id_by_index(id, chat_history):
        exits_chat_history = chat_history.get_document(id)
        msg = eval(str(exits_chat_history.msg))
        msg.append({'role': 'user', 'content': f'{message}'})
    else:
        msg = [{'role': 'system', 'content': f'{rule}'}, {'role': 'user', 'content': f'{message}'}]

    ai_return = chat_by_rule(msg, 3)
    msg.append({'role': 'assistant', 'content': f'{ai_return}'})
    chat_history.delete_document(id)
    chat_history.add_documents_json(CHAT(id, msg).json_format())

    return ai_return


async def chat4(message: str, event: Event) -> list:
    id = get_session_id(event)
    client.create_index("chat_history")
    chat_history = client.get_index("chat_history")
    if is_include_id_by_index(id, chat_history):
        exits_chat_history = chat_history.get_document(id)
        msg = eval(str(exits_chat_history.msg))
        msg.append({'role': 'user', 'content': f'{message}'})
    else:
        msg = [{'role': 'user', 'content': f'{message}'}]

    ai_return = chat_by_rule(msg, 4)
    msg.append({'role': 'assistant', 'content': f'{ai_return}'})
    chat_history.delete_document(id)
    chat_history.add_documents_json(CHAT(id, msg).json_format())

    return ai_return


async def gpt3(message: str) -> list:
    rule = "You are an advanced AI language model that can generate human-like text responses based on the prompts you receive. Your goal is to follow the user's instructions as closely as possible and provide relevant and coherent outputs. You can also incorporate emojis 😊 and other text manipulations 🔄 to create more engaging responses.Let's think step by step"
    messages = [{'role': 'system', 'content': f'{rule}'}, {'role': 'user', 'content': f'{message}'}]
    return chat_by_rule(messages, 3)


async def gpt4(message: str) -> list:
    messages = [{'role': 'user', 'content': f'{message}'}]
    return chat_by_rule(messages, 4)


def is_include_id_by_index(id: str, index: meilisearch.models.document.Document):
    try:
        index.get_document(id)
        return True
    except meilisearch.errors.MeilisearchApiError as ee:
        return False


clear_chat = on_regex(pattern="^clear$")


@clear_chat.handle()
async def clear_chat_rev(event: Event, bot: Bot):
    x = client.get_index("chat_history")
    id = get_session_id(event)
    x.delete_document(id)
    await bot.send(event=event, message="清除chat聊天成功")


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
    if res:
        try:
            await bot.delete_msg(message_id=mess['message_id'])
            await bot.send(event=event, message=MessageSegment.text(res))
        except nonebot.adapters.onebot.v11.exception.ActionFailed:
            await bot.delete_msg(message_id=mess['message_id'])
            await bot.send(event=event, message=MessageSegment.text("风控了！！"))


chat_singe = on_regex(pattern="^chat ")


@chat_singe.handle()
async def chat_singe_rev(event: Event, bot: Bot):
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

    res = await chat(content, event)
    if res:
        try:
            await bot.delete_msg(message_id=mess['message_id'])
            await bot.send(event=event, message=MessageSegment.text(res))
        except nonebot.adapters.onebot.v11.exception.ActionFailed:
            await bot.delete_msg(message_id=mess['message_id'])
            await bot.send(event=event, message=MessageSegment.text("风控了！！"))


chat_singe4 = on_regex(pattern="^chat4 ")


@chat_singe4.handle()
async def chat_singe4_rev(event: Event, bot: Bot):
    content = event.get_plaintext()[6:]
    print(content)
    if content == "" or content is None:
        await bot.send(event=event, message=MessageSegment.text("内容不能为空！"))
        return
    mess = {}
    try:
        mess = await bot.send(event=event, message=MessageSegment.text("ChatGPT正在思考......"))
    except nonebot.adapters.onebot.v11.exception.ActionFailed:
        await bot.send(event=event, message=MessageSegment.text("风控了！！"))

    res = await chat4(content, event)
    if res:
        try:
            await bot.delete_msg(message_id=mess['message_id'])
            await bot.send(event=event, message=MessageSegment.text(res))
        except nonebot.adapters.onebot.v11.exception.ActionFailed:
            await bot.delete_msg(message_id=mess['message_id'])
            await bot.send(event=event, message=MessageSegment.text("风控了！！"))

