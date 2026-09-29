"""push_message: Hermes 反向调用,nonebot 主动发送一条消息到群。

约束(M1):
  - (adapter, group_id) 必须有活跃 reactive session
  - BotRegistry 必须有该 (adapter, group_id) 的 Target
不满足任一条件返回 422 等价错误(由 FastMCP 序列化为 isError=true)。

成功路径的副作用, 与 reactive submit_decision 回复路径(_run_reactive_turn 末段)等价:
  1. mark_bot_replied — 写 ActiveSession.last_bot_reply_at, 供 post-reply cooldown 闸门
     在后续 reactive turn / refire 入口判定
  2. message_buffer.append(is_bot=True) — 让后续 _run_reactive_turn 拉到的
     <recent_messages> 里能看见 bot 这条 push 出去的话, LLM 不会"以为自己没说"
两件都做才能避免 Hermes 用 push_message 当主回复后, 后续 refire / 同 turn submit_decision
又答一遍同主题。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from nonebot import get_bot, logger
from pydantic import BaseModel, Field

from ...core.message_buffer import BufferedMessage
from ...core.outbound import get_bot_nickname, send_text_with_media
from ...core.routing import CallerScope
from ..auth import PushContextError, validate_push_context

if TYPE_CHECKING:
    from ...core.message_buffer import MessageBuffer


class PushMessageInput(BaseModel):
    adapter: str = Field(..., description="Adapter name (lowercased), e.g. 'ob11'")
    group_id: str = Field(..., description="Group ID")
    text: str = Field(..., description="Reply text. Empty allowed only if image_urls non-empty.")
    image_urls: list[str] = Field(default_factory=list, description="Image URLs")
    reply_to_msg_id: str | None = Field(default=None, description="(M1: 不使用,保留位)")
    task_id: str | None = Field(default=None, description="(M1: 不使用,M2 bg_tasks 接入)")


class PushMessageResult(BaseModel):
    ok: bool
    error: str | None = None
    warning: str | None = None
    """部分投递:文本发出去了,但有 image_urls 无法投递(见 skipped_images)。"""

    skipped_images: list[str] = Field(default_factory=list)
    """被跳过的图片引用。bot 侧只能投递 http(s) 与 data: URL;主机本地路径取不到字节。"""

    note: str | None = None
    """行为提示(非错误)。告知调用方"这一轮你已经在群里说过话了",让它在写
    submit_decision 之前就知道再回一条会是第二条消息 —— 这是唯一能在决策**之前**
    把这件事告诉 agent 的时机。"""


# push 成功后回给调用方的行为提示。写在返回值里而不是只写进 SKILL.md:agent 是在拿到
# 这个结果之后才写 submit_decision 的,这里是唯一能在它决策**之前**提醒到它的地方。
_SPOKEN_NOTE = (
    "You have now spoken in this group for this turn. If your submit_decision also carries "
    "reply_text, it will ALSO be sent as a second message. Restating what you just pushed is "
    "dropped as a duplicate — set should_reply=false unless you have something new to add."
)

# bot 侧能真正投递的 scheme。本地路径不在其中:MCP 调用方(Hermes)与 bot 可能不同机,
# 即使同机,按调用方给的任意路径去读文件也是一条不该开的洞。
_DELIVERABLE_PREFIXES = ("http://", "https://", "data:")


def _partition_image_urls(urls: list[str]) -> tuple[list[str], list[str]]:
    """拆成 (可投递, 需跳过)。"""
    ok: list[str] = []
    skipped: list[str] = []
    for u in urls:
        (ok if u.startswith(_DELIVERABLE_PREFIXES) else skipped).append(u)
    return ok, skipped


async def push_message_impl(
    inp: PushMessageInput,
    *,
    active_sessions,
    bot_registry,
    scope: CallerScope | None,
    message_buffer: MessageBuffer | None = None,
) -> PushMessageResult:
    """scope 是调用方的可操作范围,**没有默认值**:漏传会是 TypeError 而不是静默放行。
    None = 认不出调用方,一律拒(见 auth.assert_scope_allows)。"""
    if not inp.text and not inp.image_urls:
        return PushMessageResult(ok=False, error="text and image_urls both empty")

    now_ms = int(time.time() * 1000)
    try:
        validate_push_context(
            adapter=inp.adapter,
            group_id=inp.group_id,
            active_sessions=active_sessions,
            bot_registry=bot_registry,
            now_ms=now_ms,
            scope=scope,
        )
    except PushContextError as exc:
        logger.warning(f"[MCP push_message] context invalid: {exc}")
        return PushMessageResult(ok=False, error=str(exc))

    # 防御:即使 validate 通过了,这里二次 get 之间存在理论 TOCTOU 窗口
    # (registry 没 TTL,M1 内不会自动 evict,但 python -O 下 assert 会被剥除,
    # 走 if 而非 assert 保 push_message 整体错误面收敛在 PushMessageResult 里)
    entry = bot_registry.get(inp.adapter, "group", inp.group_id)
    if entry is None:
        logger.warning(
            f"[MCP push_message] registry entry disappeared after context check: {inp.adapter}/{inp.group_id}"
        )
        return PushMessageResult(
            ok=False,
            error=f"bot registry entry not found: ({inp.adapter}, {inp.group_id})",
        )

    try:
        bot = get_bot(entry.bot_self_id)
    except (KeyError, ValueError) as exc:
        logger.warning(f"[MCP push_message] bot offline self_id={entry.bot_self_id}: {exc}")
        return PushMessageResult(ok=False, error=f"bot offline: {entry.bot_self_id}")

    # 无法投递的引用要在这里就摘掉并如实回报:直接交给 outbound 只会被静默跳过,
    # 调用方拿到 ok=true 以为图发了,用户却什么也没看到 —— agent 通常会因此重发一遍,
    # 于是群里出现两条一样的文本。
    deliverable, skipped = _partition_image_urls(inp.image_urls)
    warning: str | None = None
    if skipped:
        logger.warning(
            f"[MCP push_message] {len(skipped)} 个 image_urls 无法投递(需要 http(s)/data:): {[u[:80] for u in skipped]}"
        )
        warning = (
            f"{len(skipped)} image(s) were NOT delivered: only http(s):// and data: URLs can be sent. "
            "A path on the Hermes host is not reachable from the bot. To send a locally generated "
            "image, put a MEDIA:<absolute path> tag in your submit_decision reply_text instead — "
            "the gateway inlines it for you. Do not retry this push with the same path."
        )
    if not inp.text and not deliverable:
        return PushMessageResult(
            ok=False,
            error="nothing deliverable: text empty and all image_urls unsupported",
            warning=warning,
            skipped_images=skipped,
        )

    success = await send_text_with_media(
        bot=bot,
        target=entry.target,
        text=inp.text,
        media_urls=deliverable,
        at_user_id=None,  # 主动 push 不 @ 任何用户(对话不针对特定个体)
        adapter_name=inp.adapter,
    )
    if not success:
        return PushMessageResult(ok=False, error="send failed (see nonebot log)", skipped_images=skipped)

    # 滑动续期。注:用 send 前的 now_ms 而非 send 后的 wall clock,
    # 慢 send(图片上传等)情况下 TTL 续期会比 wall clock 略短(<10s 量级,
    # 300s TTL 下可忽略)。如未来需要精确续期,在此重新读 time.time()。
    active_sessions.touch(inp.adapter, inp.group_id, now_ms=now_ms)

    # 与 reactive 回复路径对齐 — 写 last_bot_reply_at(供 cooldown 闸门),
    # 把 push 的内容注入 buffer(供后续 turn 的 <recent_messages> 看见 bot 已答)
    # media_count 记**实际投出去的**张数(不含 skipped):同 turn 去重闸门据此判断
    # 「文本已答但图还没出去」,把 submit_decision 里能投的图补发出去。
    # 带上原文:同 turn 去重据此判断 submit_decision 是不是在复述这一条。
    if not active_sessions.mark_bot_replied(
        inp.adapter, inp.group_id, now_ms=now_ms, media_count=len(deliverable), text=inp.text
    ):
        # push 已经发出去了,但窗口在这一刻没了(某一发 turn 返回了 should_exit_active)。
        # 后续 turn 的 cooldown 与同 turn 去重都看不到这一发,可能出现重复答复。
        logger.warning(
            f"[MCP push_message] mark_bot_replied landed nowhere: no active session "
            f"for {inp.adapter}/{inp.group_id}; cooldown and same-turn dedupe lose this reply"
        )
    if message_buffer is not None:
        message_buffer.append(
            BufferedMessage(
                ts=now_ms,
                adapter=inp.adapter,
                group_id=inp.group_id,
                user_id=entry.bot_self_id,
                # 与 reactive 回写同一约定:用平台账号名,别写字面 "Bot" —— 那行会
                # 进下一轮 <recent_messages>,角色名不叫 Bot 时读起来像另一个 bot。
                nickname=await get_bot_nickname(bot),
                content=inp.text,
                image_urls=list(deliverable),
                is_bot=True,
            )
        )
    return PushMessageResult(ok=True, warning=warning, skipped_images=skipped, note=_SPOKEN_NOTE)
