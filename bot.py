#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""my_nonebot2 启动入口：初始化 nonebot、挂上 QQ 适配器、加载全部插件。

插件按下面的顺序加载，而顺序会影响同优先级匹配器的裁决，调整前请先确认没有重叠触发词。
第三方插件用 pip 安装的包名加载，本仓库自带的插件用相对项目根目录的模块路径加载。
"""

import os

# 访问本机服务（如本机 Hermes API 127.0.0.1:8642）时必须绕过系统代理：
# httpx 默认按 Windows 系统代理设置走代理，而系统代理的绕过列表（ProxyOverride）对 httpx 无效，
# 于是「插件访问 127.0.0.1」的请求被丢给代理端，收到 502（curl 不受影响，容易误判成服务端故障）。
# 必须在 import nonebot / httpx 之前设好。
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost,::1")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost,::1")

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
    # ── 第三方插件（本地仓库形式克隆在 plugins/ 下，保留 .git，可直接 git pull 更新）──
    "plugins.nonebot_plugin_group_historian.nonebot_plugin_group_historian",
    "plugins.nonebot_plugin_helper_recall.nonebot_plugin_helper_recall",
    "plugins.nonebot_plugin_hermes.nonebot_plugin_hermes",
)

nonebot.init()
driver = nonebot.get_driver()
driver.register_adapter(OneBot_V11_Adapter)
app = nonebot.get_asgi()

for _plugin in PLUGINS:
    nonebot.load_plugin(_plugin)

if __name__ == "__main__":
    nonebot.run(app="__mp_main__:app")
