#!/Users/liuhq/miniconda3/bin/python
# -*- coding: utf-8 -*-

import asyncio
from playwright.async_api import async_playwright
from pathlib import Path

import nonebot
from nonebot.plugin import on_regex
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment, Message
from nonebot.params import T_State
import time
import random
import os
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

global_config = nonebot.get_driver().config
config = global_config.dict()

xr = on_regex(pattern="^xr\ ")

imgRoot = config.get('imgroot') if config.get(
    'imgroot') else f"{os.environ['HOME']}/"

@xr.handle()
async def xr_rev(bot: Bot, event: Event):
    msg = str(event.message).strip()[3:].strip()
    if not (msg.startswith("http://") or msg.startswith("https://")):
        msg = f"http://{msg}"
    msg = msg.replace("。", ".")
    msg = msg.replace(",", ".")
    msg = msg.replace("，", ".")
    pngName = f"{imgRoot}\\QQbotFiles\\xr\\{random.randint(1, 99999999999)}.png"
    screenshot(msg, pngName)
    os.system(f"echo 123 >> {pngName}")
    await bot.send(event=event, message=MessageSegment.image(pngName))
    os.remove(pngName)


def screenshot(sss: str, pic_name):
    chrome_options = Options()
    chrome_options.add_argument('headless')
    driver = webdriver.Chrome(options=chrome_options)
    try:
        driver.get(sss)
        time.sleep(1)
        width = driver.execute_script("return document.documentElement.scrollWidth")
        height = driver.execute_script("return document.documentElement.scrollHeight")
        print(width, height)
        driver.set_window_size(width, height)
        time.sleep(1)
        print(pic_name)
        driver.save_screenshot(pic_name)
    except Exception as e:
        print(e)
    finally:
        driver.close()
        print("截图完毕")
