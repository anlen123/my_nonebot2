from pathlib import Path

import nonebot
from nonebot.plugin import on_regex
from nonebot.adapters.cqhttp import Bot, Event, MessageSegment, Message
import time
import asyncio

xr = on_regex(pattern="^xr\ ")

_sub_plugins = set()
_sub_plugins |= nonebot.load_plugins(
    str((Path(__file__).parent / "plugins").
    resolve()))


global_config = nonebot.get_driver().config
imgRoot=global_config.dict()['imgroot']


@xr.handle()
async def xr_rev(bot: Bot, event: Event, state: dict):
    msg = str(event.message).strip()[3:].strip()
    s = time.time()
    if not (msg.startswith("http://") or msg.startswith("https://")):
        msg = f"http://{msg}"
    msg = msg.replace("。",".")
    img = await run(f"export ALL_PROXY=http://127.0.0.1:1081 ; {imgRoot}miniconda3/bin/python /root/my_nonebot2/nb2/plugins/xuanran/screenShot.py {msg}")
    print(img)
    print(img.endswith(".png\n"))
    print(img.startswith("True：截图成功！！！"))
    if img and img.endswith(".png\n") and img.startswith("True：截图成功！！！"):
        img = img.split("\n")[1]
        await bot.send(event=event, message=MessageSegment.image("file:///"+f"{imgRoot}QQbotFiles/xr/"+img)+f"耗时:{time.time()-s}")
    else:
        await bot.send(event=event, message="错误")




async def run(cmd):
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)

    stdout, stderr = await proc.communicate()
    return (stdout+stderr).decode()



why = on_regex(pattern="是什么$")

@why.handle()
async def why_rev(bot: Bot, event: Event, state: dict):
    msg = event.get_plaintext()
    msg = "https://zh.wikipedia.org/wiki/"+msg[:-3]
    s = time.time()
    img = await run(f"export ALL_PROXY=http://127.0.0.1:1081 ; {imgRoot}miniconda3/bin/python /root/my_nonebot2/nb2/plugins/xuanran/screenShot.py {msg}")
    print(img)
    print(img.endswith(".png\n"))
    print(img.startswith("True：截图成功！！！"))
    if img and img.endswith(".png\n") and img.startswith("True：截图成功！！！"):
        img = img.split("\n")[1]
        await bot.send(event=event, message=MessageSegment.image("file:///"+f"{imgRoot}QQbotFiles/xr/"+img)+f"耗时:{time.time()-s}")
    else:
        await bot.send(event=event, message="错误")

    

mengniang = on_regex(pattern="是什么萌娘$")

@mengniang.handle()
async def mengniang_rev(bot: Bot, event: Event, state: dict):
    msg = event.get_plaintext()
    msg = "https://zh.moegirl.org.cn/"+msg[:-5]
    s = time.time()
    img = await run(f"export ALL_PROXY=http://127.0.0.1:1081 ; {imgRoot}miniconda3/bin/python /root/my_nonebot2/nb2/plugins/xuanran/screenShot.py {msg}")
    print(img)
    print(img.endswith(".png\n"))
    print(img.startswith("True：截图成功！！！"))
    if img and img.endswith(".png\n") and img.startswith("True：截图成功！！！"):
        img = img.split("\n")[1]
        await bot.send(event=event, message=MessageSegment.image("file:///"+f"{imgRoot}QQbotFiles/xr/"+img)+f"耗时:{time.time()-s}")
    else:
        await bot.send(event=event, message="错误")

    
