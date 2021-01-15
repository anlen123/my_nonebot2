import aiohttp
from nonebot import on_command
from nonebot.adapters.cqhttp import Bot, Event


async def get_weather(cityName):
    url = f"https://tianqiapi.com/api?version=v1&appid=21492898&appsecret=V0eHZaI2&city={cityName}"

    headers = {
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.163 Safari/537.36',
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url=url, headers=headers) as resp:
            msg = await resp.json()
            return msg['data'][0]


sx = on_command(cmd="天气", aliases={"缩写"})


# 识别参数 并且给state 赋值

@sx.handle()
async def sx_rev(bot: Bot, event: Event, state: dict):
    msg = str(event.message).strip()
    date = await get_weather(msg)
    try:
        await bot.send(event=event, message=date)
    except :
        await bot.send(event=event, message="没有找到城市天气")

