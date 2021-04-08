from nonebot import on_command, on_startswith
import nonebot
from nonebot.rule import to_me
from nonebot.plugin import on_regex
from nonebot.adapters.cqhttp import Bot, Event, MessageSegment, Message, message
import aiohttp
import re
import os
# pixiv = on_command(cmd="pixiv")
pixiv = on_regex(pattern="^pixiv\ ")


# 识别参数 并且给state 赋值


@pixiv.handle()
async def pixiv_rev(bot: Bot, event: Event, state: dict):
    pid = str(event.message).strip()[6:].strip()
    names = await main(pid)
    if not names :
        await bot.send(event=event,message="没有这个pid的图片")
    else:
        msg = ""
        for name in names:
            msg+=f"[CQ:image,file=file:////root/nextcloud/pixiv/{name}]"
        await bot.send(event=event,message=Message(msg))

headers = {
    'referer': 'https://www.pixiv.net',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.163 Safari/537.36',
}

proxy = "http://127.0.0.1:1081"

async def fetch(session, url, name):
    print("发送请求：", url)
    async with session.get(url=url, headers=headers,proxy=proxy) as response:
    # async with session.get(url=url, headers=headers) as response:
        code = response.status
        if code ==200:
            content = await response.content.read()
            with open("/root/NextCloud/pixiv/"+name, mode='wb') as f:
                f.write(content)
            return True
        return False

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
        name = url[url.rfind("/")+1:]
        num= 1
        names = []
        if os.path.exists("/root/NextCloud/pixiv/"+name):
            hou = down_url[0][1]
            while os.path.exists("/root/NextCloud/pixiv/"+name) and num<=6:
                names.append(name)
                newstr = f"_p{num}.{hou}"
                num+=1
                name = re.sub("_p(\d+)\.(png|jpg|jepg)",newstr,name)
        else:
            hou = down_url[0][1]
            while ( await fetch(session=session,url=url,name=name) and num<=6):
                names.append(name)
                newstr= f"_p{num}.{hou}"
                num+=1
                url = re.sub("_p(\d+)\.(png|jpg|jepg)",newstr,url)
                name = url[url.rfind("/")+1:]
        return names

