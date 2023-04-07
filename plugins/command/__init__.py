from nonebot import get_driver

from nonebot.plugin import on_regex
from nonebot.adapters.onebot.v11 import Bot, Event, Message
import asyncio
from .config import Config

global_config = get_driver().config
config = global_config.dict()

cmd_super = config.get("cmd_super", [])
cmd_ban = config.get("cmd_ban", [])
cmd_pre = config.get("cmd_pre", "")

cmd = on_regex(pattern="^cmd\ ")


@cmd.handle()
async def cmd_rev(bot: Bot, event: Event):
    error_cmd = ["exit", "shutdown", "poweroff", "init", "halt"]
    msg = str(event.message).strip()[3:]
    if any(_ in msg for _ in error_cmd):
        await bot.send(event=event, message="别想着干坏事")
        return
    if event.user_id in cmd_super:
        msgs = await run(f"cd ;{msg}")
    else:
        msgs = await run(f"runuser -l dmf -c \"cd ;{msg}\"")
    if msgs:
        if len(msgs) >= 7000:
            msgs = msgs[:7000]
        msgs = msgs.strip()
        await bot.send(event=event, message=Message(msgs))
    else:
        await bot.send(event=event, message="您的指令是没有返回值的")

cmd_m = on_regex(pattern="^内存$")

@cmd_m.handle()
async def cmd_rev(bot: Bot, event: Event):
    error_cmd = ["exit", "shutdown", "poweroff", "init", "halt"]
    msg = 'echo "剩余储存空间" ;df -h | grep "/dev/vda1 " | awk "{print $ 4}"'
    if any(_ in msg for _ in error_cmd):
        await bot.send(event=event, message="别想着干坏事")
        return
    if event.user_id in cmd_super:
        msgs = await run(f"cd ;{msg}")
    else:
        msgs = await run(f"runuser -l dmf -c \"cd ;{msg}\"")
    if msgs:
        if len(msgs) >= 7000:
            msgs = msgs[:7000]
        await bot.send(event=event, message=Message(msgs))
    else:
        await bot.send(event=event, message="您的指令是没有返回值的")


async def run(command: str):
    try:
        if cmd_pre:
            command = f"{cmd_pre};{command}"
        else:
            command = f"{command}"
        print(command)
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await proc.communicate()
        return (stdout + stderr).decode()
    except Exception:
        return "运行错误"
