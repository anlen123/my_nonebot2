from pathlib import Path
import aiohttp
import nonebot
from nonebot.plugin import on_regex
from nonebot.adapters.cqhttp import Bot, Event
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
async def jd_test_rev(bot: Bot, event: Event, state: dict):
    msg = await main()
    if msg:
        msg = json.loads(msg)['base']
        user_name = msg['nickname']
        jdd = msg['jdNum']
        level = msg['levelName']
        await bot.send(event=event,message=f"用户:{user_name}\n等级:{level}\n拥有京东豆:{jdd}")
