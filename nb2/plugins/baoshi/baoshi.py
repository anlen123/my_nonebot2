import nonebot
from nonebot import require
from datetime import datetime
from nonebot.adapters.cqhttp import Bot, Event, MessageSegment, Message
scheduler = require("nonebot_plugin_apscheduler").scheduler

# @scheduler.scheduled_job("cron", minute="*/1", id="baoshi")
@scheduler.scheduled_job("cron",  id="baoshi",second="*/10")
async def run_every_2_hour():
   hour = datetime.now().hour
   if hour>12:
       hour-=12
   if hour==0:
       hour=12
   print(hour)
   bot = nonebot.get_bots()
   if bot :
       bot = bot['1928994748']
   await nonebot.get_bots()['1928994748'].send_msg(message_type="group",message=MessageSegment.image(f"file:////root/NextCloud/baoshi/{hour}.png"),group_id=68724983)
    #    await bot.send_msg(message=MessageSegment.image(f"file:////root/NextCloud/baoshi/{hour}.png"),user_id="1761512493")