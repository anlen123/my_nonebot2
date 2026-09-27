"""nonebot_plugin_command ── 用 QQ 遥控服务器执行 shell 命令

命令：
  cmd <命令>   执行一条 shell 命令并把输出回给发送者（超过 7000 字符截断）
  内存         查看根分区剩余空间（df -h /dev/vda1）

权限相关配置（nonebot 全局配置，写在 .env 里）：
  cmd_super    可以直接执行命令的 QQ 号列表；不在名单里的用户，命令会被降权成
               runuser -l dmf -c "..." 执行
  cmd_pre      每条命令前拼接的前置命令，例如进入某个目录或激活虚拟环境

这个插件等于把 shell 暴露给 QQ，启用前先确认 cmd_super 名单都是可信任的人。
"""

from __future__ import annotations

import asyncio

import nonebot
from nonebot import on_regex
from nonebot.adapters.onebot.v11 import Bot, Event, Message

# ── 配置 ──────────────────────────────────────────────────────────────────────
_CONFIG = nonebot.get_driver().config.model_dump()
CMD_SUPER = _CONFIG.get("cmd_super", [])  # 可以直接执行命令的 QQ 号
CMD_PRE = _CONFIG.get("cmd_pre", "")  # 每条命令前拼接的前置命令

# ── 常量 ──────────────────────────────────────────────────────────────────────
_BLOCKED_WORDS = ("exit", "shutdown", "poweroff", "init", "halt")  # 沾到就拒绝
_TRUNCATE_LIMIT = 7000  # 输出上限，QQ 单条消息装不下更多
_MEMORY_COMMAND = 'echo "剩余储存空间" ;df -h | grep "/dev/vda1 " | awk "{print $ 4}"'

REFUSED_REPLY = "别想着干坏事"
EMPTY_REPLY = "您的指令是没有返回值的"
FAILED_REPLY = "运行错误"


async def run(command: str) -> str:
    """用系统 shell 执行命令，返回 stdout + stderr 合并后的文本。

    拼上 cmd_pre 前缀；执行或解码失败一律退回 FAILED_REPLY，不让异常逃出 handler。
    """
    shell = f"{CMD_PRE};{command}" if CMD_PRE else command
    nonebot.logger.info(f"[cmd] 执行：{shell}")  # 留痕，出问题能对上谁跑了什么

    try:
        proc = await asyncio.create_subprocess_shell(
            shell,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return (stdout + stderr).decode()
    except Exception as exc:  # noqa: BLE001 —— 执行和解码都可能失败，统一回固定文案
        nonebot.logger.warning(f"[cmd] 执行失败：{exc}")
        return FAILED_REPLY


async def _run_and_reply(bot: Bot, event: Event, command: str) -> None:
    """危险命令拦截 → 按权限挑执行身份 → 回显输出（空输出回固定文案）。"""
    if any(word in command for word in _BLOCKED_WORDS):
        await bot.send(event=event, message=REFUSED_REPLY)
        return

    shell = f"cd ;{command}"
    if event.user_id not in CMD_SUPER:
        shell = f'runuser -l dmf -c "{shell}"'

    output = await run(shell)
    if not output:
        await bot.send(event=event, message=EMPTY_REPLY)
        return
    if len(output) >= _TRUNCATE_LIMIT:
        output = output[:_TRUNCATE_LIMIT]
    await bot.send(event=event, message=Message(output.strip()))


cmd = on_regex(pattern=r"^cmd\ ")


@cmd.handle()
async def cmd_rev(bot: Bot, event: Event) -> None:
    """cmd <命令>：执行并回显输出。"""
    await _run_and_reply(bot, event, str(event.message).strip()[3:])


cmd_m = on_regex(pattern="^内存$")


@cmd_m.handle()
async def memory_rev(bot: Bot, event: Event) -> None:
    """内存：查看根分区剩余空间。"""
    await _run_and_reply(bot, event, _MEMORY_COMMAND)
