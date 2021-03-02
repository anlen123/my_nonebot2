from nonebot import on_command, on_startswith
from nonebot.rule import to_me
from nonebot.adapters.cqhttp import Bot, Event, MessageSegment, Message
import aiohttp
import re
import os
pixiv = on_command(cmd="pixiv")


# 识别参数 并且给state 赋值


@pixiv.handle()
async def pixiv_rev(bot: Bot, event: Event, state: dict):
    pid = str(event.message).strip()
    print(pid)
    name = await main(pid)
    print(name)
    if not name :
        await bot.send(event=event,message="没有这个pid的图片")
    else:
        await bot.send(event=event,message=MessageSegment.image("file:///"+"/root/NextCloud/pixiv/"+name))


headers = {
    'referer': 'https://www.pixiv.net',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.163 Safari/537.36',
}

proxy = "http://127.0.0.1:1081"

async def fetch(session, url, name):
    print("发送请求：", url)
    async with session.get(url=url, headers=headers,proxy=proxy) as response:
    # async with session.get(url=url, headers=headers) as response:
        content = await response.content.read()
        with open("/root/NextCloud/pixiv/"+name, mode='wb') as f:
            f.write(content)

async def main(PID):
    url = f"https://www.pixiv.net/artworks/{PID}"
    async with aiohttp.ClientSession() as session:
        x = await session.get(url=url, headers=headers,proxy=proxy)
        # x = await session.get(url=url, headers=headers)
        content = await x.content.read()
        down_url = re.findall('"original":"(.*?)\.(png|jpg|jepg)"', content.decode())
        if not down_url:
            return ""
        url = '.'.join(down_url[0])
        print(url)
        name = url[url.rfind("/")+1:]
        if not  os.path.exists("/root/NextCloud/pixiv/"+name):
            await fetch(session, url, name)
            print("不",end="")
        print("存在")
        return name
