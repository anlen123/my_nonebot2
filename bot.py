#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""my_nonebot2 启动入口：初始化 nonebot、挂上 QQ 适配器、加载全部插件。

插件按下面的顺序加载，而顺序会影响同优先级匹配器的裁决，调整前请先确认没有重叠触发词。
第三方插件用 pip 安装的包名加载，本仓库自带的插件用相对项目根目录的模块路径加载。
"""

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBot_V11_Adapter

PLUGINS = (
    # ── 第三方插件（pip 安装）──
    "nonebot_plugin_ygo",
    "nonebot_plugin_apscheduler",
    "nonebot_plugin_abbrreply",
    "nonebot_plugin_plus_one",
    "nonebot_plugin_navicat",
    # ── 本仓库自带的插件 ──
    "plugins.love",
    "plugins.nonebot_plugin_pixiv.nonebot_plugin_pixiv",
    "plugins.nonebot_plugin_sbbot",
    "plugins.nonebot_plugin_biliav",
    "nonebot_plugin_waiter",
    "plugins.nonebot_plugin_masterduel.nonebot_plugin_masterduel",
    "plugins.nonebot_plugin_xuanran",
    "plugins.nonebot_plugin_yulu",
    "plugins.bilibili_live",
    "plugins.bilibili_video",
    "plugins.auto_message",
    "plugins.bazaardb",
    "plugins.repeater",
    "plugins.welcome",
    "plugins.seed_analyzer",
    "plugins.nonebot_plugin_auto_emojimix.nonebot_plugin_auto_emojimix",
    "plugins.deepseek_balance",
    "plugins.ai_news",
    "plugins.note",
    "plugins.jev_judge",
)

nonebot.init()
driver = nonebot.get_driver()
driver.register_adapter(OneBot_V11_Adapter)
app = nonebot.get_asgi()

for _plugin in PLUGINS:
    nonebot.load_plugin(_plugin)

if __name__ == "__main__":
    nonebot.run(app="__mp_main__:app")
