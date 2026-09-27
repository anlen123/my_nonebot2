"""love —— 群里的小彩蛋命令

用法：
  ll / love          机器人回一句「我也爱你」
  菜单（需 @机器人）  列出机器人当前支持的功能

说明：本文件原先还带着一套没启用的图片识别 Rule 和一个没人调用的转发消息辅助函数，
已删除；这里只剩两个真正注册的匹配器。
"""

from __future__ import annotations

from nonebot import on_regex
from nonebot.adapters.onebot.v11 import Bot, Event
from nonebot.rule import to_me

love = on_regex(pattern="^(ll|love)$")


@love.handle()
async def love_rev(bot: Bot, event: Event) -> None:
    await bot.send(event, message="我也爱你")


qqbot_des = on_regex(pattern="^菜单$", rule=to_me())


@qqbot_des.handle()
async def qqbot_des_rev(bot: Bot, event: Event) -> None:
    msg = """qqbot使用说明如下：
1.love, 描述：会给你回复love
2.st, 描述：会发一张色图(无了)
3.sx NB, 描述：通过缩写查全意
4.xr https://baidu.com, 描述：渲染网页成图片
5.yl, 描述：发送上传过的语录，使用上传语录，可以上传图片
6.输入b站的av,或者BV号，描述：给出视频的一些基本信息
7.搜图
8.pixiv pid, 描述：懂的都懂
9.ygo 闪刀，描述：游戏王查卡器
10.ck 游戏王查卡
11.dsr 你的问题（dsr是R1模型, ds3是v3模型），描述：deepseek回答你的问题, dsclear清除上下文
12.gm 你的问题，描述：gnmini回答你的问题, gmclear清除上下文
13.gmt 你的问题，描述：gnmini的推理模型回答你的问题, gmclear清除上下文
-------后续新加功能会补充
    """
    await bot.send(event, message=msg)
