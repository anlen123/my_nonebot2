#!/root/miniconda3/bin/python
# -*- coding: utf-8 -*-
import nonebot
from nonebot.adapters.cqhttp import Bot as CQHTTPBot 
nonebot.init()
app = nonebot.get_asgi()
driver = nonebot.get_driver()
driver.register_adapter("cqhttp",CQHTTPBot)
nonebot.load_plugins("plugins/love")
nonebot.load_plugins("plugins/setu")
nonebot.load_plugins("plugins/cmd")
nonebot.load_plugins("plugins/pixiv")
nonebot.load_plugins("plugins/xuanran")
nonebot.load_plugins("plugins/weather")
nonebot.load_plugins("plugins/sendImg")
nonebot.load_plugin('nonebot_plugin_navicat')
nonebot.load_plugin('nonebot_plugin_apscheduler')
nonebot.load_plugin('nonebot_plugin_biliav')
nonebot.load_plugin('nonebot_plugin_abbrreply')
# nonebot.load_plugins("plugins/baoshi")
# nonebot.load_plugins("plugins/banrecall")
# nonebot.load_plugins("plugins/rep_recall")
# nonebot.load_plugins("plugins/ygo")
nonebot.load_plugin("nonebot_plugin_status")
# nonebot.load_plugin('nonebot_plugin_test')
# nonebot.load_plugin("nonebot_plugin_docs")




if __name__ == "__main__":
    try:
        nonebot.run(app="bot:app")
    except:
        print("出现bug了")
