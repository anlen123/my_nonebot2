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
    if user_id==1793268622:
        await bot.send(event=event,message="机器人你别命令我")
        return 
    if user_id == 1761512493 or user_id == 2822103204 or user_id == 1928906357:
        if "exit" in msg or "shutdown" in msg:
            await bot.send(event=event, message="别想着干坏事")
            return
        subp = subprocess.Popen("cd;" + msg + " &", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                encoding="utf-8")
        out, err = subp.communicate()
        if out + err != "":
            msgg = out+err 
            if len(msgg)>=7000:
                msgg = msgg[:7000]
            await bot.send(event=event, message=msgg)
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
            msgg = out+err 
            if len(msgg)>=7000:
                msgg = msgg[:7000]
            await bot.send(event=event, message=msgg)
        else:
            await bot.send(event=event, message="您的指令是没有返回值的")
