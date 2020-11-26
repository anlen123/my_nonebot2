#!/root/yes/envs/qqbot/bin/python
# -*- coding: utf-8 -*-
import nonebot
nonebot.init()
app = nonebot.get_asgi()
# nonebot.load_plugins("src/plugins")
nonebot.load_plugins("plugins/love")
nonebot.load_plugins("plugins/null")
nonebot.load_plugins("plugins/setu")
nonebot.load_plugins("plugins/super")
nonebot.load_plugins("plugins/cmd")
nonebot.load_plugins("plugins/pixiv")
# nonebot.load_plugins("plugins/lanzouyun")
# nonebot.load_plugins("plugins/zhuanfa")
# nonebot.load_plugins("plugins/test")
# nonebot.load_plugins("plugins/weather")

# Modify some config / config depends on loaded configs
# 
# config = nonebot.get_driver().config
# do something...
if __name__ == "__main__":
    try:
        nonebot.run(app="bot:app")
    except:
        print("出现bug了")
