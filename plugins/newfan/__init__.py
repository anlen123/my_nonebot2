import nonebot
from pytz import timezone
from nonebot import require
from nonebot.plugin import on_regex
from nonebot.adapters.onebot.v11 import Bot, Event, Message, MessageSegment
import httpx,parsel
from datetime import datetime
from typing import List

global_config = nonebot.get_driver().config
config = global_config.dict()

export = nonebot.require("nonebot_plugin_navicat")
clien = export.redis_client # redis的
tz_shanghai = timezone('Asia/Shanghai')
now = datetime.now(tz=tz_shanghai)

newfan = on_regex(pattern="^新番$")


@newfan.handle()
async def xxx_Method(bot: Bot, event: Event):
    if event.get_user_id()!="1761512493":
        return 
    fan_list = await get_now_week_fan_list()
    day2week = {0: "一", 1: "二", 2: "三", 3: "四", 4: "五", 5: "六", 6: "日"}
    msgs = "今天星期" + day2week.get(int(now.weekday()),"日") +"\n"
    for fan in fan_list:
        msgs+=(MessageSegment.image(fan.img)+"\n"+fan.title+"\n时间:"+fan.time+"\n")
    await bot.send(event=event,message=msgs)


class Fan:
    def __init__(self, img, title, week, time) -> None:
        self.img = img
        self.title = title
        self.week = week
        self.time = time

    def __str__(self) -> str:
        return f"img:{self.img}, title:{self.title},week:{self.week},time:{self.time}"




async def get_fan_list():
    day2week = {0: "一", 1: "二", 2: "三", 3: "四", 4: "五", 5: "六", 6: "日"}
    tz_shanghai = timezone('Asia/Shanghai')
    now = datetime.now(tz=tz_shanghai)
    year = str(now.year)
    month = now.month
    if 1 <= month < 4:
        month = 1
    elif month < 10:
        month = 4
    else:
        month = 10
    month = str(month).zfill(2)

    async with httpx.AsyncClient() as client:
        headers = {
            'authority': 'acgsecrets.hk',
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'cache-control': 'max-age=0',
            'sec-ch-ua': '" Not A;Brand";v="99", "Chromium";v="100", "Google Chrome";v="100"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"macOS"',
            'sec-fetch-dest': 'document',
            'sec-fetch-mode': 'navigate',
            'sec-fetch-site': 'same-origin',
            'sec-fetch-user': '?1',
            'upgrade-insecure-requests': '1',
            'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36'
        }
        key = f'{now.year}_{now.month}_{now.day}'
        txt = clien.get(key)
        if not txt:
            resp = await client.get(f'https://acgsecrets.hk/bangumi/{year + month}/', headers=headers)
            txt = resp.text
            clien.set(key,txt)
        else:
            txt = bytes(txt).decode()
        par_text = parsel.Selector(txt)
        msgs = par_text.xpath('//*[@id="acgs-anime-icons"]/div')
        fan_list = []
        for msg in msgs:
            img = msg.xpath('a/div[1]/img/@src').get()
            title = msg.xpath('a/div[3]/div[2]/text()').get()
            week = msg.xpath('a/div[7]/div[1]/text()').get()
            time = msg.xpath('a/div[7]/div[2]/text()').get()
            if img and time and week and time and week in day2week.values():
                fan_list.append(Fan(img, title, week, time))
        return fan_list


async def get_now_week_fan_list():
    fan_list = await get_fan_list()
    day2week = {0: "一", 1: "二", 2: "三", 3: "四", 4: "五", 5: "六", 6: "日"}
    week_now = [fan for fan in fan_list if day2week.get(now.weekday(), "日") == fan.week]
    return sorted(week_now, key=lambda a: int(str(a.time).replace(":", "")))


scheduler = require("nonebot_plugin_apscheduler").scheduler

@scheduler.scheduled_job("cron", id="xinfan", minute="*/1")
async def xinfan():
    hour = now.hour
    minute = now.minute
    fan_list = await get_fan_list()
    day2week = {0: "一", 1: "二", 2: "三", 3: "四", 4: "五", 5: "六", 6: "日"}
    fan_list = [fan for fan in fan_list if day2week.get(now.weekday(), "日") == fan.week or day2week.get(now.weekday() - 1 , "日") == fan.week]
    # for fan in fan_list:
        # print(fan)
    msg = ""
    for fan in fan_list:
        h = int(str(fan.time).split(":")[0])
        m = int(str(fan.time).split(":")[1])
        if h <= 24:
            h = h - 25
        if int(h) == int(hour) and int(minute) == int(m):
            msg+=(MessageSegment.image(fan.img)+"\n"+fan.title+"\n时间:"+fan.time+"\n")
    if msg:
        bot = nonebot.get_bots()
        if bot :
            bot = bot['1928994748']
            await bot.send_msg(message="有新番更新了:" + msg,user_id="1761512493")
