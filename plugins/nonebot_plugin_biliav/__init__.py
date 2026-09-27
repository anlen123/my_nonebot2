"""哔哩哔哩视频信息 —— 群里发 av/BV 号就能查

用法：
  消息里带 av1234567 或 BV1xx411c7mD 时自动查询，回复标题、封面、统计、链接与简介。

说明：
  * 命中判定见 isAvBv()，正则保持原样（它对 BV 号的分段校验是本插件一贯的口径）；
  * 具体请求与文案拼装在 data_source.py 里。
"""

from __future__ import annotations

import re

from nonebot import get_driver, on_message
from nonebot.adapters.onebot.v11 import Bot, Event
from nonebot.rule import Rule

from .config import Config
from .data_source import get_av_data

global_config = get_driver().config
# 插件配置（目前没有自定义字段），沿用 nonebot 插件模板的写法
config = Config(**global_config.dict())

# 两个正则与旧代码逐字一致：前者只管「命中与否」，后者负责把号码抠出来
_AVBV_DETECT = r"av(\d{1,12})|BV(1[A-Za-z0-9]{2}4.1.7[A-Za-z0-9]{2})"
_AVBV_EXTRACT = r"av(\d{1,100})|BV(1[A-Za-z0-9]{2}4.1.7[A-Za-z0-9]{2,100})"


def isAvBv() -> Rule:
    """消息里出现 av/BV 号时命中。"""

    async def isisAvBv_(bot: Bot, event: Event) -> bool:
        if event.get_type() != "message":
            return False
        return bool(re.findall(_AVBV_DETECT, str(event.get_plaintext())))

    return Rule(isisAvBv_)


biliav = on_message(rule=isAvBv())


@biliav.handle()
async def handle(bot: Bot, event: Event) -> None:
    if not event.get_plaintext().strip():
        return

    matched = re.search(_AVBV_EXTRACT, str(event.get_message()))
    if not matched:
        return

    reply = await get_av_data(matched[0])
    await bot.send(event=event, message=reply)
