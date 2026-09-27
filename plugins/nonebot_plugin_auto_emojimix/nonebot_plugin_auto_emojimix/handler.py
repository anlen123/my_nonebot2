"""命令处理器"""

import re

from cachetools import TTLCache
from nonebot import on_message, logger
from nonebot.adapters import Event
from nonebot.matcher import Matcher
from nonebot.params import EventPlainText
from nonebot.rule import Rule
from nonebot.typing import T_State
from nonebot_plugin_alconna import UniMessage

from .config import plugin_config
from .service import (
    ComboNotFoundError,
    DownloadError,
    UnsupportedEmojiError,
    emoji_mix_service,
)

# 用户冷却缓存：key=user_id，过期自动清理
_cooldown_cache: TTLCache[str, bool] | None = (
    TTLCache(maxsize=4096, ttl=plugin_config.emojimix_cd)
    if plugin_config.emojimix_cd > 0
    else None
)


# Rule 函数


async def _check_cooldown(event: Event) -> bool:
    """冷却检查：用户是否在冷却中（只读，不设置冷却）。"""
    if _cooldown_cache is None:
        return True
    return event.get_user_id() not in _cooldown_cache


async def _check_explicit(state: T_State, text: str = EventPlainText()) -> bool:
    """显式模式匹配：emoji1+emoji2。"""
    try:
        if not plugin_config.emojimix_explicit:
            return False
        text = text.strip()
        logger.info(f"[emojimix] check msg: {repr(text[:80])}")
        if not text or "+" not in text:
            return False
        if matched := re.match(emoji_mix_service.explicit_pattern, text):
            state["code1"] = matched.group("code1")
            state["code2"] = matched.group("code2")
            logger.info(f"[emojimix] MATCHED! {state['code1']}+{state['code2']}")
            return True
        return False
    except Exception as e:
        logger.error(f"[emojimix] check error: {e}")
        return False


async def _check_auto(state: T_State, text: str = EventPlainText()) -> bool:
    """自动模式匹配：两个相邻 emoji。"""
    if not plugin_config.emojimix_auto:
        return False
    text = text.strip()
    if not text or "+" in text:
        return False
    if matched := re.search(emoji_mix_service.auto_pattern, text):
        state["code1"] = matched.group("code1")
        state["code2"] = matched.group("code2")
        return True
    return False


# Matcher 注册
emojimix = on_message(Rule(_check_explicit, _check_cooldown), block=True)
auto_emojimix_matcher = on_message(
    Rule(_check_auto, _check_cooldown), block=False, priority=20
)


# Handler


@emojimix.handle()
async def handle_emojimix(event: Event, state: T_State, matcher: Matcher):
    """处理显式 emoji 合成请求 (emoji1+emoji2)"""
    if _cooldown_cache is not None:
        _cooldown_cache[event.get_user_id()] = True
    try:
        result = await emoji_mix_service.mix_emoji(state["code1"], state["code2"])
        await UniMessage.image(raw=result).finish()
    except UnsupportedEmojiError as e:
        await UniMessage.text(f"不支持的emoji：{e.emoji}").finish()
    except ComboNotFoundError:
        await UniMessage.text("不支持该emoji组合").finish()
    except DownloadError:
        await UniMessage.text("下载表情出错").finish()


@auto_emojimix_matcher.handle()
async def handle_auto_emojimix(event: Event, state: T_State, matcher: Matcher):
    """处理自动 emoji 合成（检测相邻 emoji 对）"""
    if _cooldown_cache is not None:
        _cooldown_cache[event.get_user_id()] = True
    try:
        result = await emoji_mix_service.mix_emoji(state["code1"], state["code2"])
        await UniMessage.image(raw=result).send()
    except (UnsupportedEmojiError, ComboNotFoundError, DownloadError):
        # 自动模式下，合成失败时静默忽略
        pass
