"""deepseek_gpt ── 通过火山方舟（Ark）接口调用 DeepSeek 做多轮对话与翻译

命令（正则匹配，命令词后必须跟一个空格）：
  ds3 <内容>                DeepSeek-V3 多轮对话
  dsr <内容>                DeepSeek-R1 多轮对话
  dsclear                   清空自己的对话记录
  fy <内容> / 翻译 <内容>     中英日韩互译（中文译成英文，其余译成中文）

群聊里的回答走合并转发，私聊直接发文本。对话历史按 QQ 号存在进程工作目录下的
chat_history.db（SQLite），导入插件时建表。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator, List, Optional

from nonebot import on_regex
from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent, MessageSegment
from nonebot.adapters.onebot.v11.exception import ActionFailed
from openai import AsyncOpenAI

# ── 常量 ──────────────────────────────────────────────────────────────────────
DB_PATH = "chat_history.db"  # 对话历史库，相对进程工作目录
ARK_API_KEY = "77b1cd88-22d3-4c9e-9091-8394d5bbfcab"  # 火山方舟密钥（历史遗留：硬编码在源码里）
ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"

MODEL_DEEPSEEK_V3 = "deepseek-v3-241226"
MODEL_DEEPSEEK_R1 = "deepseek-r1-250120"

SYSTEM_PROMPT = "You are a helpful assistant"
TRANSLATE_PROMPT = (
    "你现在扮演翻译官，你可以准确的翻译日文，英文，韩文，英语为中文。"
    "当我输入中文的时候则翻译成英文，只需要给我结果即可，无需说多余的话。"
)

EMPTY_REPLY = "内容不能为空！"  # 只发了命令词、没给正文时回这句
RISK_REPLY = "风控了！！"  # QQ 风控拦截发送时的兜底文案
CLEARED_REPLY = "您的deepseek对话记录已被清除！"


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
    """取出该用户的全部历史消息（OpenAI messages 格式）；没有记录时返回空列表。"""
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
def _ark_client() -> AsyncOpenAI:
    """构造火山方舟客户端；对话与翻译共用同一套鉴权信息。"""
    return AsyncOpenAI(api_key=ARK_API_KEY, base_url=ARK_BASE_URL)


async def deepseek(message: str, user_id: str, model: str) -> Optional[str]:
    """带上下文请求一次对话，并把本轮问答写回历史；返回模型回复。"""
    client = _ark_client()
    history = get_conversation_history(user_id)
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += history
    messages.append({"role": "user", "content": message})

    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=1024,
        temperature=0.7,
        stream=False,
    )
    assistant_reply = response.choices[0].message.content

    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": assistant_reply})
    update_conversation_history(user_id, history)

    return assistant_reply


async def deepseek_fy(content: str) -> str:
    """用 DeepSeek-R1 做互译，直接返回译文；这一路不带上下文。"""
    client = _ark_client()
    response = await client.chat.completions.create(
        model=MODEL_DEEPSEEK_R1,
        messages=[
            {"role": "system", "content": TRANSLATE_PROMPT},
            {"role": "user", "content": content},
        ],
        max_tokens=1024,
        temperature=0.7,
        stream=False,
    )
    return str(response.choices[0].message.content).strip()


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


# ── 命令 ──────────────────────────────────────────────────────────────────────
deepseek_singe = on_regex(pattern="^ds3 ")
deepseek_singe_v2 = on_regex(pattern="^dsr ")


@deepseek_singe.handle()
@deepseek_singe_v2.handle()
async def deepseek_singe_rev(event: Event, bot: Bot) -> None:
    """ds3 / dsr：多轮对话，先顶一条占位提示，出结果后撤回再发正文。"""
    text = event.get_plaintext()
    # 两个命令词互斥，命中哪个用哪个模型；别的输入进不来（正则已经滤过）
    if text.startswith("ds3 "):
        content, model = text[4:], MODEL_DEEPSEEK_V3
    elif text.startswith("dsr "):
        content, model = text[4:], MODEL_DEEPSEEK_R1
    else:
        return

    if not content.strip():
        await bot.send(event=event, message=MessageSegment.text(EMPTY_REPLY))
        return

    thinking = await _send_thinking(bot, event, f"{model}正在思考......")
    res = await deepseek(content, event.get_user_id(), model)
    if res:
        await _send_reply(bot, event, thinking, str(res).strip())


clear = on_regex(pattern="^dsclear$")


@clear.handle()
async def clear_conversation(event: Event, bot: Bot) -> None:
    """dsclear：清空自己的对话记录。"""
    clear_conversation_history(str(event.user_id))
    await bot.send(event=event, message=MessageSegment.text(CLEARED_REPLY))


ds_fy_singe = on_regex(pattern="^(翻译|fy) ")


@ds_fy_singe.handle()
async def ds_fy_singe_rev(event: Event, bot: Bot) -> None:
    """fy / 翻译：中英日韩互译，一次性问答。"""
    text = event.get_plaintext()
    if not text.startswith(("fy ", "翻译 ")):
        return  # 正则已经滤过，这里只是兜底
    content = text[3:]

    res = await deepseek_fy(content)
    await bot.send(event=event, message=f"翻译句子：{content}\n\n翻译后：{res}")
