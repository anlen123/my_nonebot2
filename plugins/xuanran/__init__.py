from pathlib import Path

import nonebot
from nonebot.plugin import on_regex
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment, Message
from nonebot.params import T_State, State
import time
import asyncio
import os

export = nonebot.require("nonebot_plugin_navicat")
rc = export.redis_client  # redis的

global_config = nonebot.get_driver().config
imgRoot = global_config.dict().get("imgroot", os.environ["HOME"] + "/")
xuanran_proxy = global_config.dict().get("xuanran_proxy", "")
if xuanran_proxy:
    xuanran_proxy += ";"

xr = on_regex(pattern="^xr\ ")


async def screenshots(msg: str):
    print("渲染网址: " + msg)
    path = f"{xuanran_proxy} {imgRoot}miniconda3/bin/python /root/my_nonebot2/plugins/xuanran/screenShot.py {msg}"
    print("命令: " + path)
    img = await run(path)
    print("图片地址: " + img)
    endStr = img.endswith(".png\n")
    includeStr = "True：截图成功！！！" in img
    print("结尾是png " if endStr else "结尾不是png")
    print("包含截图成功" if includeStr else "不包含截图成功")
    return (img, endStr, includeStr)


@xr.handle()
async def xr_rev(bot: Bot, event: Event):
    msg = str(event.message).strip()[3:].strip()
    s = time.time()
    if not (msg.startswith("http://") or msg.startswith("https://")):
        msg = f"http://{msg}"
    msg = msg.replace("。", ".")
    msg = msg.replace(",", ".")
    msg = msg.replace("，", ".")
    img, endStr, includeStr = await screenshots(msg)
    if img and endStr and includeStr:
        img = img.split("\n")[1]
        await bot.send(event=event, message=MessageSegment.image(
            "file:///" + f"{imgRoot}QQbotFiles/xr/" + img) + f"耗时:{time.time() - s}")
    else:
        await bot.send(event=event, message="错误")
        rc.lpush("xuanran", msg)
        await errorRetry(bot, event)


retry = 0


async def errorRetry(bot: Bot, event: Event):
    global retry
    try:
        if retry >= 3:
            while rc.llen('xuanran') != 0:
                rc.lpop("xuanran")
            retry = 0
            return

        retry += 1
        time.sleep(4)
        msg = rc.lindex('xuanran', 0)
        msg = bytes.decode(msg)
        print(f"重试的: {msg}")
        s = time.time()
        if not (msg.startswith("http://") or msg.startswith("https://")):
            msg = f"http://{msg}"
        msg = msg.replace("。", ".")
        img, endStr, includeStr = await screenshots(msg)
        if img and endStr and includeStr:
            img = img.split("\n")[1]
            await bot.send(event=event, message=f"第{retry}次重试成功: " + MessageSegment.image(
                "file:///" + f"{imgRoot}QQbotFiles/xr/" + img) + f"耗时:{time.time() - s}")
            retry = 0
            rc.lpop('xuanran')
        else:
            await bot.send(event=event, message=f"第{retry}次重试失败: ")
            await errorRetry(bot, event)
    except:
        while rc.llen('xuanran') != 0:
            rc.lpop("xuanran")
        retry = 0
        return


async def run(cmd):
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)

    stdout, stderr = await proc.communicate()
    return (stdout + stderr).decode()


why = on_regex(pattern="是什么$")


@why.handle()
async def why_rev(bot: Bot, event: Event, state: T_State = State()):
    msg = event.get_plaintext()
    msg = "https://zh.wikipedia.org/wiki/" + msg[:-3]
    s = time.time()
    img, endStr, includeStr = await screenshots(msg)
    if img and endStr and includeStr:
        img = img.split("\n")[1]
        await bot.send(event=event, message=MessageSegment.image(
            "file:///" + f"{imgRoot}QQbotFiles/xr/" + img) + f"耗时:{time.time() - s}")
    else:
        await bot.send(event=event, message="错误")


mengniang = on_regex(pattern="是什么萌娘$")


@mengniang.handle()
async def mengniang_rev(bot: Bot, event: Event, state: T_State = State()):
    msg = event.get_plaintext()
    msg = "https://zh.moegirl.org.cn/" + msg[:-5]
    s = time.time()
    img = await run(
        f"{xuanran_proxy} {imgRoot}miniconda3/bin/python /root/my_nonebot2/plugins/xuanran/screenShot.py {msg}")
    print(img)
    endStr = img.endswith(".png\n")
    includeStr = "True：截图成功！！！" in img
    print(endStr)
    print(endStr)
    if img and endStr and includeStr:
        img = img.split("\n")[1]
        await bot.send(event=event, message=MessageSegment.image(
            "file:///" + f"{imgRoot}QQbotFiles/xr/" + img) + f"耗时:{time.time() - s}")
    else:
        await bot.send(event=event, message="错误")
