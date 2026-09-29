"""jev_judge 插件配置

配置项写在 .env.dev / .env.prod（nonebot 会把 key 统一转成小写，读的时候用小写名）。
全部有默认值，除了 API key 之外不配也能跑起来。

  TYPESAFE_API_KEY   Typesafe / Jev 的 API key，从 https://console.typesafe.ai/keys 获取
  TYPESAFE_BASE_URL  接口地址，默认 https://api.typesafe.ai/v1/systemone
  JEV_MODEL          模型名，默认 jev-latest
  JEV_PREFIX         触发前缀（后面必须跟一个空格），默认「判断」
  JEV_INSTRUCTIONS   默认问题（引用模式下没写问题时也用它），默认「描述是正确的」
  JEV_QUESTION_KEY   问题在请求 / 响应里的 key，默认 correct
  JEV_TIMEOUT        单次请求超时（秒），默认 30
  JEV_PROXY          请求代理，如 http://127.0.0.1:7890；留空直连
  JEV_COOLDOWN       同一用户两次调用最小间隔（秒），默认 3；0 = 不限制
  JEV_MAX_STATE      待判断内容的最大长度，默认 500（引用内容超长时截取前若干字）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

import nonebot
from nonebot import get_driver

__all__ = ["JevConfig", "load_config"]

# 单一默认值来源：下面构造配置时的回落值全部指向这里，同一个默认值只写一次
_DEFAULTS: Dict[str, Any] = {
    "typesafe_api_key": "",
    "typesafe_base_url": "https://api.typesafe.ai/v1/systemone",
    "jev_model": "jev-latest",
    "jev_prefix": "判断",
    "jev_instructions": "描述是正确的",
    "jev_question_key": "correct",
    "jev_timeout": 30.0,
    "jev_proxy": "",
    "jev_cooldown": 3.0,
    "jev_max_state": 500,
}


@dataclass(frozen=True)
class JevConfig:
    api_key: str
    base_url: str
    model: str
    prefix: str
    instructions: str
    question_key: str
    timeout: float
    proxy: str
    cooldown: float
    max_state: int


def _raw() -> Dict[str, Any]:
    """拿 nonebot 已加载的配置（.env.dev 里的自定义项都会在这里）

    配置对象由 nonebot 提供，可能抛出的异常无法穷举；读不到就整体回落默认值，
    不让插件因为一个读配置的小问题加载不起来。
    """
    try:
        return get_driver().config.model_dump()
    except Exception as exc:  # noqa: BLE001
        nonebot.logger.warning(f"[jev] 读取 driver 配置失败，全部用默认值：{exc}")
        return {}


def _as_float(value: Any, default: float) -> float:
    """配错的数值一律回落默认值，不因为一行 .env 写坏就让插件起不来。"""
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    """同 _as_float；先过 float 是为了容忍 "30.0" 这种写法。"""
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def load_config() -> JevConfig:
    """把 .env 里的 TYPESAFE_* / JEV_* 读成一个不可变的配置对象。"""
    raw = _raw()

    def get(key: str) -> Any:
        value = raw.get(key)
        # 空字符串按「没配」处理，这样 .env 里留空可以表达「用默认值」
        if value is None or (isinstance(value, str) and not value.strip()):
            return _DEFAULTS[key]
        return value

    def text(key: str) -> str:
        """取字符串项并去掉首尾空白。"""
        return str(get(key)).strip()

    cfg = JevConfig(
        api_key=text("typesafe_api_key"),
        base_url=text("typesafe_base_url").rstrip("/"),
        model=text("jev_model"),
        prefix=text("jev_prefix") or _DEFAULTS["jev_prefix"],
        instructions=text("jev_instructions") or _DEFAULTS["jev_instructions"],
        question_key=text("jev_question_key") or _DEFAULTS["jev_question_key"],
        timeout=_as_float(get("jev_timeout"), _DEFAULTS["jev_timeout"]),
        proxy=text("jev_proxy"),
        cooldown=max(0.0, _as_float(get("jev_cooldown"), _DEFAULTS["jev_cooldown"])),
        max_state=max(10, _as_int(get("jev_max_state"), _DEFAULTS["jev_max_state"])),
    )

    if not cfg.api_key:
        nonebot.logger.warning(
            "[jev] 未配置 TYPESAFE_API_KEY，插件已加载但无法调用接口。"
            "去 https://console.typesafe.ai/keys 拿 key 填进 .env.dev"
        )
    else:
        nonebot.logger.info(
            f"[jev] 已启用：前缀「{cfg.prefix}」· 模型 {cfg.model} · "
            f"问句「{cfg.instructions}」· 代理 {cfg.proxy or '直连'}"
        )

    return cfg
