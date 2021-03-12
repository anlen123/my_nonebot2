from nonebot import require
from nonebot import on_command, on_startswith
from nonebot.plugin import on_regex
from nonebot.rule import to_me
from nonebot.adapters.cqhttp import Bot, Event, MessageSegment, Message, message
import nonebot
from datetime import datetime
import aiohttp
# 识别参数 并且给state 赋值

xinwen = on_regex(pattern="^xw$")


@xinwen.handle()
async def xinwen_rev(bot: Bot, event: Event, state: dict):
    date = (await main())['data']
    msg = ""
    for index,x in enumerate(date[:10]):
        msg += (str(index+1)+":  "+x['name']+"\n")
    await xinwen.finish(message=msg)


scheduler = require("nonebot_plugin_apscheduler").scheduler


@scheduler.scheduled_job("cron",  id="xinwen", hour="0", minute="0")
async def xinwen_scheduler():
    date = (await main())['data']
    msg = ""
    for index,x in enumerate(date[:10]):
        if "习近平" in x :
            x = x.replace("习近平","习大大")
        msg += (str(index+1)+":  "+x['name']+"\n")
    await nonebot.get_bots()['1928994748'].send_msg(message=msg)


headers = {
    'authority': 'api.hmister.cn',
    'cache-control': 'max-age=0',
    'upgrade-insecure-requests': '1',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/86.0.4240.198 Safari/537.36',
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
    'sec-fetch-site': 'same-site',
    'sec-fetch-mode': 'navigate',
    'sec-fetch-user': '?1',
    'sec-fetch-dest': 'document',
    'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
}


async def getContent(session):
    async with session.get(url="https://api.hmister.cn/weibo/", headers=headers) as response:
        return await response.json(content_type='text/html', encoding='utf-8')


async def main():
    async with aiohttp.ClientSession() as session:
        return (await getContent(session))
