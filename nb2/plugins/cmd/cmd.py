from nonebot import on_command
from nonebot.rule import to_me, Rule
from nonebot.adapters.cqhttp import Bot, Event, MessageSegment, Message
import os
import subprocess

cmd = on_command(cmd="cmd")


@cmd.handle()
async def cmd_rev(bot: Bot, event: Event, state: dict):
    msg = str(event.message).strip()
    user_id = event.user_id
    if user_id == 1761512493 or user_id == 2822103204:
        if "exit" in msg or "shutdown" in msg:
            await bot.send(event=event, message="别想着干坏事")
            return
        subp = subprocess.Popen("cd;" + msg, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                encoding="utf-8")
        out, err = subp.communicate()
        if out + err != "":
            await bot.send(event=event, message=out + err)
        else:
            await bot.send(event=event, message="您的指令是没有返回值的")
    else:
        if "exit" in msg or "shutdown" in msg:
            await bot.send(event=event, message="别的干坏事")
            return
        msg = f"runuser -l dmf -c \"cd ;{msg}\""
        subp = subprocess.Popen("cd;" + msg, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                encoding="utf-8")
        out, err = subp.communicate()
        if out + err != "":
            await bot.send(event=event, message=out + err)
        else:
            await bot.send(event=event, message="您的指令是没有返回值的")
