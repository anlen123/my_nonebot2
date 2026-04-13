"""
welcome —— 新成员入群欢迎插件

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

import json
import os
from pathlib import Path
from typing import Dict

import nonebot
from nonebot.plugin import on_notice
from nonebot.adapters.onebot.v11 import Bot, GroupIncreaseNoticeEvent, MessageSegment, Message


# ── 读取配置 ──────────────────────────────────────────────────────────────────

def _find_env_file() -> Path:
    root = Path(__file__).parent.parent.parent
    for name in (".env.dev", ".env.prod", ".env"):
        p = root / name
        if p.exists():
            return p
    return root / ".env.dev"


def _parse_env_file(path: Path) -> Dict[str, str]:
    result = {}
    if not path.exists():
        return result
    lines = path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # 支持多行值（括号未闭合时继续拼接）
        open_b  = value.count("[") + value.count("{")
        close_b = value.count("]") + value.count("}")
        while open_b > close_b and i < len(lines):
            nxt = lines[i].strip()
            i += 1
            if nxt.startswith("#"):
                continue
            value  += nxt
            open_b  += nxt.count("[") + nxt.count("{")
            close_b += nxt.count("]") + nxt.count("}")
        result[key] = value
    return result


def load_welcome_config() -> Dict[str, dict]:
    """
    返回 { "群号": {"message": str, "image": str} }
    """
    env_path = _find_env_file()
    raw = _parse_env_file(env_path)
    cfg_raw = raw.get("WELCOME_CONFIG", os.environ.get("WELCOME_CONFIG", "{}"))
    try:
        cfg = json.loads(cfg_raw)
    except (json.JSONDecodeError, TypeError):
        cfg = {}
    return cfg


WELCOME_CONFIG: Dict[str, dict] = load_welcome_config()

# ── 事件处理 ──────────────────────────────────────────────────────────────────

welcome = on_notice()


@welcome.handle()
async def welcome_handle(bot: Bot, event: GroupIncreaseNoticeEvent):
    group_id = str(event.group_id)
    user_id  = event.user_id

    cfg = WELCOME_CONFIG.get(group_id)
    if not cfg:
        return

    msg_text = cfg.get("message", "")
    img_path = cfg.get("image", "").strip()

    # 构建消息：@新人 + 换行 + 欢迎语
    msg = Message()
    msg += MessageSegment.at(user_id)
    msg += MessageSegment.text(f"\n{msg_text}")

    # 图片
    if img_path:
        if img_path.startswith("http://") or img_path.startswith("https://"):
            msg += MessageSegment.image(img_path)
        else:
            p = Path(img_path)
            if p.exists():
                msg += MessageSegment.image(p.read_bytes())
            else:
                nonebot.logger.warning(f"[welcome] 图片不存在: {img_path}")

    await bot.send_group_msg(group_id=int(group_id), message=msg)
    nonebot.logger.info(f"[welcome] group={group_id} 欢迎新成员 uid={user_id}")


nonebot.logger.info(f"[welcome] 插件已加载，已配置 {len(WELCOME_CONFIG)} 个群")
