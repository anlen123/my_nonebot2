# -*- coding: utf-8 -*-
import aiohttp
from nonebot.plugin import on_command
from nonebot.adapters.cqhttp import Bot, Event, MessageSegment, Message

headers = {'Accept': '*/*', 'Accept-Encoding': 'gzip, deflate, br', 'Accept-Language': 'zh-CN,zh;q=0.9',
           'Connection': 'keep-alive',
           'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8', 'Host': 'tool.runoob.com',
           'Origin': 'https://c.runoob.com', 'Referer': 'https://c.runoob.com/compile/66', 'Sec-Fetch-Dest': 'empty',
           'Sec-Fetch-Mode': 'cors', 'Sec-Fetch-Site': 'same-site',
           'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/84.0.4147.105 Safari/537.36'}

language_map: dict = {"php": 3, "py": 0, "py3": 15, "java": 8, "c": 7, "cpp": 7, "rb": 1, "cs": 10, "scala": 5,
                      "erl": 12, "pl": 14, "sh": 11, "rs": 9, "swift": 16, "go": 6, "node.js": 4, "lua": 17, "pas": 18,
                      "kt": 19, "ts": 1001, "vb": 84, "R": 80}


async def execute(code: str, language: str):
    if language == "js":
        language = "node.js"
    async with aiohttp.ClientSession() as session:
        data = {"code": code, "token": "4381fe197827ec87cbac9552f14ec62a", "stdin": "",
                "language": language_map[language],
                "fileext": language}
        async with session.post("https://tool.runoob.com/compile2.php", data=data, headers=headers) as resp:
            data = await resp.json()
            print(data)
        return data["output"], data["errors"]


super_ = on_command("bash")


@super_.handle()
async def handle_super_(bot: Bot, event: Event, state: dict):
    language, code = str(event.message).split("\n", 1)
    language: str = language.strip()
    if code:
        # print(code)
        stdout, stderr = await execute(code, language)
        if temp := stdout.strip():
            await bot.send(event, temp)
        if temp := stderr.strip():
            await bot.send(event, temp)
