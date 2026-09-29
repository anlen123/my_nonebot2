"""jev_judge —— 用 Jev（Typesafe）判断一句话是否正确，或对被引用的消息提问

两种用法：

1) 直接判断（老用法，行为不变）：

    判断 <一个陈述>

   「判断」+ 空格开头，后面整段作为 state 发给 Jev，问题固定为 JEV_INSTRUCTIONS
   （默认「描述是正确的」）。结尾的问号可有可无（有的话会被去掉）。

    判断 中国是世界上最大的国家
    → POST api.typesafe.ai/v1/systemone
      {"state": "中国是世界上最大的国家", "model": "jev-latest",
       "questions": {"correct": {"type": "noul", "instructions": "描述是正确的"}}}

2) 引用提问（新用法）：

    <引用一条消息> 判断 <一句问题>

   被引用的**文字**作为 state，你写的那句作为问题（questions.instructions）。
   只引用、不写问题（或只发「判断」两个字）时，问题回退成 JEV_INSTRUCTIONS。

    Jev 官方明确只接受文本（图片/音频/视频都不支持），所以引用到图片时不会去
    编内容，而是直接回一句「引用的是图片，判断不了」，不额外调用任何别的接口。
    引用内容超过 JEV_MAX_STATE 时截取前若干字，并在结果里注明。

被引用的消息由 OneBot 适配器预先取好放在 event.reply 里，这里不额外调接口。

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
from .jev import JevError, ask, quoted_state, render_result

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


def extract_question(text: str, prefix: str) -> Optional[str]:
    """引用模式下取「判断」后面的问题

    * 不是「判断」（或「判断 + 空白」）开头 → None，表示不该触发
    * 只写了「判断」两个字 → 返回空串，表示触发但没给问题（调用方回退默认问题）
    """
    text = text.strip()
    if text == prefix:
        return ""
    if not text.startswith(prefix):
        return None
    body = text[len(prefix):]
    if not body[:1].isspace():
        return None
    return body.strip().rstrip("?？").strip()


def _has_reply(event: MessageEvent) -> bool:
    """消息是否引用了别的消息

    坑：适配器把被引内容取回来后会 `del event.message[index]` 删掉 reply 段，
    所以只扫 event.message 永远是 False（坑过一次）。正确信号有两个：
      * event.reply 有值 → 适配器已把被引消息取回来（正常情况）
      * original_message 里还有 reply 段 → 引用了但没取回来（消息被撤回/太旧）
    """
    if event.reply is not None:
        return True
    original = getattr(event, "original_message", None)
    return any(seg.type == "reply" for seg in original or [])


async def _is_jev(event: MessageEvent) -> bool:
    text = event.get_plaintext()
    if _has_reply(event):
        # 引用模式：允许只发「判断」两个字
        return extract_question(text, CFG.prefix) is not None
    return extract_state(text, CFG.prefix) is not None


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

    text = event.get_plaintext()
    quoted = _has_reply(event)
    question: Optional[str] = None
    note: Optional[str] = None

    if quoted:
        asked = extract_question(text, CFG.prefix)
        if asked is None:  # rule 已经校验过，这里兜底
            return
        if event.reply is None:
            await jev_cmd.finish(
                "🚫 拿不到被引用的消息（可能已撤回或太旧），换一条重新引用试试～"
            )
            return
        # 引用内容即 state：文字照用，图片只标记（Jev 官方只支持文本输入）
        state, has_image, has_text = quoted_state(event.reply.message)
        if not has_text:
            if has_image:
                await jev_cmd.finish(
                    "🖼️ 引用的是图片，Jev 只认文字、判断不了～"
                    "（官方接口不支持图片输入）请引用文字消息再试"
                )
            await jev_cmd.finish("📭 引用的消息里没有可判断的文字～（语音、表情这类都不行）")
        if has_image:
            note = "引用里的图片已忽略，Jev 只认文字"
        if len(state) > CFG.max_state:
            state = state[: CFG.max_state]
            note = "；".join(x for x in (f"引用内容过长，只取了前 {CFG.max_state} 字", note) if x)
        # 没写问题时回退到配置里的默认问题（默认「描述是正确的」）
        question = asked or CFG.instructions
    else:
        state = extract_state(text, CFG.prefix)
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
        data = await ask(state, CFG, instructions=question)
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
        text = render_result(
            state,
            data,
            CFG,
            question=question if quoted else None,
            quoted=quoted,
            note=note,
        )
    except Exception as exc:  # noqa: BLE001
        nonebot.logger.exception("[jev] 结果渲染失败")
        await jev_cmd.finish(f"结果解析失败：{type(exc).__name__}: {str(exc)[:200]}")

    nonebot.logger.info(
        f"[jev] {user_id} 判断了 {len(state)} 字" + ("（引用）" if quoted else "")
    )
    await bot.send(event=event, message=MessageSegment.text(text))
