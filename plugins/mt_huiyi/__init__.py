from pathlib import Path

import nonebot
from nonebot import get_driver
from nonebot import require
from nonebot.adapters.cqhttp import Bot, Event, MessageSegment, Message, message
from nonebot.plugin import on_regex
import time
import re
from .config import Config

global_config = get_driver().config
config = Config(**global_config.dict())

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

import asyncio
scheduler = require("nonebot_plugin_apscheduler").scheduler
export = nonebot.require("nonebot_plugin_navicat")
clien = export.redis_client # redis的
cmd = ""

async def run(cmd):
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)

    stdout, stderr = await proc.communicate()
    return (stdout+stderr).decode()
import os 
@scheduler.scheduled_job("cron",  id="huiyi",hour="8",minute="0")
async def huiyi():
    global cmd
    huiyi_qiang = clien.get('huiyi').decode()
    if huiyi_qiang=='true':
        print("进来了")
        cmd = await xiu_cmd(cmd)
        msg = await run(cmd)
        bot = nonebot.get_bots()
        print("============")
        print(msg)
        print("============")
        msg = re.findall("成功",msg)
        if msg:
            msg = msg[0]
        else:
            msg = "失败"
        if bot :
            bot = bot['1928994748']
            await bot.send_msg(message=msg,user_id="1761512493")
    else:
        bot = nonebot.get_bots()
        if bot :
            bot = bot['1928994748']
        await bot.send_msg(message="你没有开启抢会议室功能",user_id="1761512493")

mt_huiyi_swatch = on_regex(pattern="^mt_huiyi_open$")

@mt_huiyi_swatch.handle()
async def mt_huiyi_swatch_m(bot: Bot,event: Event,state: dict):
    mt_huiti = clien.get("huiyi").decode()
    if mt_huiti =='true':
        clien.set('huiyi','false')
    else:
        clien.set('huiyi','true')
    msg1= clien.get("huiyi").decode()
    print(msg1)
    await bot.send(event=event,message=f"{msg1}")


# 修改cookie
cook_modify = on_regex(pattern="^mt_huiyi_cook$")

@cook_modify.handle()
async def cook_modify_method(bot: Bot,event: Event,state: dict):
    message = str(event.get_message()).strip()[13:]
    if message:
        state['cook'] = message

@cook_modify.got("cook",prompt="请输入你的新token")
async def cook_got(bot: Bot,event: Event,state: dict):
    global cook
    global start_time
    global end_time
    global cmd 
    cook = state['cook']
    clien.set("mt_cook",cook)
    await xiu_cmd(cmd)
    await bot.send(event=event,message="cook修改成功")


time_modify = on_regex(pattern="^mt_huiyi_time$")

@time_modify.handle()
async def time_modify_method(bot: Bot,event: Event,state: dict):
    message = str(event.get_message()).strip()[13:]
    if message:
        state['mt_time'] = message

@time_modify.got("mt_time",prompt="请输入时间如: 2021-12-21 19:00:00#2021-12-21 20:00:00")
async def cook_got(bot: Bot,event: Event,state: dict):
    global cook
    global start_time
    global end_time
    global cmd 

    mt_time = state['mt_time']
    start_ = str(mt_time).split("#")[0]
    end_ = str(mt_time).split("#")[1]
    clien.set("mt_start_time",start_)
    clien.set("mt_end_time",end_)
    await xiu_cmd(cmd)
    await bot.send(event=event,message="时间修改成功")

async def xiu_cmd(cmd):
    mt_cook = clien.get('mt_cook').decode()
    start_time = clien.get("mt_start_time").decode()
    start_time = await time2timeLong(start_time)
    end_time = clien.get("mt_end_time").decode()
    end_time = await time2timeLong(end_time)
    cmd=f"""
curl 'https://calendar.sankuai.com/api/v2/xm/schedules' \
  -H 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.93 Safari/537.36' \
  -H 'Content-Type: application/json' \
  -H 'access-token: {mt_cook}' \
  --data '{{"title":"","startTime":{start_time},"endTime":{end_time},"isAllDay":0,"location":"","attendees":["5492702"],"noticeType":0,"noticeRule":"P0Y0M0DT0H10M0S","recurrencePattern":{{"type":"NONE","showType":"NONE"}},"deadline":0,"memo":"","organizer":"5492702","room":{{"id":2292,"name":"攀枝花厅-培训室","email":"roomcdyf-panzhihua@meituan.com","capacity":15,"disabled":0,"floorId":459,"floorName":"5层","buildingId":156,"buildingName":"成都两江国际","equipId":2,"equipName":"投影","sort":null,"memo":"","displayName":null,"floorMap":"https://s3plus.meituan.net/v1/mss_e17221229a4b4f2dace2fe85851e628d/meeting-svg/五层地图.svg","roomName":"攀枝花厅","roomMap":"https://s3plus.meituan.net/v1/mss_e17221229a4b4f2dace2fe85851e628d/meeting-svg/459_攀枝花厅_1551346842585","price":null,"pointX":0.2292756901230923,"pointY":0.10823211130851983,"orgSiteCodeId":null,"orgCityId":null,"orgProvinceId":null,"orgNationId":null,"mobileMap":null,"window":"UNKNOWN","roomLocationUrl":"https://123.sankuai.com//huiyi/#/map/floor-459/攀枝花厅?x=0.2292756901230923&y=0.10823211130851983"}},"appKey":"meeting","bookType":13}}' \
""" 
    return cmd


async def time2timeLong(timeStr:str):
    # 转为时间数组
    timeArray = time.strptime(timeStr, "%Y-%m-%d %H:%M:%S")
    timeStamp = int(time.mktime(timeArray))
    return str(timeStamp)+"000"