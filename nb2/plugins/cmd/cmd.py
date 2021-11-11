from nonebot.plugin import on_regex
from nonebot.rule import regex, to_me, Rule
from nonebot.adapters.cqhttp import Bot, Event, MessageSegment, Message
import subprocess
import asyncio
cmd = on_regex(pattern="^cmd\ ")


@cmd.handle()
async def cmd_rev(bot: Bot, event: Event, state: dict):
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
        if "exit" in msg or "shutdown" in msg:
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
    cmd = "export ALL_PROXY=http://127.0.0.1:1081"+cmd
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)

    stdout, stderr = await proc.communicate()
    return (stdout+stderr).decode()
