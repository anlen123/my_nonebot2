from nonebot import on_command,on_startswith
from nonebot.plugin import on_regex
from selenium import webdriver
from nonebot.rule import to_me
from nonebot.adapters.cqhttp import Bot, Event, MessageSegment, Message
import time
from playwright.async_api import async_playwright
xr=on_regex(pattern="^xr\ ")
import os 
# 识别参数 并且给state 赋值
@xr.handle()
async def xr_rev(bot: Bot, event: Event, state: dict):
    msg=str(event.message).strip()[3:].strip()
    s = time.time()
    img=main(msg)
    # img=await main_async(msg)
    try:
        await bot.send(event=event,message=MessageSegment.image("file:///"+"/root/NextCloud/xr/"+img)+f"耗时:{time.time()-s}")
    except:
        await bot.send(event=event,message="错误")

async def main_async(url):
    async with async_playwright() as p:
        for browser_type in [p.chromium]:
            browser = await browser_type.launch()
            page = await browser.new_page()
            await page.goto(url)
            picture_time = time.strftime("%Y-%m-%d-%H_%M_%S", time.localtime(time.time()))
            path = f'{picture_time}.png'
            await page.screenshot(path=path)
            await browser.close()
            img_name = os.path.abspath(picture_time+".png")
            os.system(f"mv {img_name} /root/NextCloud/xr")
            return path

def main(url):
    # url = "http://liuhuaqiang.top"
    option = webdriver.ChromeOptions()
    option.add_argument("--headless")                               #设置无头模式
    option.add_argument('--no-sandbox')
    driver=webdriver.Chrome(options=option) 
    driver.get(url)
    # driver.maximize_window()
    width = driver.execute_script("return document.documentElement.scrollWidth")
    height = driver.execute_script("return document.documentElement.scrollHeight")
    driver.set_window_size(width, height)
    driver.implicitly_wait(10)
    picture_time = time.strftime("%Y-%m-%d-%H_%M_%S", time.localtime(time.time()))
    picture_url=driver.get_screenshot_as_file(f'{picture_time}.png')
    print("%s：截图成功！！！" % picture_url)
    driver.quit()
    import os 
    os.system(f"mv {picture_time}.png /root/NextCloud/xr")
    # os.system("./NextCloud/nextcloud_update.sh")
    return picture_time+".png"

