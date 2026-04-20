"""
keyword_image —— 关键词触发图片插件

当群消息中包含指定关键词时，从配置的文件夹中随机取一张图片发送。

配置（.env.dev / .env.prod）：
  KEYWORD_IMAGE_RULES=[
    {
      "keywords": ["哈哈", "笑死"],
      "folder": "C:/images/laugh",
      "groups": ["638793706", "548912612"]
    },
    {
      "keywords": ["awsl"],
      "folder": "/home/bot/images/awsl",
      "groups": []
    }
  ]

  groups 为空列表时表示所有群都生效。
"""

import json
import os
import random
from pathlib import Path
from typing import List, Dict

import nonebot
from nonebot.plugin import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment

IMG_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


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


def load_rules() -> List[dict]:
    """
    返回规则列表，每条规则：
    {
      "keywords": ["kw1", "kw2"],
      "folder": "/path/to/images",
      "groups": ["群号1", "群号2"]   # 空列表 = 所有群
    }
    """
    raw = _parse_env_file(_find_env_file())
    rules_raw = raw.get("KEYWORD_IMAGE_RULES", os.environ.get("KEYWORD_IMAGE_RULES", "[]"))
    try:
        rules = json.loads(rules_raw)
    except (json.JSONDecodeError, TypeError):
        rules = []
    return rules


RULES: List[dict] = load_rules()

# 构建关键词 -> 规则 的快速查找表
# { keyword: [rule, ...] }
_kw_index: Dict[str, List[dict]] = {}
for rule in RULES:
    for kw in rule.get("keywords", []):
        _kw_index.setdefault(kw, []).append(rule)

nonebot.logger.info(
    f"[keyword_image] 插件已加载，共 {len(RULES)} 条规则，"
    f"{len(_kw_index)} 个关键词"
)


# ── 图片选取 ──────────────────────────────────────────────────────────────────

def _pick_image(folder: str) -> Path | None:
    p = Path(folder)
    if not p.exists() or not p.is_dir():
        nonebot.logger.warning(f"[keyword_image] 文件夹不存在: {folder}")
        return None
    imgs = [f for f in p.iterdir() if f.is_file() and f.suffix.lower() in IMG_EXTS]
    if not imgs:
        nonebot.logger.warning(f"[keyword_image] 文件夹中没有图片: {folder}")
        return None
    return random.choice(imgs)


# ── 消息处理 ──────────────────────────────────────────────────────────────────

kw_img = on_message(priority=50, block=False)


@kw_img.handle()
async def kw_img_handle(bot: Bot, event: GroupMessageEvent):
    if str(event.user_id) == str(bot.self_id):
        return

    text     = event.get_plaintext()
    group_id = str(event.group_id)

    # 找到第一个匹配的关键词对应的规则
    matched_rule = None
    for kw, rules in _kw_index.items():
        if kw in text:
            for rule in rules:
                groups = rule.get("groups", [])
                if not groups or group_id in groups:
                    matched_rule = rule
                    break
        if matched_rule:
            break

    if not matched_rule:
        return

    img_path = _pick_image(matched_rule["folder"])
    if not img_path:
        return

    nonebot.logger.info(
        f"[keyword_image] group={group_id} 触发关键词，发送图片: {img_path.name}"
    )
    await bot.send_group_msg(
        group_id=int(group_id),
        message=MessageSegment.image(img_path.read_bytes())
    )
