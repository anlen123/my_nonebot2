"""语录 —— 群友共享的语录图库

用法：
  语录 / yulu / yl / 来点语录   从本群语录库里随机发一张
  上传语录                     先发这三个字，再按提示发一张图片，存进本群语录库

存储：
  <imgRoot>QQbotFiles/yulu/<群号>/ 下按群隔离，子目录里的图片也会被收进随机池；
  发送时用 file:/// 地址交给协议端读取，图片不经过网络。
"""

from __future__ import annotations

import os
import random as rd
import time
import uuid
from collections import deque
from typing import Deque, List

import httpx
from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent, MessageSegment
from nonebot.plugin import on_regex

from .config import config

imgRoot = config.imgRoot

yulu = on_regex("^语录$|^yulu$|^yl$|^来点语录$")


def _group_dir(group_id: int) -> str:
    """本群语录目录（保持字符串拼接，与 imgRoot 的写法一致）。"""
    return f"{imgRoot}QQbotFiles/yulu/{group_id}/"


def _ensure_group_dir(group_id: int) -> str:
    """确保本群语录目录存在，返回目录路径。"""
    path = _group_dir(group_id)
    if not os.path.exists(path):
        os.makedirs(path)
    return path


async def get_img_url(path: str) -> str:
    """本地图片转成协议端能直接读的 file:/// 地址。"""
    return "file:///" + path


async def get_all_yl(group_id: int) -> List[str]:
    """递归列出本群语录目录下的所有文件（广度优先，目录本身不算）。"""
    root = _group_dir(group_id)
    files: List[str] = []
    pending: Deque[str] = deque()

    for name in os.listdir(root):
        full = root + name
        if os.path.isdir(full):
            pending.append(full)
        elif os.path.isfile(full):
            files.append(full)

    while pending:
        top = pending.popleft()
        for name in os.listdir(top):
            full = f"{top}/{name}"
            if os.path.isdir(full):
                pending.append(full)
            elif os.path.isfile(full):
                files.append(full)
    return files


@yulu.handle()
async def yulu_rev(bot: Bot, event: GroupMessageEvent) -> None:
    _ensure_group_dir(event.group_id)

    img_list = await get_all_yl(event.group_id)
    if not img_list:
        await yulu.finish("语录库已经空了")
        return

    rd.seed(time.time())
    path = img_list[rd.randint(0, len(img_list) - 1)]
    await bot.send(event=event, message=MessageSegment.image(await get_img_url(path)))


yulu_save = on_regex("^上传语录$")


@yulu_save.handle()
async def yulu_save_handle(event: Event) -> None:
    message = event.message
    if str(message) != "上传语录":
        await yulu_save.finish("后面不加参数,直接at我后,输入\"上传语录\"即可.")


@yulu_save.got(key="url", prompt="请输入图片")
async def yulu_save_got(event: GroupMessageEvent) -> None:
    message = event.message
    url = message[0].data["url"]
    group_dir = _ensure_group_dir(event.group_id)

    if not url:
        await yulu_save.finish("好像出错了!!!")
        return

    response = httpx.get(url)
    with open(f"{group_dir}{uuid.uuid4()}.png", mode="wb") as fp:
        fp.write(response.content)
    await yulu_save.finish("上传成功!!!")
