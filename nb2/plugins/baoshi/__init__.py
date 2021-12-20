from pathlib import Path

import nonebot

import nonebot
from nonebot import require
from datetime import datetime
from nonebot.adapters.cqhttp import Bot, Event, MessageSegment, Message
scheduler = require("nonebot_plugin_apscheduler").scheduler

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



global_config = nonebot.get_driver().config
imgRoot=global_config.dict()['imgroot']

@scheduler.scheduled_job("cron",  id="baoshi",hour="*/1",minute="0")
async def run_every_2_hour():
   hour = datetime.now().hour
   if 1<=hour<=8:
       return 
   if hour>12:
       hour-=12
   if hour==0:
       hour=12
   bot = nonebot.get_bots()
   if bot :
       bot = bot['1928994748']
       await bot.send_msg(message=MessageSegment.image(f"file:///{imgRoot}QQbotFiles/baoshi/{hour}.png"),user_id="1761512493")
@scheduler.scheduled_job("cron",  id="xiaban",hour="20",minute="00")
async def workTMD():
   bot = nonebot.get_bots()
   if bot :
       bot = bot['1928994748']
       await bot.send_msg(message=MessageSegment.image(f"file:///{imgRoot}/QQbotFiles/baoshi/workTMD.png"),user_id="1761512493")
