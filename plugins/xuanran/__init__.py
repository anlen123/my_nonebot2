#!/Users/liuhq/miniconda3/bin/python
# -*- coding: utf-8 -*-

import asyncio,pendulum
from playwright.async_api import async_playwright
from pathlib import Path

import nonebot
from nonebot.plugin import on_regex
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment, Message
from nonebot.params import T_State
import time
import random
import asyncio
import os

export = nonebot.require("nonebot_plugin_navicat")
rc = export.redis_client  # redis的

global_config = nonebot.get_driver().config
imgRoot = global_config.dict().get("imgroot", os.environ["HOME"] + "/")
xuanran_proxy = global_config.dict().get("xuanran_proxy", "")
if xuanran_proxy:
    xuanran_proxy += ";"

xr = on_regex(pattern="^xr\ ")


async def screenshots(msg: str):
    print("渲染网址: " + msg)
    img = await main(msg)
    print("图片地址: " + img)
    endStr = img.endswith(".png")
    print("结尾是png " if endStr else "结尾不是png")
    return (img, endStr)


@xr.handle()
async def xr_rev(bot: Bot, event: Event):
    msg = str(event.message).strip()[3:].strip()
    s = time.time()
    if not (msg.startswith("http://") or msg.startswith("https://")):
        msg = f"http://{msg}"
    msg = msg.replace("。", ".")
    msg = msg.replace(",", ".")
    msg = msg.replace("，", ".")
    img, endStr = await screenshots(msg)
    if img and endStr:
        await bot.send(event=event, message=MessageSegment.image(
            "file:///" + img) + f"耗时:{time.time() - s}")
    else:
        await bot.send(event=event, message="错误")

why = on_regex(pattern="是什么$")


@why.handle()
async def why_rev(bot: Bot, event: Event):
    msg = event.get_plaintext()
    msg = "https://zh.wikipedia.org/wiki/" + msg[:-3]
    s = time.time()
    img, endStr = await screenshots(msg)
    if img and endStr:
        await bot.send(event=event, message=MessageSegment.image(
            "file:///"  + img) + f"耗时:{time.time() - s}")
    else:
        await bot.send(event=event, message="错误")

async def main(url:str):
    async with async_playwright() as p:
        for browser_type in [p.chromium]:
            browser = await browser_type.launch()
            page = await browser.new_page()
            await page.goto(url)
            path = f'{imgRoot}.png'
            await page.screenshot(path=path)
            await browser.close()
            return path

async def main(url:str):
    async with async_playwright() as p:
        for browser_type in [p.chromium]:
            browser = await browser_type.launch()
            page = await browser.new_page()
            await page.goto(url)
            picture_time = pendulum.now().format("Y-MM-DD-HH_mm_ss")
            path = f'{imgRoot}QQbotFiles/xr/{picture_time}.png'
            await page.screenshot(path=path,full_page=True)
            await browser.close()
            return path


pokemon = on_regex(pattern="^随机宝可梦$")

@pokemon.handle()
async def pokemon_rev(bot: Bot, event: Event):
    s = time.time()
    pokemonId = random.randint(1,898)
    msg = f"https://www.pokemon.cn/play/pokedex/{str(pokemonId).zfill(3)}"
    img, endStr = await screenshots(msg)
    if img and endStr:
        await bot.send(event=event, message=MessageSegment.image(
            "file:///" + img) + f"耗时:{time.time() - s}")
    else:
        await bot.send(event=event, message="错误")
