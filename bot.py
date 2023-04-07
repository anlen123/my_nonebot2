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
# nonebot.load_plugin('nonebot_plugin_ygo')
nonebot.load_plugin("nonebot_plugin_gocqhttp")
nonebot.load_plugin('nonebot_plugin_apscheduler')
nonebot.load_plugin('nonebot_plugin_abbrreply')
nonebot.load_plugin("nonebot_plugin_status")
nonebot.load_plugin('nonebot_plugin_repeater')
nonebot.load_plugin('nonebot_plugin_aidraw')
nonebot.load_plugin('nonebot_plugin_navicat')
# nonebot.load_plugin('nonebot_plugin_chatgpt_turbo')
# nonebot.load_plugin('nonebot_plugin_customemote')

nonebot.load_plugin("plugins.love")
nonebot.load_plugin("plugins.command")
nonebot.load_plugin("plugins.setu")
nonebot.load_plugin("plugins.xuanran")
nonebot.load_plugin("plugins.sendimg")
nonebot.load_plugin("plugins.yulu")
nonebot.load_plugin("plugins.sbbot")
nonebot.load_plugin("plugins.nonebot_plugin_biliav")
nonebot.load_plugin("plugins.nonebot_plugin_picsearcher.nonebot_plugin_picsearcher")
nonebot.load_plugin("plugins.nonebot_plugin_pixiv.nonebot_plugin_pixiv")
nonebot.load_plugin("plugins.nonebot_plugin_ygo.nonebot_plugin_ygo")
# nonebot.load_plugin("plugins.nonebot_plugin_tgface")
nonebot.load_plugin("plugins.nonebot_plugin_chatgpt_turbo.nonebot_plugin_chatgpt_turbo")
nonebot.load_plugin("plugins.chat_gpt_piao")
# nonebot.load_plugin("plugins.nonebot_plugin_quote.nonebot_plugin_quote")

if __name__ == "__main__":
    # nonebot.logger.warning("Always use `nb run` to start the bot instead of manually running!")
    nonebot.run(app="__mp_main__:app")
