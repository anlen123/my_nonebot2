from PIL import Image
import requests
from io import BytesIO
import base64
import os, random, nonebot, time
from collections import Counter
from . import rarityUtils

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

from ..model.Card import YgoCard
from typing import List
from ..config import config
import httpx

nonebot_plugin_masterduel_root_dir = config.nonebot_plugin_masterduel_root_dir
nonebot_plugin_masterduel_img_dir = config.nonebot_plugin_masterduel_img_dir
nonebot_plugin_masterduel_img_card_dir = config.nonebot_plugin_masterduel_img_card_dir


def down_card_id(card_id: int):
    url_image_url = f'https://cdn.233.momobako.com/ygopro/pics/{card_id}.jpg'
    print(url_image_url)
    # 检查本地是否存在URL图片
    local_img_path = f'{nonebot_plugin_masterduel_img_card_dir}\\{card_id}.jpg'

    if not os.path.exists(local_img_path):
        # 否则，通过URL下载图片并保存到本地
        print("下载图片ing")
        response = requests.get(url_image_url)
        url_image = Image.open(BytesIO(response.content))
        url_image.save(local_img_path)  # 保存到本地
    return local_img_path


def pin_quality(card_id: int, quality: str):
    # 准备 URL 图片的地址

    local_image_path = f'{nonebot_plugin_masterduel_img_dir}\\{quality}'

    local_img_path = down_card_id(card_id=card_id)
    # 加载本地图片
    if not quality:
        return local_img_path
    url_image = Image.open(local_img_path).convert('RGBA')

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
        response = httpx.get(url)

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
        with open(name, 'w', encoding='utf-8') as f:
            f.write(sss)
        file_url = f"file:///{os.getcwd()}\\{name}"
        driver.get(file_url)
        time.sleep(1)
        width = driver.execute_script("return document.documentElement.scrollWidth")
        height = driver.execute_script("return document.documentElement.scrollHeight")
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
        driver.set_window_size(width, height)
        time.sleep(1)
        driver.save_screenshot(pic_name)
    except Exception as e:
        print(e)
    finally:
        driver.close()
        print("截图完毕")


def get_pl_all_temp(cards: List[YgoCard]):
    all_sss = ""
    for card in cards:
        all_sss += get_pl_singe_temp(card)
    return f"""
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Card Design</title>
    <link rel="stylesheet"
        href="https://lf26-cdn-tos.bytecdntp.com/cdn/expire-1-y/twitter-bootstrap/3.4.1/css/bootstrap.min.css"
        integrity="sha384-HSMxcRTRxnN+Bdg0JdbxYKrThecOKuH5zCYotlSAcp1+c8xmyTe9GYg1l9a69psu" crossorigin="anonymous">
    <style>
        .result {{
            border: 5px solid #9ea00ad3;
            /* Blue border */
            border-radius: 10px;
            /* Rounded corners */
            padding: 20px;
            /* Space inside the border */
            margin-top: 20px;
            /* Space above the card */
            box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1);
            /* Soft shadow */
            background-color: #f9f9f9;
            /* Light background color */
        }}

        .cardimg img {{
            border-radius: 8px;
            /* Rounded corners for the image */
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.2);
            /* Shadow for the image */
            max-width: 100%;
            /* Ensure the image fits within its container */
        }}

        .result div {{
            color: #333;
            /* Darker text color */
            line-height: 1.6;
            /* Improved readability */
        }}
    </style>
</head>
    
    <body>
        <main>
            {all_sss}
        </main>
    </body>
    """


def get_pl_singe_temp(card: YgoCard):
    file_url = down_card_id(card.id)
    return f"""
            <div class="container">
            <div class="row card result">
                <div class="col-md-12">
                    <hr>
                </div>
                <div class="col-md-3 col-xs-4 cardimg">
                    <img src="{file_url}" style="display: block;">
                </div>
                <div style="font-size: 20px;">
                    卡名: {card.name}
                    <br>
                    效果: {card.desc}
                </div>

            </div>
        </div>
    """


def get_all_temp(cardListMain: None, cardListEx: None):
    cardId2count = Counter(cardListMain)
    tempList = []
    UR = 0
    SR = 0

    for key, value in cardId2count.items():
        rarity = rarityUtils.get_rarity(int(key))
        if rarity == "UR":
            UR += (30 * value)
        if rarity == "SR":
            SR += (30 * value)
        tempList.append(get_singe_temp(int(key), value, rarity))
    tempMainStr = '\n'.join(tempList)
    if cardListEx:
        cardIdEx2count = Counter(cardListEx)
        tempExList = []
        for key, value in cardIdEx2count.items():
            rarity = rarityUtils.get_rarity(int(key))
            if rarity == "UR":
                UR += (30 * value)
            if rarity == "SR":
                SR += (30 * value)
            tempExList.append(get_singe_temp(int(key), value, rarity))
        tempExStr = '\n'.join(tempExList)
    else:
        tempExStr = ""
    UR_FILE_URL = f"{nonebot_plugin_masterduel_img_dir}\\UR"
    SR_FILE_URL = f"{nonebot_plugin_masterduel_img_dir}\\SR"
    sss = f"""
    <link rel="stylesheet" id="deck-card-css"
        href="https://duelmeta.com/wp-content/plugins/ygo-plugin-master/assets/css/deck-card.css?ver=6.4.5" type="text/css"
        media="all">
        <style>
            .area {{
                border: 20px solid green;
            }}
            .card-number {{
                font-size: 100px;
                /* 调整这个值以匹配上面的UR图标的字体大小 */
                font-weight: bold;
                /* 如果需要，可以加粗字体 */
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
                                                    <div style="text-align: left; margin-top: 12px;">
                                                        <img src="{UR_FILE_URL}"
                                                            alt="Additional Image">
                                                        <a class="card-number">： {UR}</a>
                                                        <br>
                                                        <img src="{SR_FILE_URL}"
                                                            alt="Additional Image">
                                                        <a class="card-number">： {SR}</a>
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

    file_url = down_card_id(cardId)
    temp = f"""
    <li class="ygo-card card-box" data-ygo-card-sn="{cardId}"
        data-target="webuiPopover3">
            {rarityStr}
        <div class="card-image"><img decoding="async"
                src="{file_url}"
                class="lazy entered loaded" data-ll-status="loaded">
            {limit_temp}
        </div>
    </li>
    """
    return temp
