from pathlib import Path
import aiohttp
import nonebot
from nonebot.plugin import on_regex
from nonebot.adapters.onebot.v11 import Bot, Event
from nonebot.params import T_State,State
from nonebot import require
from datetime import datetime

scheduler = require("nonebot_plugin_apscheduler").scheduler

export = nonebot.require("nonebot_plugin_navicat")
clien = export.redis_client # redis的

import json
_sub_plugins = set()
_sub_plugins |= nonebot.load_plugins(
    str((Path(__file__).parent / "plugins").
    resolve()))


global_config = nonebot.get_driver().config
config = global_config.dict()
jd_cookie=config['jd_cookie'] if 'jd_cookie' in config else ""

url = "https://wq.jd.com/user/info/QueryJDUserInfo?sceneval=2"

headers = {
  'user-agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/86.0.4240.198 Safari/537.36',
  'referer': 'https://wqs.jd.com/my/jingdou/my.shtml?sceneval=2&jxsid=16399181398606646767&ptag=7155.1.17',
  'cookie': f'{jd_cookie}'
}

async def fetch(session, url):
    async with session.get(url,headers=headers) as response:
        return await response.text()


async def main():
    async with aiohttp.ClientSession() as session:
        html = await fetch(session, url)
        return html




jd_test = on_regex(pattern="^jdd$")

@jd_test.handle()
async def jd_test_rev(bot: Bot, event: Event, state: T_State=State()):
    msg = await main()
    if msg:
        msg = json.loads(msg)['base']
        user_name = msg['nickname']
        jdd = msg['jdNum']
        level = msg['levelName']
        await bot.send(event=event,message=f"用户:{user_name}\n等级:{level}\n拥有京东豆:{jdd}")

@scheduler.scheduled_job("cron",  id="jdd_add",hour="*/1",minute=None)
async def jdd_add():
    now = datetime.now().hour
    if now >=1 and now <=10 :
        pass
    jdd_count = clien.get("jdd_count")
    if not jdd_count:
        jdd_count = 0
        clien.set("jdd_count",0)

    msg = await main()
    if msg:
        msg = json.loads(msg)['base']
        jdd = msg['jdNum']

    if jdd and jdd_count and int(jdd_count)-int(jdd)!=0:
        clien.set("jdd_count",jdd)
        bot = nonebot.get_bots()
        if bot :
            bot = bot['1928994748']
            await bot.send_msg(message=f"京东豆增加{int(jdd)-int(jdd_count)}个,现在你的京东豆数量是: {jdd}个",user_id="1761512493")
