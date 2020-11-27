import aiohttp 
from nonebot import on_command,on_startswith
from nonebot.rule import to_me
from nonebot.adapters.cqhttp import Bot, Event, MessageSegment, Message

sx = on_command(cmd="sx")


# 识别参数 并且给state 赋值


@sx.handle()
async def sx_rev(bot: Bot, event: Event, state: dict):
    msg = str(event.message).strip()
    date = await main(msg)
    date=eval(date)
    name= date[0]['name']
    print(name)
    content = date[0]['trans']
    print(content)
    await bot.send(event=event,message=name+"\n"+str(content))



async def main(word):
    url = "https://lab.magiconch.com/api/nbnhhsh/guess"

    headers = {
        'origin': 'https://lab.magiconch.com',
        'referer': 'https://lab.magiconch.com/nbnhhsh/',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.163 Safari/537.36',
    }
    data = {
        "text": f"{word}"
    }
    msg = "没有查询到信息"
    async with aiohttp.ClientSession() as session:
        date = await session.post(url=url, headers=headers, data=data)
        msg = (await date.content.read()).decode()
    return msg
