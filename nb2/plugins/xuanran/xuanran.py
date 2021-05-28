import os
from nonebot import on_command, on_startswith
from nonebot.plugin import on_regex
from selenium import webdriver
from nonebot.rule import to_me
from nonebot.adapters.cqhttp import Bot, Event, MessageSegment, Message
import time
xr = on_regex(pattern="^xr\ ")
# 识别参数 并且给state 赋值


@xr.handle()
async def xr_rev(bot: Bot, event: Event, state: dict):
    msg = str(event.message).strip()[3:].strip()
    s = time.time()
    img = main(msg)
    try:
        await bot.send(event=event, message=MessageSegment.image("file:///"+"/root/QQbotFiles/xr/"+img)+f"耗时:{time.time()-s}")
    except:
        await bot.send(event=event, message="错误")




def main(url):
    # url = "http://liuhuaqiang.top"
    option = webdriver.ChromeOptions()
    option.add_argument("--headless")  # 设置无头模式
    option.add_argument('--no-sandbox')
    driver = webdriver.Chrome(options=option)
    driver.get(url)
    # driver.maximize_window()
    width = driver.execute_script(
        "return document.documentElement.scrollWidth")
    height = driver.execute_script(
        "return document.documentElement.scrollHeight")
    driver.set_window_size(width, height)
    driver.implicitly_wait(10)
    picture_time = time.strftime(
        "%Y-%m-%d-%H_%M_%S", time.localtime(time.time()))
    picture_url = driver.get_screenshot_as_file(f'{picture_time}.png')
    print("%s：截图成功！！！" % picture_url)
    driver.quit()
    os.system(f"mv {picture_time}.png /root/QQbotFiles/xr")
    # os.system("./QQbotFiles/QQbotFiles_update.sh")
    return picture_time+".png"
