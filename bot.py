#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBot_V11_Adapter

nonebot.init()
app = nonebot.get_asgi()
driver = nonebot.get_driver()
driver.register_adapter(OneBot_V11_Adapter)

nonebot.load_from_toml("pyproject.toml")

# nonebot.load_plugin("nonebot_plugin_pixiv")
nonebot.load_plugin('nonebot_plugin_ygo')
nonebot.load_plugin('nonebot_plugin_apscheduler')
nonebot.load_plugin('nonebot_plugin_abbrreply')
nonebot.load_plugin('nonebot_plugin_repeater')
nonebot.load_plugin('nonebot_plugin_navicat')

nonebot.load_plugin("plugins.love")
nonebot.load_plugin("plugins.nonebot_plugin_pixiv.nonebot_plugin_pixiv")

nonebot.load_plugin("plugins.nonebot_plugin_sbbot")
nonebot.load_plugin("plugins.nonebot_plugin_biliav")
nonebot.load_plugin("plugins.nonebot_plugin_picsearcher.nonebot_plugin_picsearcher")
nonebot.load_plugin("plugins.nonebot_plugin_masterduel.nonebot_plugin_masterduel")
# nonebot.load_plugin("plugins.nonebot_plugin_command") #看情况恢复吧， win上使用太危险了
nonebot.load_plugin("plugins.nonebot_plugin_xuanran")
# nonebot.load_plugin("plugins.nonebot_plugin_yulu")

# nonebot.load_plugin("plugins.chat_gpt")

if __name__ == "__main__":
    # nonebot.logger.warning("Always use `nb run` to start the bot instead of manually running!")
    nonebot.run(app="__mp_main__:app")
