from nonebot import on_command,on_startswith
from nonebot.rule import to_me
from nonebot.adapters.cqhttp import Bot, Event, MessageSegment, Message
import nonebot
love = on_startswith(msg="love", priority=4, rule=to_me())
from datetime import datetime

# 识别参数 并且给state 赋值

@love.handle()
async def love_rev(bot: Bot, event: Event, state: dict):
    await love.finish(message="我也爱你", at_sender=True)


from nonebot import require

scheduler = require("nonebot_plugin_apscheduler").scheduler

# @scheduler.scheduled_job("cron", minute="*/1", id="baoshi")
@scheduler.scheduled_job("cron", hour="*/1", id="baoshi",minute="0")
async def run_every_2_hour():
    await nonebot.get_bots()['1928994748'].send_msg(message_type="group",message=f"整点报时: {datetime.now().hour}",group_id=68724983)