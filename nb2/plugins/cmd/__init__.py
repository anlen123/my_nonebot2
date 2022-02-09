from pathlib import Path

import nonebot
from nonebot import get_driver

from nonebot.plugin import on_regex
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment, Message
from nonebot.params import T_State,State
import asyncio
from .config import Config

global_config = get_driver().config
cmd_pre = global_config.dict()['cmd_pre'] if 'cmd_pre' in global_config.dict() else ""


_sub_plugins = set()
_sub_plugins |= nonebot.load_plugins(
    str((Path(__file__).parent / "plugins").
    resolve()))



cmd = on_regex(pattern="^cmd\ ")
@cmd.handle()
async def cmd_rev(bot: Bot, event: Event, state: T_State=State()):
    error_cmd = ["exit","shutdown","poweroff","init","halt"]
    msg = str(event.message).strip()[3:]
    user_id = event.user_id
    if user_id==1793268622:
        await bot.send(event=event,message="机器人你别命令我")
        return 
    if user_id == 1761512493 or user_id == 2822103204 or user_id == 1928906357:
        if "exit" in msg or "shutdown" in msg:
            await bot.send(event=event, message="别想着干坏事")
            return
        msgg = await run(f"cd ;{msg}")
        if msgg!= "":
            if len(msgg)>=7000:
                msgg = msgg[:7000]
            await bot.send(event=event, message=Message(msgg))
        else:
            await bot.send(event=event, message="您的指令是没有返回值的")
    else:
        if "exit" in msg or "shutdown" in msg or "poweroff" in msg or "init" in msg or "halt" in msg:
            await bot.send(event=event, message="别的干坏事")
            return
        msg = f"runuser -l dmf -c \"cd ;{msg}\""
        msgg = await run(f"cd ;{msg}")
        if msgg!="":
            if len(msgg)>=7000:
                msgg = msgg[:7000]
            await bot.send(event=event, message=Message(msgg))
        else:
            await bot.send(event=event, message="您的指令是没有返回值的")
async def run(cmd):
    try:
        cmd = cmd_pre +"; "+cmd
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await proc.communicate()
        return (stdout+stderr).decode()
    except:
        return "运行错误"
