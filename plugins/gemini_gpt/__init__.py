"""gemini_gpt ── 用 Google Gemini 做多轮对话与图片问答

命令（正则匹配，对话命令的命令词后必须跟一个空格）：
  gemini <内容> / gm <内容>   gemini-2.0-pro-exp-02-05 多轮对话
  gmt <内容>                  gemini-2.5-pro-exp-03-25 多轮对话
  gmi                         进入图片问答：先发图、再提问
  gmclear                     清空自己的对话记录

群聊里的回答走合并转发，私聊直接发文本。对话历史按 QQ 号存在进程工作目录下的
chat_gemini_history.db（SQLite），导入插件时建表；用户发的图存到
IMG_ROOT/QQbotFiles/gemini/<QQ号>/ 下。

配置见 .env 的 imgRoot（默认值见 config.py）。
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, List, Optional

import google.generativeai as genai_v1
import google.genai as genai_v2
import httpx
import PIL.Image
from nonebot import on_regex
from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent, MessageSegment
from nonebot.adapters.onebot.v11.exception import ActionFailed
from nonebot.params import T_State

from .config import config

# ── 常量 ──────────────────────────────────────────────────────────────────────
DB_PATH = "chat_gemini_history.db"  # 对话历史库，相对进程工作目录
API_KEY = "AIzaSyAEwpfKamjMN70nxLofoC0rbXs84DQx9ak"  # Gemini 密钥（历史遗留：硬编码在源码里）

MODEL_GEMINI_2_0 = "gemini-2.0-pro-exp-02-05"
MODEL_GEMINI_2_5 = "gemini-2.5-pro-exp-03-25"
MODEL_GEMINI_IMAGE = "gemini-2.0-flash-thinking-exp-01-21"

SYSTEM_PROMPT = "You are a helpful assistant"

IMG_ROOT = config.imgRoot  # 图片根目录，由 .env 的 imgRoot 覆盖
IMG_SUBDIR = "QQbotFiles\\gemini\\"  # 拼在根目录后面，再按 QQ 号分文件夹

EMPTY_REPLY = "内容不能为空！"  # 只发了命令词、没给正文时回这句
RISK_REPLY = "风控了！！"  # QQ 风控拦截发送时的兜底文案
CLEARED_REPLY = "您的gnmini对话记录已被清除！"
GMI_USAGE = '后面不加参数,直接at我后,输入"gmi"即可.'


# ── 对话历史（SQLite） ────────────────────────────────────────────────────────
@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    """打开历史库，正常退出时提交、无论如何都关闭。

    sqlite3 连接自带的 `with` 只提交不关闭，所以这里统一包一层。
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """建表；导入插件时执行一次，保证库文件与表结构就位。"""
    with _db() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS conversations ("
            "user_id TEXT PRIMARY KEY, messages TEXT)"
        )


def get_conversation_history(user_id: str) -> list[dict[str, Any]]:
    """取出该用户的全部历史消息（Gemini contents 格式）；没有记录时返回空列表。"""
    with _db() as conn:
        row = conn.execute(
            "SELECT messages FROM conversations WHERE user_id = ?", (user_id,)
        ).fetchone()
    return json.loads(row[0]) if row else []


def update_conversation_history(user_id: str, messages: list[dict[str, Any]]) -> None:
    """整体覆盖写入该用户的历史消息。"""
    with _db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO conversations (user_id, messages) VALUES (?, ?)",
            (user_id, json.dumps(messages)),
        )


def clear_conversation_history(user_id: str) -> None:
    """删除该用户的全部历史消息。"""
    with _db() as conn:
        conn.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,))


init_db()


# ── 模型调用 ──────────────────────────────────────────────────────────────────
def genai_multi_turn_async_img(question: str, image_path: str) -> Optional[str]:
    """把本地图片连同提问一起交给 Gemini，返回文本回答（这一路不带上下文）。"""
    image = PIL.Image.open(image_path)
    client_v2 = genai_v2.Client(api_key=API_KEY)
    response = client_v2.models.generate_content(
        model=MODEL_GEMINI_IMAGE, contents=[f"{question}", image]
    )
    return response.text


async def genai_multi_turn_async(
    message: str, user_id: str, m: str = MODEL_GEMINI_2_0
) -> Optional[str]:
    """带上下文请求一次对话，并把本轮问答写回历史；返回模型回复。"""
    genai_v1.configure(api_key=API_KEY)
    model = genai_v1.GenerativeModel(m)
    history = get_conversation_history(user_id)
    messages: list[dict[str, Any]] = [{"role": "model", "parts": SYSTEM_PROMPT}]
    messages += history
    messages.append({"role": "user", "parts": [message]})

    response = await model.generate_content_async(messages)
    assistant_reply = response.text

    history.append({"role": "user", "parts": [message]})
    history.append({"role": "model", "parts": [assistant_reply]})
    update_conversation_history(user_id, history)

    return assistant_reply


# ── 回复 ──────────────────────────────────────────────────────────────────────
async def send_forward_msg_group(
    bot: Bot, event: GroupMessageEvent, name: str, msgs: List[str]
) -> None:
    """把 msgs 以合并转发发到群里，每条消息一个「节点」。"""

    def to_json(msg: str) -> dict[str, Any]:
        return {"type": "node", "data": {"name": name, "uin": bot.self_id, "content": msg}}

    messages = [to_json(msg) for msg in msgs]
    await bot.call_api("send_group_forward_msg", group_id=event.group_id, messages=messages)


async def _send_thinking(bot: Bot, event: Event, text: str) -> dict[str, Any]:
    """先发一条占位提示，返回它的发送结果（供稍后撤回）。

    被风控拦截时提示一句并返回空 dict —— 与原流程一致，调用方接着照常请求模型。
    """
    try:
        return await bot.send(event=event, message=MessageSegment.text(text))
    except ActionFailed:
        await bot.send(event=event, message=MessageSegment.text(RISK_REPLY))
        return {}


async def _send_reply(bot: Bot, event: Event, thinking: dict[str, Any], reply: str) -> None:
    """撤回占位提示后把结果发出去：群聊合并转发、私聊直接发文本。"""
    try:
        await bot.delete_msg(message_id=thinking["message_id"])
        if isinstance(event, GroupMessageEvent):
            await send_forward_msg_group(bot, event, "qqbot", [reply])
        else:
            await bot.send(event=event, message=MessageSegment.text(reply))
    except ActionFailed:
        await bot.delete_msg(message_id=thinking["message_id"])
        await bot.send(event=event, message=MessageSegment.text(RISK_REPLY))


# ── 图片问答 ──────────────────────────────────────────────────────────────────
gemini_img = on_regex("^gmi$")


@gemini_img.handle()
async def gemini_img_hamdle(event: Event) -> None:
    """gmi：只认裸命令，后面跟了别的内容就提示用法。"""
    if str(event.message) != "gmi":
        await gemini_img.finish(GMI_USAGE)


@gemini_img.got(key="url", prompt="请输入图片")
async def gmi_got_image(event: Event, tt: T_State) -> None:
    """第一步：收图，下载到本地，把路径塞进 state 供下一步用。"""
    url = event.message[0].data["url"]
    tt["url"] = url

    path_prefix = f"{IMG_ROOT}{IMG_SUBDIR}{event.user_id}\\"
    if not os.path.exists(path_prefix):
        os.makedirs(path_prefix)

    if not url:
        await gemini_img.finish("好像出错了!!!")
        return

    r = httpx.get(url)
    pa = f"{path_prefix}{uuid.uuid4()}.png"
    with open(pa, mode="wb") as f:
        f.write(r.content)
    tt["pa"] = pa


@gemini_img.got(key="qu", prompt="请提出问题：")
async def gmi_got_question(bot: Bot, event: Event, tt: T_State) -> None:
    """第二步：拿到提问后连图一起问模型，出结果再撤回占位提示。"""
    tt["qu"] = event.get_message()

    thinking = await _send_thinking(bot, event, "解析中...")
    res = genai_multi_turn_async_img(str(event.get_message()), tt["pa"])
    if res:
        await _send_reply(bot, event, thinking, res)


# ── 命令 ──────────────────────────────────────────────────────────────────────
gemini_singe = on_regex(pattern="^(gemini|gm|gmt) ")


@gemini_singe.handle()
async def gemini_singe_rev(event: Event, bot: Bot) -> None:
    """gemini / gm / gmt：多轮对话，先顶一条占位提示，出结果后撤回再发正文。"""
    text = event.get_plaintext()
    # 三个命令词互斥，命中哪个用哪个模型；别的输入进不来（正则已经滤过）
    if text.startswith("gemini "):
        content, model = text[7:], MODEL_GEMINI_2_0
    elif text.startswith("gm "):
        content, model = text[3:], MODEL_GEMINI_2_0
    elif text.startswith("gmt "):
        content, model = text[4:], MODEL_GEMINI_2_5
    else:
        return

    if not content.strip():
        await bot.send(event=event, message=MessageSegment.text(EMPTY_REPLY))
        return

    thinking = await _send_thinking(bot, event, f"{model}正在思考......")
    res = await genai_multi_turn_async(content, event.get_user_id(), model)
    if res:
        await _send_reply(bot, event, thinking, res)


clear = on_regex(pattern="^gmclear$")


@clear.handle()
async def clear_conversation(event: Event, bot: Bot) -> None:
    """gmclear：清空自己的对话记录。"""
    clear_conversation_history(str(event.user_id))
    await bot.send(event=event, message=MessageSegment.text(CLEARED_REPLY))
