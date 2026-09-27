"""jev_judge —— 用 Jev（Typesafe）判断一句话是否正确

触发格式固定为：

    判断 <一个陈述>

「判断」+ 空格开头，后面整段作为 state 发给 Jev，问题固定为一条
noul：「描述是正确的」。结尾的问号可有可无（有的话会被去掉）。

例：
    判断 中国是世界上最大的国家
    → POST api.typesafe.ai/v1/systemone
      {"state": "中国是世界上最大的国家", "model": "jev-latest",
       "questions": {"correct": {"type": "noul", "instructions": "描述是正确的"}}}

配置见 .env.dev 的 TYPESAFE_* / JEV_* 项。
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

import nonebot
from nonebot import on_message
from nonebot.adapters.onebot.v11 import (
    Bot,
    MessageEvent,
    MessageSegment,
    permission,
)
from nonebot.rule import Rule

from .config import load_config
from .jev import JevError, ask, render_result

# ── 加载配置 ──────────────────────────────────────────────────────────────────
CFG = load_config()

# 同一用户上次调用的时间戳（monotonic 秒），防连点烧额度
_cooldown: Dict[str, float] = {}


# ── 触发规则 ──────────────────────────────────────────────────────────────────
def extract_state(text: str, prefix: str) -> Optional[str]:
    """「判断 中国最大」->「中国最大」

    必须是「前缀 + 空白」开头，后面整段就是 state。前缀后强制要有空白，
    否则「判断力」「判断句」这类以「判断」打头的词也会被误触发。
    结尾的问号可有可无，有的话会被去掉。形状不对或 state 为空返回 None。
    """
    text = text.strip()
    if not text.startswith(prefix):
        return None
    body = text[len(prefix):]
    if not body[:1].isspace():
        return None
    state = body.strip().rstrip("?？").strip()
    return state or None


async def _is_jev(event: MessageEvent) -> bool:
    return extract_state(event.get_plaintext(), CFG.prefix) is not None


jev_cmd = on_message(
    rule=Rule(_is_jev),
    permission=permission.PRIVATE | permission.GROUP,
    priority=5,
    block=True,
)


# ── 辅助 ──────────────────────────────────────────────────────────────────────
async def _drop(bot: Bot, message: Optional[Dict[str, Any]]) -> None:
    """删除「判断中」提示。

    删不掉只影响一条提示（可能已被撤回或已超时），不值得打断主流程。
    """
    if not message:
        return
    try:
        await bot.delete_msg(message_id=message["message_id"])
    except Exception as exc:  # noqa: BLE001
        nonebot.logger.warning(f"[jev] 删除「判断中」提示失败：{exc}")


# ── 事件处理 ──────────────────────────────────────────────────────────────────
@jev_cmd.handle()
async def _handle_jev(event: MessageEvent, bot: Bot) -> None:
    if not CFG.api_key:
        await jev_cmd.finish(
            "⚙️ 还没配置 Jev 的 API key，用不了。\n"
            "去 https://console.typesafe.ai/keys 拿到 key，"
            "填进 .env.dev 的 TYPESAFE_API_KEY，然后重启机器人。"
        )

    state = extract_state(event.get_plaintext(), CFG.prefix)
    if not state:  # rule 已经校验过，这里兜底
        return

    if len(state) > CFG.max_state:
        await jev_cmd.finish(
            f"📏 描述有 {len(state)} 字，超过上限 {CFG.max_state} 字了，短一点再试～"
        )

    # 冷却检查
    user_id = str(event.get_user_id())
    now = time.monotonic()
    if CFG.cooldown > 0:
        waited = now - _cooldown.get(user_id, 0.0)
        if waited < CFG.cooldown:
            await jev_cmd.finish(f"⏳ 太频繁了，{CFG.cooldown - waited:.1f} 秒后再试")
    _cooldown[user_id] = now

    thinking: Optional[Dict[str, Any]] = None
    try:
        thinking = await bot.send(
            event=event, message=MessageSegment.text("🤔 判断中…")
        )
    except Exception as exc:  # noqa: BLE001 — 只是「正在处理」的提示，发不出去不影响判断
        nonebot.logger.warning(f"[jev] 发送「判断中」提示失败：{exc}")

    try:
        data = await ask(state, CFG)
    except JevError as exc:
        await _drop(bot, thinking)
        nonebot.logger.warning(f"[jev] 调用失败：{exc}")
        await jev_cmd.finish(f"判断失败：{exc}")
    except Exception as exc:  # noqa: BLE001
        await _drop(bot, thinking)
        nonebot.logger.exception("[jev] 未预期错误")
        await jev_cmd.finish(f"判断失败：{type(exc).__name__}: {str(exc)[:200]}")

    await _drop(bot, thinking)

    try:
        text = render_result(state, data, CFG)
    except Exception as exc:  # noqa: BLE001
        nonebot.logger.exception("[jev] 结果渲染失败")
        await jev_cmd.finish(f"结果解析失败：{type(exc).__name__}: {str(exc)[:200]}")

    nonebot.logger.info(f"[jev] {user_id} 判断了 {len(state)} 字")
    await bot.send(event=event, message=MessageSegment.text(text))
