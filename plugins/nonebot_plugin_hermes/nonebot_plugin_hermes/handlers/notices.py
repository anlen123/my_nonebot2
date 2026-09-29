"""通知事件处理器

priority=1 dispatch: 适配器特定 notice 事件 → 合成伪消息 → 复用 message.py 路由。

**CLAUDE.md 规则例外**: 本文件是仅有的允许 import 适配器特定类型的位置。
- 仅在 dispatch 函数内部 try-import (惰性)
- 失败 (ImportError) 即 no-op,不影响其他平台
"""

from __future__ import annotations

import time

import nonebot_plugin_alconna as alconna
from nonebot import logger, on_notice
from nonebot.adapters import Bot, Event

from ..config import plugin_config
from ..utils import check_isolation, get_adapter_name
from .message import route_synthesized_input

notice_handler = on_notice(priority=1, block=False)


def _now_ms() -> int:
    return int(time.time() * 1000)


@notice_handler.handle()
async def dispatch(bot: Bot, event: Event):
    """OneBot v11 notice 事件分发入口。

    早期 return:
      - 两个开关都关
      - adapter != OneBot V11
      - ImportError (onebot adapter 未装)
    """
    if not (plugin_config.hermes_poke_trigger_enabled or plugin_config.hermes_greet_on_join):
        return

    adapter_name = get_adapter_name(bot)
    if adapter_name != "onebotv11":
        return

    try:
        from nonebot.adapters.onebot.v11 import (
            GroupIncreaseNoticeEvent,
            PokeNotifyEvent,
        )
    except ImportError:
        logger.debug("[HERMES notice] OneBot v11 adapter not installed; skip")
        return

    if isinstance(event, PokeNotifyEvent) and plugin_config.hermes_poke_trigger_enabled:
        await _handle_poke(bot, event)
    elif isinstance(event, GroupIncreaseNoticeEvent) and plugin_config.hermes_greet_on_join:
        await _handle_member_join(bot, event)


async def _handle_poke(bot: Bot, event) -> None:
    """戳一戳: 仅戳 bot 自己时触发,合成 `[poke] 戳了你一下`。"""
    # OneBot v11 的 target_id/self_id/user_id 都是 int,统一字符串后比较
    if str(event.target_id) != str(bot.self_id):
        return
    if str(event.user_id) == str(bot.self_id):
        return  # bot 戳自己(罕见),跳过

    user_id = str(event.user_id)
    group_id = str(event.group_id) if event.group_id else None

    target = _build_target(adapter_name="onebotv11", user_id=user_id, group_id=group_id)
    if not check_isolation(event, target):
        return

    nickname = await _resolve_nickname(bot, user_id=user_id, group_id=group_id)

    await route_synthesized_input(
        bot=bot,
        target=target,
        adapter_name="onebotv11",
        user_id=user_id,
        group_id=group_id,
        nickname=nickname,
        text="[poke] 戳了你一下",
        addressed_to_bot=True,  # 只在戳 bot 本体时走到这,平台层面就是指向 bot 的事件
        allow_passive=True,
        now_ms=_now_ms(),
    )


async def _handle_member_join(bot: Bot, event) -> None:
    """入群: 合成 `[event=member_join] {nickname} 加入了群`。
    active_session 关时由 route_synthesized_input 自检并跳过。"""
    if str(event.user_id) == str(bot.self_id):
        return  # bot 自己被拉进新群,buffer 空,跳过

    user_id = str(event.user_id)
    group_id = str(event.group_id)

    target = _build_target(adapter_name="onebotv11", user_id=user_id, group_id=group_id)
    if not check_isolation(event, target):
        return

    nickname = await _resolve_nickname(bot, user_id=user_id, group_id=group_id)

    await route_synthesized_input(
        bot=bot,
        target=target,
        adapter_name="onebotv11",
        user_id=user_id,
        group_id=group_id,
        nickname=nickname,
        text=f"[event=member_join] {nickname} 加入了群",
        addressed_to_bot=False,  # 群级事件,不是「冲 bot 说的」;要不要迎新由模型自判
        allow_passive=False,
        now_ms=_now_ms(),
    )


async def _resolve_nickname(bot: Bot, *, user_id: str, group_id):
    """OneBot v11 API 拿昵称,失败 fallback 用 user_id 字符串。"""
    try:
        if group_id:
            info = await bot.call_api(
                "get_group_member_info",
                group_id=int(group_id),
                user_id=int(user_id),
                no_cache=True,
            )
            return info.get("card") or info.get("nickname") or user_id
        info = await bot.call_api("get_stranger_info", user_id=int(user_id))
        return info.get("nickname") or user_id
    except Exception as e:
        logger.debug(f"[HERMES notice] nickname resolve failed for {user_id}: {e}")
        return user_id


def _build_target(*, adapter_name: str, user_id: str, group_id):
    """构造 alconna.Target——notice handler 不在 alconna 消息上下文里,要自己造。"""
    if group_id:
        return alconna.Target(id=group_id, private=False, adapter=adapter_name)
    return alconna.Target(id=user_id, private=True, adapter=adapter_name)
