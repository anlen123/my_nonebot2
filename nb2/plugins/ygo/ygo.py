from nonebot import require
from nonebot import on_command, on_startswith
from nonebot.plugin import on_regex
from nonebot.rule import to_me
from nonebot.adapters.cqhttp import Bot, Event, MessageSegment, Message, message
import nonebot
from datetime import datetime
import aiohttp
import re
import os 
import random
# 识别参数 并且给state 赋值

ygo = on_regex(pattern="^ygo\ ")


headers = {
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/86.0.4240.198 Safari/537.36',
    'referer': 'https://ygocdb.com'
}

@ygo.handle()
async def ygo_rev(bot: Bot, event: Event, state: dict):
    # print(event.get_message())
    msg = str(event.get_message())[4:]
    url = f"https://ygocdb.com/?search={msg}"
    # print(url)
    async with aiohttp.ClientSession() as session: 
        async with session.get(url=url, headers=headers) as response:   
            txt =await response.text()
            list_pic = re.findall('\<a href\=\"(.*?)\" target\=\"_blank\"\>', txt)
            list_pic = [x for x in list_pic if str(x).endswith(".jpg")]

    pic_all = os.listdir("/root/QQbotFiles/ygo")

    send_msg=""
    
    if list_pic:
        list_pic = list_pic[:1]
        for x in list_pic:
            if x not in pic_all:
                await down_img(x)
            name = x[x.rfind("/")+1:]
            send_msg+=f'[CQ:image,file=file:////root/QQbotFiles/ygo/{name}]'

    if send_msg!="":
        await bot.send(event=event,message=Message(send_msg))
    else:
        await bot.send(event=event,message="未查询到此类卡片")

async def down_img(url):
    async with aiohttp.ClientSession() as session: 
        async with session.get(url=url, headers=headers) as response:
            content = await response.content.read()   
            with open(f"/root/QQbotFiles/ygo/{url[url.rfind('/')+1:]}",mode= "wb") as f:
                f.write(content)



suiji = on_regex(pattern="^随机一卡$")

@suiji.handle()
async def ygo_rev(bot: Bot, event: Event, state: dict):
    pic_all = os.listdir("/root/QQbotFiles/ygo")
    await bot.send(event=event,message=MessageSegment.image(file=f"file:////root/QQbotFiles/ygo/{random.choice(pic_all)}"))
