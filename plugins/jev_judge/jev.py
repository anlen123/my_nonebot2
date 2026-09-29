"""jev_judge —— Typesafe / Jev 接口客户端与结果渲染

接口：https://docs.typesafe.ai
  POST https://api.typesafe.ai/v1/systemone
  {
    "state":     "中国是世界上最大的国家",
    "model":     "jev-latest",
    "questions": { "correct": { "type": "noul", "instructions": "描述是正确的" } }
  }

noul 题型返回的是 0~1 的浮点概率（1.0 = 是，0.0 = 否）。
instructions 既可以是陈述（判断它是否成立），也可以是一句问句（文档里的
官方例子就是 "Is the customer asking for a human agent?"）。

注意：任何日志 / 报错信息里都不能出现 API key。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx
import nonebot

from .config import JevConfig

__all__ = [
    "JevError",
    "ask",
    "render_result",
    "segments_of",
    "quoted_state",
    "build_questions",
]

logger = nonebot.logger

# 结果里预览原文的最大字数
_PREVIEW_CHARS = 60


class JevError(Exception):
    """调用 Jev 接口失败，message 是可以直接发给用户的中文提示"""


def _log_body(url: str, payload: Dict[str, Any], api_key: str) -> None:
    """把请求体记进日志，方便核对「到底发了什么」

    密钥在请求头里、不在体内，这里仍做一次兜底替换：万一有人把 key 写进了
    state，日志里也不能出现它。日志固定压成一行，便于 grep。
    """
    try:
        body = json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        body = repr(payload)
    if api_key and api_key in body:
        body = body.replace(api_key, "***")
    logger.info(f"[jev] 请求 {url} body={body}")


def build_state(state: str) -> Any:
    """state 用纯字符串（官方 quickstart / 接口参考的写法）

    2026-09-29 实测记录，别再反复试：
      * 官方样例、接口参考里 state 都是字符串，这是规范写法
      * 控制台（Playground）的 state 编辑器是 JSON 编辑器，会把 state 包成
        {"state": "…"} 再发出去 —— 那是它的编辑器形态，不是接口要求
      * 同一句话、同一个问题，包对象 vs 纯字符串，模型给分系统性差约 14 个点
        （包对象偏低，例：10% 对 24%），所以拿控制台的数来对机器人时要留意口径
    """
    return state


def build_questions(cfg: JevConfig, instructions: Optional[str] = None) -> Dict[str, Any]:
    """构造一条 noul 问题；给了 instructions 就用它，否则用配置里的默认问题"""
    return {
        cfg.question_key: {
            "type": "noul",
            "instructions": instructions or cfg.instructions,
        }
    }


async def ask(
    state: str, cfg: JevConfig, *, instructions: Optional[str] = None
) -> Dict[str, Any]:
    """调用 Jev 接口，返回原始响应 dict

    instructions 覆盖默认问题（引用模式下由用户输入决定）。
    所有网络 / 状态码 / 解析问题都转成 JevError，提示语可以直接发给用户。
    """
    payload = {
        "state": build_state(state),
        "model": cfg.model,
        "questions": build_questions(cfg, instructions),
    }
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }

    _log_body(cfg.base_url, payload, cfg.api_key)

    kwargs: Dict[str, Any] = {"timeout": cfg.timeout}
    if cfg.proxy:
        kwargs["proxy"] = cfg.proxy

    try:
        async with httpx.AsyncClient(**kwargs) as client:
            resp = await client.post(cfg.base_url, json=payload, headers=headers)
    except httpx.TimeoutException:
        raise JevError(
            f"⏱️ 请求超时（{cfg.timeout:g}s）。"
            "如果只是网络不通，可在 .env.dev 配置 JEV_PROXY（如 http://127.0.0.1:7890）"
        ) from None
    except httpx.HTTPError as exc:
        raise JevError(f"🌐 网络错误：{type(exc).__name__}: {exc}") from exc

    if resp.status_code == 401:
        raise JevError("🔑 API key 无效或已过期（401），请检查 .env.dev 里的 TYPESAFE_API_KEY")
    if resp.status_code == 429:
        raise JevError("🚦 请求太频繁了（429），缓一会儿再试")
    if resp.status_code != 200:
        raise JevError(f"❌ 接口返回 {resp.status_code}：{resp.text[:300]}")

    try:
        return resp.json()
    except ValueError:
        raise JevError(f"❌ 接口返回的不是合法 JSON：{resp.text[:200]}") from None


# ── 被引用消息 → 可判断的文字 ─────────────────────────────────────────────────
# OneBot 的 message 字段有三种形态：MessageSegment 列表（适配器取回的 event.reply）、
# 段字典列表、带 CQ 码的字符串（老实现）
_CQ_RE = re.compile(r"\[CQ:([a-zA-Z_]+)((?:,[^\]]*)?)\]")


def _as_segment(item: Any) -> Optional[Dict[str, Any]]:
    """把 MessageSegment / 段字典统一成 {type, data}；认不出来返回 None"""
    data = getattr(item, "data", None)
    if data is not None and getattr(item, "type", None) is not None:
        return {
            "type": str(item.type),
            "data": dict(data) if isinstance(data, dict) else {},
        }
    if isinstance(item, dict):
        raw_data = item.get("data")
        return {
            "type": str(item.get("type") or ""),
            "data": raw_data if isinstance(raw_data, dict) else {},
        }
    return None


def segments_of(raw: Any) -> List[Dict[str, Any]]:
    """把 OneBot 的 message 字段统一成「段字典」列表

    适配器给的是 Message（MessageSegment 列表）、有些实现给段字典列表、
    还有的给 CQ 码字符串 —— 三种都认。认不出来的形状返回空列表。
    """
    if isinstance(raw, str):
        parts: List[Dict[str, Any]] = []
        last = 0
        for match in _CQ_RE.finditer(raw):
            if match.start() > last:
                parts.append({"type": "text", "data": {"text": raw[last:match.start()]}})
            params: Dict[str, str] = {}
            for pair in match.group(2).lstrip(",").split(","):
                if "=" in pair:
                    key, _, value = pair.partition("=")
                    params[key] = value
            parts.append({"type": match.group(1), "data": params})
            last = match.end()
        if last < len(raw):
            parts.append({"type": "text", "data": {"text": raw[last:]}})
        if not parts:
            parts = [{"type": "text", "data": {"text": raw}}]
        return parts

    if isinstance(raw, (list, tuple)):
        return [seg for seg in (_as_segment(item) for item in raw) if seg is not None]

    single = _as_segment(raw)
    return [single] if single is not None else []


def quoted_state(message: Any) -> Tuple[str, bool, bool]:
    """从被引用的消息里取出可判断的文字

    返回 (state 文字, 是否含图片, 是否有真正的文字)。
    @ 与表情会还原成可读占位（否则「你看这个 @某人」这种上下文会断），但只有
    占位符、没有真正文字时第三个值为 False，调用方据此回「没有可判断的文字」。
    图片只做标记 —— Jev 官方明确只吃文本，图片不发给接口。
    """
    texts: List[str] = []
    has_image = False
    has_text = False
    for seg in segments_of(message):
        kind = str(seg.get("type") or "")
        data = seg.get("data") or {}
        if kind == "text":
            body = str(data.get("text") or "")
            texts.append(body)
            has_text = has_text or bool(body.strip())
        elif kind == "image":
            has_image = True
        elif kind == "at":
            qq = data.get("qq")
            texts.append(f"@{qq}" if qq else "@某人")
        elif kind in ("face", "mface"):
            texts.append("[表情]")
    return "".join(texts).strip(), has_image, has_text


# ── 渲染 ──────────────────────────────────────────────────────────────────────

def _num(value: Any) -> Optional[float]:
    """概率可能被写成 0.87，也可能是 "0.87"；转不动就当没结果。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _preview(text: str) -> str:
    """把描述压成单行并截到 _PREVIEW_CHARS，别让长文把回复撑爆。"""
    flat = " ".join(text.split())
    if len(flat) > _PREVIEW_CHARS:
        return flat[:_PREVIEW_CHARS] + "…"
    return flat


def render_result(
    state: str,
    data: Dict[str, Any],
    cfg: JevConfig,
    *,
    question: Optional[str] = None,
    quoted: bool = False,
    note: Optional[str] = None,
) -> str:
    """把接口响应渲染成一条可以直接发出去的文本

    question 给出时（引用模式）会多一行「问的是什么」；quoted 决定原文用 📎 引用
    还是 📄 陈述的图标；note 是「图片已忽略 / 内容被截断」这类补充说明。
    """
    answers = data.get("answers") or {}
    answer = answers.get(cfg.question_key) or {}
    value = _num(answer.get("noul"))

    if value is None:
        verdict = "❔ 没解析出判断结果"
    else:
        correct = value >= 0.5
        verdict = f"{'✅ 正确' if correct else '❌ 不正确'} · 置信度 {value:.0%}"

    icon = "📎 引用" if quoted else "📄"
    lines = ["🤖 Jev 判断", ""]
    lines.append(f"{icon}：「{_preview(state)}」" if quoted else f"📄 「{_preview(state)}」")
    if question:
        lines.append(f"❓ 问题：{_preview(question)}")
    lines.extend(["", verdict])
    if note:
        lines.extend(["", f"（{note}）"])

    # 页脚只列真正拿到的信息：模型名可能缺失，usage 缺失时不编造 token 数
    model = str(data.get("model") or "")
    usage = data.get("usage") or {}
    footer = [model] if model else []
    if usage:
        footer.append(
            f"{usage.get('input_tokens', '?')}+{usage.get('output_tokens', '?')} tokens"
        )
    if footer:
        lines.extend(["", "── " + " · ".join(footer)])

    return "\n".join(lines).strip()
