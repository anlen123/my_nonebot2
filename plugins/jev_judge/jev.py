"""jev_judge —— Typesafe / Jev 接口客户端与结果渲染

接口：https://docs.typesafe.ai
  POST https://api.typesafe.ai/v1/systemone
  {
    "state":     "中国是世界上最大的国家",
    "model":     "jev-latest",
    "questions": { "correct": { "type": "noul", "instructions": "描述是正确的" } }
  }

noul 题型返回的是 0~1 的浮点概率（1.0 = 是，0.0 = 否）。

注意：任何日志 / 报错信息里都不能出现 API key。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from .config import JevConfig

__all__ = ["JevError", "ask", "render_result"]

# 结果里预览原文的最大字数
_PREVIEW_CHARS = 60


class JevError(Exception):
    """调用 Jev 接口失败，message 是可以直接发给用户的中文提示"""


def build_questions(cfg: JevConfig) -> Dict[str, Any]:
    """固定只问一条 noul：「描述是正确的」"""
    return {
        cfg.question_key: {
            "type": "noul",
            "instructions": cfg.instructions,
        }
    }


async def ask(state: str, cfg: JevConfig) -> Dict[str, Any]:
    """调用 Jev 接口，返回原始响应 dict

    所有网络 / 状态码 / 解析问题都转成 JevError，提示语可以直接发给用户。
    """
    payload = {
        "state": state,
        "model": cfg.model,
        "questions": build_questions(cfg),
    }
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }

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


def render_result(state: str, data: Dict[str, Any], cfg: JevConfig) -> str:
    """把接口响应渲染成一条可以直接发出去的文本"""
    answers = data.get("answers") or {}
    answer = answers.get(cfg.question_key) or {}
    value = _num(answer.get("noul"))

    if value is None:
        verdict = "❔ 没解析出判断结果"
    else:
        correct = value >= 0.5
        verdict = f"{'✅ 正确' if correct else '❌ 不正确'} · 置信度 {value:.0%}"

    lines = ["🤖 Jev 判断", "", f"📄 「{_preview(state)}」", "", verdict]

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
