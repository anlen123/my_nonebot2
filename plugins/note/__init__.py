"""note —— QQ 备忘录插件

命令（必须小写 note，`NOTE` / `Note` 不会触发）：

  note add <内容>   记录一条
  note rm <序号>    按序号删除
  note list         查看全部（附加命令，方便确认序号）

定时：每 20 分钟把全部记录私聊推送给 NOTE_PUSH_TARGET（默认 1761512493）。

序号说明
--------
序号是**稳定自增 id**，不是列表下标。删除中间的记录后，其他记录的序号不会
前移——比如删掉 1 之后，剩下的仍然叫 2、3，而不是变成 1、2。

这样设计是因为推送是 20 分钟一次，用户很可能是「看着一条较旧的推送」来发
`note rm`。如果按下标算，中间删过一条就会整体错位，直接删错记录。

配置见 .env.dev / .env.prod 的 NOTE_* 项，全部有默认值。
"""

from __future__ import annotations

import base64
import re
from typing import List, Optional

import nonebot
from nonebot import on_message, require
from nonebot.adapters.onebot.v11 import (
    Bot,
    Message,
    MessageEvent,
    MessageSegment,
    permission,
)
from nonebot.rule import Rule

from .config import load_config
from .render import render_notes
from .storage import add_note, list_notes, remove_note

require("nonebot_plugin_apscheduler")  # 先 require 装进来，下一行才能 import 它的 scheduler
from nonebot_plugin_apscheduler import scheduler

# ── 加载配置 ──────────────────────────────────────────────────────────────────
_cfg = load_config()
TARGET = _cfg["push_target"]
INTERVAL = _cfg["push_interval"]
PUSH_WHEN_EMPTY = _cfg["push_when_empty"]
STORAGE_PATH = _cfg["storage_path"]

USAGE = (
    "📝 备忘录用法\n"
    "note add <内容>   记录一条\n"
    "note rm <序号>    删除一条\n"
    "note list         查看全部"
)

# ── 触发规则：区分大小写的 "note" ─────────────────────────────────────────────
# 刻意不用 on_command：COMMAND_START 为空时 on_command 对大小写的处理不确定，
# 这里用显式正则锁死「只有小写 note 才触发」。
_NOTE_RE = re.compile(r"^note(?:\s+(.*))?$", re.DOTALL)


def _parse_arg(text: str) -> Optional[str]:
    """取出 `note` 后面的整段参数；整条消息不像 note 命令时返回 None。

    「像 note 命令」= 小写 note 顶格开头（后面可以什么都没有，此时返回空串）。
    rule 和 handler 都走这一个入口，避免同一套匹配规则在两边各写一遍、
    以后改触发条件时漏改其中一处。
    """
    match = _NOTE_RE.match(text.strip())
    if match is None:
        return None
    return match.group(1) or ""


async def _is_note(event: MessageEvent) -> bool:
    return _parse_arg(event.get_plaintext()) is not None


note_cmd = on_message(
    rule=Rule(_is_note),
    permission=permission.PRIVATE | permission.GROUP,
    priority=5,
    block=True,
)


# ── 渲染 ──────────────────────────────────────────────────────────────────────
def _format_notes(notes: List[dict], with_time: bool = True) -> str:
    if not notes:
        return "📝 备忘录 · 暂无记录"

    lines = [f"📝 备忘录 · 共 {len(notes)} 条", ""]
    for n in notes:
        lines.append(f"{n['id']}. {n['text']}")
        if with_time and n.get("created_at"):
            # "2026-09-14 17:30:00" -> "09-14 17:30"
            lines.append(f"   {n['created_at'][5:16]}")
    return "\n".join(lines)


def _human_interval(seconds: int) -> str:
    """秒数转成人话：整小时说「N 小时」，整分钟说「N 分钟」，其余按秒。"""
    if seconds % 3600 == 0:
        return f"{seconds // 3600} 小时"
    if seconds % 60 == 0:
        return f"{seconds // 60} 分钟"
    return f"{seconds} 秒"


def _image_msg(png: bytes) -> Message:
    """把 PNG 字节包成可直接发送的图片消息（base64 内联，不落盘）。"""
    return Message(MessageSegment.image(f"base64://{base64.b64encode(png).decode()}"))


def _note_card(notes: List[dict]) -> Message:
    """渲染备忘录卡片；渲染失败时降级为纯文本，不让整条命令失败"""
    try:
        png = render_notes(notes, interval_text=_human_interval(INTERVAL))
        return _image_msg(png)
    except Exception as exc:  # noqa: BLE001 — 出图只是呈现层，任何异常都不该让命令失败
        nonebot.logger.warning(f"[note] 卡片渲染失败，降级为文本：{exc}")
        return Message(_format_notes(notes))


# ── 命令处理 ──────────────────────────────────────────────────────────────────
@note_cmd.handle()
async def _handle_note(event: MessageEvent) -> None:
    # rule 已经确认过这是 note 命令，这里只负责取参数；取不到就按空参数走
    arg = (_parse_arg(event.get_plaintext()) or "").strip()

    if not arg:
        await note_cmd.finish(USAGE)

    head, _, rest = arg.partition(" ")
    rest = rest.strip()

    # ── note add <内容> ──
    if head == "add":
        if not rest:
            await note_cmd.finish("要记什么呢？格式：note add <内容>")
        if len(rest) > 500:
            await note_cmd.finish(f"内容太长了（{len(rest)} 字），限制 500 字以内～")
        try:
            user_id = int(event.get_user_id())
        except (TypeError, ValueError):
            user_id = None
        note = add_note(STORAGE_PATH, rest, user_id)
        total = len(list_notes(STORAGE_PATH))
        await note_cmd.finish(f"✅ 已记录 #{note['id']}：{note['text']}\n当前共 {total} 条")

    # ── note rm <序号> ──
    if head == "rm":
        if not rest:
            await note_cmd.finish("要删哪条？格式：note rm <序号>")
        try:
            note_id = int(rest)
        except ValueError:
            await note_cmd.finish(f"「{rest}」不是有效序号，请输入数字，如：note rm 2")
        removed = remove_note(STORAGE_PATH, note_id)
        if removed is None:
            notes = list_notes(STORAGE_PATH)
            have = "、".join(str(n["id"]) for n in notes) or "（列表为空）"
            await note_cmd.finish(f"没有序号为 {note_id} 的记录。现有：{have}")
        total = len(list_notes(STORAGE_PATH))
        await note_cmd.finish(f"🗑️ 已删除 #{removed['id']}：{removed['text']}\n剩余 {total} 条")

    # ── note list ──
    if head == "list":
        notes = list_notes(STORAGE_PATH)
        await note_cmd.finish(_note_card(notes))

    # ── 未知子命令 ──
    await note_cmd.finish(f"未知子命令「{head}」\n\n{USAGE}")


# ── 定时推送 ──────────────────────────────────────────────────────────────────
async def push_notes() -> None:
    """定时把全部记录私聊推送给 TARGET。"""
    notes = list_notes(STORAGE_PATH)

    if not notes and not PUSH_WHEN_EMPTY:
        nonebot.logger.info("[note] 无记录，跳过本次推送")
        return

    try:
        bot: Bot = nonebot.get_bot()
    except Exception as exc:  # noqa: BLE001 — 没有连接只是这一轮没得推，下一轮再来
        nonebot.logger.warning(f"[note] 当前没有可用的 bot 连接，跳过推送：{exc}")
        return

    try:
        await bot.send_private_msg(user_id=int(TARGET), message=_note_card(notes))
        nonebot.logger.info(f"[note] 已向 {TARGET} 推送 {len(notes)} 条记录")
    except Exception as exc:  # noqa: BLE001 — 推送失败只记日志，等下一个周期重试
        nonebot.logger.warning(f"[note] 推送失败：{exc}")


# misfire_grace_time + coalesce：机器人重启或卡顿后最多补推一次，
# 而不是把积压的推送一次性全轰出来
scheduler.add_job(
    push_notes,
    trigger="interval",
    seconds=INTERVAL,
    id="note_push",
    replace_existing=True,
    misfire_grace_time=600,
    coalesce=True,
)

nonebot.logger.info(
    f"[note] 已注册：每隔 {INTERVAL}s（{_human_interval(INTERVAL)}）"
    f"推送到 {TARGET}，存储 {STORAGE_PATH}"
)
