from pathlib import Path

import nonebot
from .config import Config
from nonebot.plugin import on_regex
from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent
import requests, os, uuid, aiohttp, json

global_config = nonebot.get_driver().config
config = global_config.dict()

# 识别参数 并且给state 赋值
imgRoot = config.get('imgroot', "")
# token = config.get('tg_token', "")
token = "5311560561:AAFwYgMfW5YGcu6aM_-_c_DP3wIwZ4ez8xM"

tgface = on_regex(pattern="^https://t.me/addstickers/")

if not os.path.exists("tgface"):
    os.makedirs("tgface")

export = nonebot.require("nonebot_plugin_navicat")
clien = export.redis_client  # redis的


@tgface.handle()
async def tgface_rev(bot: Bot, event: Event):
    if isinstance(event, GroupMessageEvent):
        tg_name = event.get_plaintext()[25:]
        lock = clien.get(tg_name)
        if not lock:
            clien.set(tg_name,'true',px=600000)
            # for _, dirs, _ in os.walk("tgface"):
                # for name in dirs:
                    # os.system(f"rm -rf tgface/{name}")
            if os.path.exists(f"tgface/{tg_name}.zip"):
                await bot.send(event, message=f"http://216.240.134.27:8000/{tg_name}.zip")
                clien.delete(tg_name)
                return
            await main(tg_name)
            clien.delete(tg_name)
            await bot.send(event, message=f"http://216.240.134.27:8000/{tg_name}.zip")
        else:
            await bot.send(event, message="有任务在执行，请稍后再试！！")
    else:
        await bot.send(event, message="只支持群组")


async def get_file_id(name: str):
    url = f"https://api.telegram.org/bot{token}/getStickerSet?name={name}"
    async with aiohttp.ClientSession() as session:
        x = await session.get(url=url)
        files = json.loads(await x.content.read())
        return [_['file_id'] for _ in files['result']['stickers']]


async def get_file_path(file_id: str):
    url = f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}"
    async with aiohttp.ClientSession() as session:
        x = await session.get(url=url)
        content = await x.content.read()
        return json.loads(content)['result']['file_path']


async def down_load(uid: str, path: str):
    url = f"https://api.telegram.org/file/bot{token}/{path}"
    print(url)
    async with aiohttp.ClientSession() as session:
        x = await session.get(url=url)
        content = await x.content.read()
        if not os.path.exists(f"tgface/{uid}"):
            os.makedirs(f"tgface/{uid}")
        file_name = path.replace('/', '_')
        with open(f"tgface/{uid}/{file_name}", 'wb') as file:
            file.write(content)
        print("下载完成")
        return file_name


def get_uuid() -> str:
    return str(uuid.uuid1()).replace("-", "")


def pan_file_type(file_name: str):  # 0是视频转gif, 1是tgs, 2 是图片
    if file_name.endswith("webm"):
        return 0
    if file_name.endswith("tgs"):
        return 1
    return 2


async def main(tg_name: str):
    if os.path.exists(f"tgface/{tg_name}.zip"):
        return
    files = await get_file_id(tg_name)
    uid = get_uuid()
    file_names = []
    for file in files:
        try:
            path = await get_file_path(file)
            file_name = await down_load(uid, path)
            file_names.append(file_name)
        except Exception:
            print("处理错误，处理下一张")
    for name in file_names:
        if pan_file_type(name) == 0:
            gif_name = name.replace("webm", "gif")
            os.system(
                f'ffmpeg -i tgface/{uid}/{name} -r 5 -movflags faststart -vf "setpts=.66667*PTS,scale=480:-1" -y -hide_banner tgface/{uid}/{gif_name}')
            os.system(f"rm -rf tgface/{uid}/{name}")
        elif pan_file_type(name) == 1:
            gif_name = name.replace("tgs", "gif")
            print(f"lottie_convert.py tgface/{uid}/{name} tgface/{uid}/{gif_name}")
            os.system(f"lottie_convert.py tgface/{uid}/{name} tgface/{uid}/{gif_name}")
            os.system(f"rm -rf tgface/{uid}/{name}")
    # print("压缩中...")
    # os.system(f"tar czvf {tg_name}.tar.gz {uid}")
    # print("删除中...")
    # os.system(f"rm -rf {uid}")
    print("压缩中...")
    os.system(f"zip -q -r tgface/{tg_name}.zip tgface/{uid}")
    print("删除中...")
    os.system(f"rm -rf tgface/{uid}")
