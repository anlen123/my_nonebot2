import aiohttp
import nonebot
from nonebot.plugin import on_regex
from nonebot.adapters.onebot.v11 import Bot, Event
from nonebot import require
from datetime import datetime
import json

scheduler = require("nonebot_plugin_apscheduler").scheduler

export = nonebot.require("nonebot_plugin_navicat")
redis_client = export.redis_client  # redis的

global_config = nonebot.get_driver().config
config = global_config.dict()
jd_cookie = config.get('jd_cookie', "")


async def fetch(session, url, headers):
    async with session.get(url, headers=headers) as response:
        return await response.text()


async def main(url, headers):
    async with aiohttp.ClientSession() as session:
        html = await fetch(session, url, headers)
        return html


jd_test = on_regex(pattern="^jdd$")


@jd_test.handle()
async def jd_test_rev(bot: Bot, event: Event):
    url = "https://wq.jd.com/user/info/QueryJDUserInfo?sceneval=2"
    headers = {
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/86.0.4240.198 Safari/537.36',
        'referer': 'https://wqs.jd.com/my/jingdou/my.shtml?sceneval=2&jxsid=16399181398606646767&ptag=7155.1.17',
        'cookie': f'{jd_cookie}'
    }
    msg = await main(url, headers)
    if msg:
        msg = json.loads(msg)['base']
        user_name = msg['nickname']
        jdd = msg['jdNum']
        level = msg['levelName']
        await bot.send(event=event, message=f"用户:{user_name}\n等级:{level}\n拥有京东豆:{jdd}")


@scheduler.scheduled_job("cron", id="jdd_add", hour="*/1", minute=None)
async def jdd_add():
    url = "https://wq.jd.com/user/info/QueryJDUserInfo?sceneval=2"
    headers = {
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/86.0.4240.198 Safari/537.36',
        'referer': 'https://wqs.jd.com/my/jingdou/my.shtml?sceneval=2&jxsid=16399181398606646767&ptag=7155.1.17',
        'cookie': f'{jd_cookie}'
    }
    now = datetime.now().hour
    if 1 <= now <= 10:
        pass
    jdd_count = redis_client.get("jdd_count")
    if not jdd_count:
        jdd_count = 0
        redis_client.set("jdd_count", 0)

    msg = await main(url, headers)
    if msg:
        msg = json.loads(msg)['base']
        jdd = msg['jdNum']

    if jdd and jdd_count and int(jdd_count) - int(jdd) != 0:
        redis_client.set("jdd_count", jdd)
        bot = nonebot.get_bots()
        if bot:
            bot = bot['1928994748']
            await bot.send_msg(message=f"京东豆增加{int(jdd) - int(jdd_count)}个,现在你的京东豆数量是: {jdd}个", user_id="1761512493")


@scheduler.scheduled_job("cron", id="jdd_add_wwj", hour="*/1", minute=None)
async def jdd_add_wwj():
    url = "https://wq.jd.com/user/info/QueryJDUserInfo?sceneval=2"
    headers = {
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/86.0.4240.198 Safari/537.36',
        'referer': 'https://wqs.jd.com/my/jingdou/my.shtml?sceneval=2&jxsid=16399181398606646767&ptag=7155.1.17',
        'cookie': 'pt_key=AAJiIGzEADDyPfp6LHuEEfM7OM_CmY1iKi6mq5YQra-3w-KO-I2_VjIf48ec19VBfaa0-_wjOJs;pt_pin=1255542159_m;'
    }
    now = datetime.now().hour
    if 1 <= now <= 10:
        pass
    jdd_count_wwj = redis_client.get("jdd_count_wwj")
    if not jdd_count_wwj:
        jdd_count_wwj = 0
        redis_client.set("jdd_count_wwj", 0)

    msg = await main(url, headers)
    if msg:
        msg = json.loads(msg)['base']
        jdd = msg['jdNum']

    if jdd and jdd_count_wwj and int(jdd_count_wwj) - int(jdd) != 0:
        redis_client.set("jdd_count_wwj", jdd)
        bot = nonebot.get_bots()
        if bot:
            bot = bot['1928994748']
            await bot.send_msg(message=f"京东豆增加{int(jdd) - int(jdd_count_wwj)}个,现在你的京东豆数量是: {jdd}个",
                               user_id="1255542159")
