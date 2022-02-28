#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBot_V11_Adapter
nonebot.init()
app = nonebot.get_asgi()
driver = nonebot.get_driver()
driver.register_adapter(OneBot_V11_Adapter)

# Custom your logger
# 
# from nonebot.log import logger, default_format
# logger.add("error.log",
#            rotation="00:00",
#            diagnose=False,
#            level="ERROR",
#            format=default_format)

# You can pass some keyword args config to init function


# nonebot.load_builtin_plugins()

# Please DO NOT modify this file unless you know what you are doing!
# As an alternative, you should use command `nb` or modify `pyproject.toml` to load plugins
nonebot.load_from_toml("pyproject.toml")

nonebot.load_plugin("nonebot_plugin_gocqhttp")
nonebot.load_plugin('nonebot_plugin_navicat')
nonebot.load_plugin('nonebot_plugin_apscheduler')
nonebot.load_plugin('nonebot_plugin_abbrreply')
nonebot.load_plugin("nonebot_plugin_status")
nonebot.load_plugin("nonebot_plugin_pixiv")
nonebot.load_plugin("nonebot_plugin_biliav")
nonebot.load_plugin('nonebot_plugin_directlinker')
nonebot.load_plugin('nonebot_plugin_repeater')

nonebot.load_plugin("plugins.love")
nonebot.load_plugin("plugins.command")
nonebot.load_plugin("plugins.setu")
nonebot.load_plugin("plugins.xuanran")
nonebot.load_plugin("plugins.sendimg")
nonebot.load_plugin("plugins.jd_test")
nonebot.load_plugin("plugins.ygo")


# nonebot.load_plugin("plugins.baoshi")
# nonebot.load_plugin("plugins.ban_recall")
# nonebot.load_plugin('nonebot_plugin_test')
# nonebot.load_plugin("nonebot_plugin_docs")

if __name__ == "__main__":
    nonebot.logger.warning("Always use `nb run` to start the bot instead of manually running!")
    nonebot.run(app="__mp_main__:app")
