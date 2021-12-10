from pathlib import Path

import nonebot
from nonebot import get_driver

from .config import Config
from nonebot import on_command
from nonebot.rule import to_me,Rule
from nonebot.adapters.cqhttp import Bot, Event, MessageSegment, Message
import os
global_config = get_driver().config
config = Config(**global_config.dict())

# Export something for other plugin
# export = nonebot.export()
# export.foo = "bar"

# @export.xxx
# def some_function():
#     pass

_sub_plugins = set()
_sub_plugins |= nonebot.load_plugins(
    str((Path(__file__).parent / "plugins").
    resolve()))

testx = on_command(cmd="tt")

@testx.handle()
async def test_rev(bot: Bot, event: Event, state: dict):
    pass
