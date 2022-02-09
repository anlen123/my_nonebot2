from pathlib import Path

import nonebot
from nonebot import get_driver
from nonebot.plugin import on_regex

from .config import Config
from nonebot.adapters.onebot.v11 import Bot, Event
from nonebot.params import State,T_State
global_config = get_driver().config
config = global_config.dict()
if 'cmd_pre' in config:
    cmd_pre=config['cmd_pre']
else:
    cmd_pre=""

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

testx = on_regex(pattern="tt")

@testx.handle()
async def test_rev(bot: Bot, event: Event, state: T_State = State()) -> None:
    print(cmd_pre)
    pass
