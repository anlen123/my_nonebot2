"""welcome —— 新成员入群欢迎插件

当有新成员加入群聊时，自动发送：
  @新人
  欢迎语
  图片（可选）

配置（.env.dev / .env.prod）：
  WELCOME_CONFIG={
    "群号1": {"message": "欢迎加入！", "image": "/path/to/img.png"},
    "群号2": {"message": "欢迎～", "image": ""}
  }

  image 字段支持：
    - 本地绝对路径：/path/to/img.png
    - 网络 URL：https://example.com/img.png
    - 留空则不发图片
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import nonebot
from nonebot.adapters.onebot.v11 import Bot, GroupIncreaseNoticeEvent, Message, MessageSegment
from nonebot.plugin import on_notice

# 候选配置文件，按优先级从高到低
_ENV_FILENAMES = (".env.dev", ".env.prod", ".env")
# 仓库根目录：本文件位于 <root>/plugins/welcome/__init__.py
_ROOT = Path(__file__).parent.parent.parent

CONFIG_KEY = "WELCOME_CONFIG"


def _find_env_file() -> Path:
    """返回第一个存在的配置文件；一个都不存在时返回默认的 .env.dev 路径。"""
    for name in _ENV_FILENAMES:
        candidate = _ROOT / name
        if candidate.exists():
            return candidate
    return _ROOT / _ENV_FILENAMES[0]


def _unclosed(text: str) -> bool:
    """值的 [ { 比 ] } 多，说明这个值还没写完，得接着读下一行。"""
    return text.count("[") + text.count("{") > text.count("]") + text.count("}")


def _parse_env_file(path: Path) -> Dict[str, str]:
    """解析 key=value 文本；值支持跨多行（括号没配平时继续拼接，注释行跳过）。"""
    if not path.exists():
        return {}

    result: Dict[str, str] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        index += 1
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, _, value = line.partition("=")
        value = value.strip()
        while _unclosed(value) and index < len(lines):
            continuation = lines[index].strip()
            index += 1
            if continuation.startswith("#"):
                continue
            value += continuation

        result[key.strip()] = value
    return result


def load_welcome_config() -> Dict[str, Dict[str, Any]]:
    """读取欢迎配置，返回 { "群号": {"message": str, "image": str} }。"""
    raw = _parse_env_file(_find_env_file())
    cfg_raw = raw.get(CONFIG_KEY, os.environ.get(CONFIG_KEY, "{}"))
    try:
        cfg: Dict[str, Dict[str, Any]] = json.loads(cfg_raw)
    except (json.JSONDecodeError, TypeError):
        # 值不是合法 JSON 时按「没配置」处理，免得整个插件加载失败
        cfg = {}
    return cfg


WELCOME_CONFIG: Dict[str, Dict[str, Any]] = load_welcome_config()


def _image_segment(raw: str) -> Optional[MessageSegment]:
    """把配置里的 image 变成图片消息段；留空或本地文件缺失时返回 None。

    网络地址直接交给协议端下载，本地路径才读字节。
    """
    if not raw:
        return None
    if raw.startswith(("http://", "https://")):
        return MessageSegment.image(raw)

    path = Path(raw)
    if not path.exists():
        nonebot.logger.warning(f"[welcome] 图片不存在: {raw}")
        return None
    return MessageSegment.image(path.read_bytes())


welcome = on_notice()


@welcome.handle()
async def welcome_handle(bot: Bot, event: GroupIncreaseNoticeEvent) -> None:
    group_id = str(event.group_id)
    cfg = WELCOME_CONFIG.get(group_id)
    if not cfg:
        return

    # 消息构成：@新人 + 欢迎语（换行分隔）+ 可选图片
    message = Message()
    message += MessageSegment.at(event.user_id)
    message += MessageSegment.text(f"\n{cfg.get('message', '')}")

    image = _image_segment(str(cfg.get("image", "")).strip())
    if image is not None:
        message += image

    await bot.send_group_msg(group_id=int(group_id), message=message)
    nonebot.logger.info(f"[welcome] group={group_id} 欢迎新成员 uid={event.user_id}")


nonebot.logger.info(f"[welcome] 插件已加载，已配置 {len(WELCOME_CONFIG)} 个群")
