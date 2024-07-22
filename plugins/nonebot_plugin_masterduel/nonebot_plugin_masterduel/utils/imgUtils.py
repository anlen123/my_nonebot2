from PIL import Image
import requests
from io import BytesIO
import base64
import os, random, nonebot, time
from collections import Counter
from . import rarityUtils

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

import asyncio

global_config = nonebot.get_driver().config
config = global_config.dict()

nonebot_plugin_masterduel_img_dir = config.get('nonebot_plugin_masterduel_img_dir')
nonebot_plugin_masterduel_root_dir = config.get('nonebot_plugin_masterduel_root_dir')


def pin_quality(card_id: int, quality: str):
    # 加载URL图片
    url_image_url = f'https://cdn.233.momobako.com/ygopro/pics/{card_id}.jpg'

    # 加载URL图片
    response = requests.get(url_image_url)
    url_image = Image.open(BytesIO(response.content))

    # 加载本地图片
    if not quality:
        return url_image_url

    local_image_path = f'{nonebot_plugin_masterduel_img_dir}\\{quality}'
    local_image = Image.open(local_image_path).convert('RGBA')
    local_image.thumbnail((local_image.width * 0.5, local_image.height * 0.5))

    # 计算拼接后的图像大小
    result_width = url_image.width
    result_height = url_image.height + local_image.height

    # 创建空白图像作为拼接结果
    result_image = Image.new('RGBA', (result_width, result_height))

    # 将URL图片粘贴到拼接结果的底部
    result_image.paste(url_image, (0, local_image.height))

    # 将本地图片粘贴到拼接结果的顶部
    result_image.paste(local_image, (url_image.width - local_image.width, 0))

    # 粘贴小蓝
    xiaoLan_path = f'{nonebot_plugin_masterduel_img_dir}\\xiaolan'
    xiaoLan_image = Image.open(xiaoLan_path).convert('RGBA')
    xiaoLan_image.thumbnail((xiaoLan_image.width * 0.36, xiaoLan_image.height * 0.36))
    result_image.paste(xiaoLan_image, (0, 0), xiaoLan_image)

    # 将拼接后的图像转换为Base64格式
    buffered = BytesIO()
    result_image.save(buffered, format='PNG')

    image_base64 = base64.b64encode(buffered.getvalue()).decode()

    return "base64://" + image_base64


def down_img(path: str, url: str, name: str):
    """
    从给定的URL下载图片，并保存到指定的路径。

    参数:
    path (str): 图片保存的路径（包括文件名和扩展名）。
    url (str): 图片的URL。

    返回:
    None
    """
    try:
        # 发送GET请求获取图片数据
        response = requests.get(url)

        # 确保请求成功
        response.raise_for_status()

        if not os.path.exists(path):
            os.makedirs(path)

        full_path = os.path.join(path, name)

        # 将图片数据写入文件
        with open(full_path, 'wb') as f:
            f.write(response.content)

        # 如果需要，可以在这里添加使用PIL库对图片进行进一步处理的代码
        # 例如：image = Image.open(BytesIO(response.content))

        print(f"图片已成功保存到 {path}")

    except requests.RequestException as e:
        # 捕获requests库抛出的异常
        print(f"下载图片时发生错误: {e}")
    except Exception as e:
        # 捕获其他异常
        print(f"发生未知错误: {e}")


def screenshot(sss: str, pic_name):
    chrome_options = Options()
    chrome_options.add_argument('headless')
    driver = webdriver.Chrome(options=chrome_options)
    try:
        name = str(random.randint(1, 999999999)) + '.html'
        print(name)
        with open(name, 'w', encoding='utf-8') as f:
            f.write(sss)
        file_url = f"file:///{os.getcwd()}\\{name}"
        driver.get(file_url)
        time.sleep(1)
        width = driver.execute_script("return document.documentElement.scrollWidth")
        height = driver.execute_script("return document.documentElement.scrollHeight")
        print(width, height)
        driver.set_window_size(width, height)
        time.sleep(1)
        driver.save_screenshot(pic_name)
        url = f"{os.getcwd()}\\{name}"
        os.remove(url)
    except Exception as e:
        print(e)
    finally:
        driver.close()
        print("截图完毕")


def screenshot_by_url(sss: str, pic_name):
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
        driver.save_screenshot(pic_name)
    except Exception as e:
        print(e)
    finally:
        driver.close()
        print("截图完毕")


def get_all_temp(cardListMain: None, cardListEx: None):
    cardId2count = Counter(cardListMain)
    tempList = []
    for key, value in cardId2count.items():
        rarity = rarityUtils.get_rarity(int(key))
        tempList.append(get_singe_temp(int(key), value, rarity))
    tempMainStr = '\n'.join(tempList)
    if cardListEx:
        cardIdEx2count = Counter(cardListEx)
        tempExList = []
        for key, value in cardIdEx2count.items():
            rarity = rarityUtils.get_rarity(int(key))
            tempExList.append(get_singe_temp(int(key), value, rarity))
        tempExStr = '\n'.join(tempExList)
    else:
        tempExStr = ""
    sss = f"""
    <link rel="stylesheet" id="deck-card-css"
        href="https://duelmeta.com/wp-content/plugins/ygo-plugin-master/assets/css/deck-card.css?ver=6.4.5" type="text/css"
        media="all">
        <style>
            .area {{
                border: 20px solid green;
            }}
        </style>
    <body class="post-template-default single single-post postid-77257 single-format-standard social-top post-style-1">
        <div id="page" class="site up action">
            <div id="content" class="site-content">
                <div class="b2-single-content wrapper">
                    <div id="primary-home" class="content-area">
                        <article class="single-article b2-radius box">
                            <div class="entry-content">
                                <div class="su-row">
                                    <div class="su-column su-column-size-1-2">
                                        <div class="su-column-inner su-u-clearfix su-u-trim">
                                            <div class="deck-wrapper deck-post">
                                                <div class="area">
                                                    <br>
                                                    <br>
                                                    <div class="area-item">
                                                        <ul class="card-wrapper">
                                                        {tempMainStr}
                                                        </ul>
                                                    </div>
                                                   <br>
                                                   <br>
                                                    <div class="area-item">
                                                        <ul class="card-wrapper">
                                                        {tempExStr}
                                                        </ul>
                                                    </div>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </article>
                    </div>
                </div>
            </div>
        </div>
    </body>
    """
    return sss


def get_singe_temp(cardId: int, count: int, rarity: str):
    limit_temp = f"""
    <div class="card-limit">
        <img decoding="async"
            src="{nonebot_plugin_masterduel_img_dir}\\deck_html_img\\{count}x.png"
            class="lazy entered loaded"
            data-ll-status="loaded">
    </div>
    """
    if not count or count <= 1:
        limit_temp = ""

    if rarity:

        rarityStr = f"""
        <div class="rarity">
            <div class="rarity-item"><img decoding="async"
                    src="{nonebot_plugin_masterduel_img_dir}\\deck_html_img\\{rarity}.png"
                    class="lazy entered loaded"
                    data-ll-status="loaded"></div>
        </div>
        """
    else:
        rarityStr = ""

    temp = f"""
    <li class="ygo-card card-box" data-ygo-card-sn="{cardId}"
        data-target="webuiPopover3">
            {rarityStr}
        <div class="card-image"><img decoding="async"
                src="https://cdn.233.momobako.com/ygopro/pics/{cardId}.jpg"
                class="lazy entered loaded" data-ll-status="loaded">
            {limit_temp}
        </div>
    </li>
    """
    return temp
