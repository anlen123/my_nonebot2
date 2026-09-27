import json
import sqlite3
from io import BytesIO
from typing import List

import Levenshtein
import nonebot
import parsel
import requests
from PIL import Image, ImageFilter

from .model.Alias import Alias
from .model.Card import YgoCard
from .model.Card import buildYgoCard
from .model.Datas import Datas
from .model.Rarity import Rarity
from .model.Texts import Texts
from .utils.likelihoodUtils import Likelihood
from .config import config

from objprint import op

nonebot_plugin_masterduel_root_dir = config.nonebot_plugin_masterduel_root_dir
nonebot_plugin_masterduel_img_dir = config.nonebot_plugin_masterduel_img_dir
nonebot_plugin_masterduel_img_card_dir = config.nonebot_plugin_masterduel_img_card_dir


def get_like(s1: str, s2: str):
    a = Likelihood()
    return a.likelihood(s1, s2)


def get_card_info_by_id(cardId: int) -> YgoCard:
    try:
        datas = get_datas(f'SELECT * FROM datas WHERE id = {cardId}')[0]
        texts = get_texts(f'SELECT * FROM texts WHERE id = {cardId}')[0]
        ygoCard = buildYgoCard(datas, texts)
        return ygoCard
    except IndexError:
        print(f"No card found with id {cardId}")


def get_card_info_like_name(name: str) -> YgoCard:
    try:
        texts = get_texts(f'SELECT * FROM texts WHERE name like "%{name}%"')[0]
        return get_card_info_by_id(int(texts.id))
    except IndexError:
        print(f"No card found with id {name}")


def get_card_info_like_names(name: str) -> List[YgoCard]:
    try:
        textsList = get_texts(f'SELECT * FROM texts WHERE name like "%{name}%"')
        return [get_card_info_by_id(int(texts.id)) for texts in textsList]
    except IndexError:
        print(f"No card found with id {name}")


def get_card_info_by_name(name: str) -> YgoCard:
    try:
        texts = get_texts(f'SELECT * FROM texts WHERE name = "{name}"')[0]
        return get_card_info_by_id(int(texts.id))
    except IndexError:
        print(f"No card found with id {name}")


def get_card_info_by_alias(name: str) -> YgoCard:
    try:
        aliasList = get_nonebot_plugin_masterduel_alias(f'SELECT * FROM alias WHERE name = "{name}"')
        if aliasList:
            return get_card_info_by_id(int(aliasList[0].card_id))
    except IndexError:
        print(f"No card found with id {name}")


def set_card_alias_by_id(cardId: int, name: str):
    try:
        aliasList = get_nonebot_plugin_masterduel_alias(f'SELECT * FROM alias WHERE name = "{name}"')
    except IndexError:
        print(f"No card found with id {name}")
        return None

    if aliasList:
        alias = aliasList[0]  # 更新
        set_nonebot_plugin_masterduel(f'update alias set card_id = {cardId} where name = "{alias.name}"')
    else:
        # 插入
        set_nonebot_plugin_masterduel(f'insert into alias (name, card_id) VALUES ("{name}",{cardId})')


def get_ban_msg(card_id: int):
    datas = read_ban_card(card_id)
    all_date = get_all_ban_date()
    get_all_ban_date()
    msg = ""

    index = 1
    datas_dict = {}
    for data in datas:
        card_id, _, ban_date, c = data
        datas_dict[ban_date] = c

    flag = 3
    for date in all_date:
        c = datas_dict.get(date, 3)
        if c != flag:
            msg += f"第{index}次调整 {date}: {flag} -> {c}\n"
            flag = c
            index += 1
    return msg


def read_ban_card(card_id: int):
    return get_nonebot_plugin_masterduel(f"SELECT * FROM ban WHERE id = {card_id} ORDER BY date")


def get_all_ban_date():
    return [_[0] for _ in get_nonebot_plugin_masterduel(f"select distinct date from ban where ban_type order by date")]


def get_nonebot_plugin_masterduel_rarity(sql: str) -> list[Rarity]:
    rows = get_nonebot_plugin_masterduel(sql)
    rarityList = []
    # 对于每一行，创建一个 Ygo_Card 对象，并添加到列表中
    for row in rows:
        # print(row)
        rarity = Rarity(*row)
        rarityList.append(rarity)
    return rarityList


def get_datas(sql: str) -> list[Datas]:
    rows = get_cards(sql)
    datasList = []
    for row in rows:
        # print(row)
        datas = Datas(*row)
        datasList.append(datas)
    return datasList


def set_nonebot_plugin_masterduel(sql: str):
    print(sql)
    conn = sqlite3.connect(f'{nonebot_plugin_masterduel_root_dir}\\nonebot_plugin_masterduel.cdb')
    cursor = conn.cursor()
    cursor.execute(sql)
    conn.commit()
    conn.close()


def close_ygo_game_open_value():
    set_nonebot_plugin_masterduel(f"update redis_like set value =\"0\" where name=\"ygo_game_open\"")


def open_ygo_game_open_value():
    set_nonebot_plugin_masterduel(f"update redis_like set value =\"1\" where name=\"ygo_game_open\"")


def is_ygo_game_open() -> bool:
    ygo_game_open = get_nonebot_plugin_masterduel(f"select * from redis_like where name=\"ygo_game_open\"")
    print(f"{ygo_game_open=}")
    if ygo_game_open:
        ygo_game_open_value = ygo_game_open[0][1]
        return str(ygo_game_open_value) == "1"
    return False


def get_nonebot_plugin_masterduel_alias(sql: str) -> list[Alias]:
    rows = get_nonebot_plugin_masterduel(sql)
    aliasList = []
    for row in rows:
        alias = Alias(*row)
        aliasList.append(alias)
    return aliasList


def get_texts(sql: str) -> list[Texts]:
    rows = get_cards(sql)
    textsList = []
    for row in rows:
        texts = Texts(*row)
        textsList.append(texts)
    return textsList


def get_nonebot_plugin_masterduel(sql: str):
    print(sql)
    conn = sqlite3.connect(f'{nonebot_plugin_masterduel_root_dir}\\nonebot_plugin_masterduel.cdb')
    cursor = conn.cursor()
    cursor.execute(sql)
    # 获取查询结果
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_cards(sql: str):
    conn = sqlite3.connect(f'{nonebot_plugin_masterduel_root_dir}\\cards.cdb')
    cursor = conn.cursor()
    cursor.execute(sql)
    # 获取查询结果
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_id_and_name_all():
    return get_cards("select id,name from texts")


def get_max_like_id(name: str):
    print(name)

    rows = get_id_and_name_all()
    ret = None
    max_number = 0
    for row in rows:
        name1 = row[1]
        dis = get_like(name1, name)
        # dis = Levenshtein.jaro(name1, name)
        if dis > max_number:
            max_number = dis
            ret = row
            print(dis)

    if (not ret) or (float(max_number) < 0.5):
        return ""
    return ret[0]


def get_nonebot_plugin_masterduel_id_by_cid(cid: int):
    ret = get_nonebot_plugin_masterduel(f"select id from cid where cid = '{cid}'")

    url = f'https://ygocdb.com/api/v0/?search={cid}'

    if ret:
        return ret[0][0]
    else:
        url = f'https://ygocdb.com/api/v0/?search={cid}'
        resp = requests.get(url, headers={"qq": "1761512493"})
        resp_json = json.loads(resp.text)
        flag = False
        for x in resp_json['result']:
            r_cid = (x['cid'])
            card_id = (x['id'])
            if str(cid).strip() == str(r_cid).strip():
                set_nonebot_plugin_masterduel(f"insert into cid (id,cid) values ('{card_id}','{cid}')")
                return card_id


def get_card_by_md_shard_deck_code(deckCode: str) -> tuple:
    search_url = "https://ayk-deck.mo.konami.net/ayk/yocgapi/search"
    detail_url = "https://ayk-deck.mo.konami.net/ayk/yocgapi/detail"

    headers = {"User-Agent": "UnityPlayer/2020.3.46f1 (UnityWebRequest/1.0, libcurl/7.84.0-DEV)",
               "Host": "ayk-deck.mo.konami.net"}

    post_data = {"commandName": "data.ycdb.searchDeckForMd",
                 "params": {"typeCode": 0, "categoryList": [], "tagList": [], "cardIdList": [], "keyword": "",
                            "deckCode": f"{deckCode}", "sortCode": 2, "sizePerPage": 100, "requestPageNo": 0,
                            "deckTypeList": [1, 2]}}

    resp = requests.post(url=search_url, data=json.dumps(post_data), headers=headers, verify=False)
    deck_resp = json.loads(resp.text)
    # print(f"{deck_resp=}")
    if not deck_resp['result']['deckList']:
        return ()
    post_data = {"commandName": "data.ycdb.searchDeckForMd",
                 "params": {"targetId": f"{deck_resp['result']['deckList'][0]['cardgameId']}",
                            "deckNo": f"{deck_resp['result']['deckList'][0]['deckNo']}", }}

    resp = requests.post(url=detail_url, data=json.dumps(post_data), headers=headers, verify=False)

    # print(resp.text)

    card_resp = json.loads(resp.text)
    mainCardListCid = []
    extraCardListCid = []
    for cid in card_resp['result']['mainCardList']:
        cardId = [cid['cardId']]
        count = int(cid['count'])
        if cardId:
            mainCardListCid += cardId * count

    for cid in card_resp['result']['extraCardList']:
        cardId = [cid['cardId']]
        count = int(cid['count'])
        if cardId:
            extraCardListCid += cardId * count

    mainCardList = [y for y in [get_nonebot_plugin_masterduel_id_by_cid(int(x)) for x in mainCardListCid] if y]
    exCardList = [y for y in [get_nonebot_plugin_masterduel_id_by_cid(int(x)) for x in extraCardListCid] if y]

    return mainCardList, exCardList


def get_cai_ding(cardId: int):
    # key = "绝对王 J革命"
    ygoCard = get_card_info_by_id(cardId)
    if not ygoCard:
        return []
    url = f"https://ocg-rule.readthedocs.io/_/api/v2/search/?q=\"{ygoCard.name}\"&project=ocg-rule&version=latest&language=zh-cn"
    resp = requests.get(url=url)
    resp_json = json.loads(resp.text)
    msg = []
    for x in resp_json['results']:
        title = x['title']
        msg_temp = title
        content_list = []
        for y in x['blocks']:
            title_detail = y['title']
            content = y['content']
            content_list.append([title_detail, content])
        msg.append([msg_temp, content_list])
    return msg


def get_cai_ding_html(cardId: int) -> str:
    msgList = get_cai_ding(cardId)
    if not msgList:
        return ""
    ygoCard = get_card_info_by_id(cardId)
    caiDingList = []

    for msg in msgList:
        title = msg[0]
        detailList = []
        for m in msg[1]:
            t = m[0]
            desc = m[1]
            detail_temp = f"""
            <p>
                {t}
            </p>
            <p class="fragment">
                {desc}
            </p>
            """
            detailList.append(detail_temp)

        detailStr = '\n'.join(detailList)
        singe_temp = f"""
        <li class="module-item search-result-item">
            <p class="module-item-title">
                {title}
            </p>
            {detailStr}
        </li>
        """
        caiDingList.append(singe_temp)
    caiDingStr = '\n'.join(caiDingList)
    sss = f"""
    <head>
    <!-- css -->
    <link rel="stylesheet" href="https://assets.readthedocs.org/css/core.42fd09c35ae4.css">
    <style>
        .module {{
            border: 4px solid blue;  /* 加粗蓝色边框 */
            padding: 10px;  /* 添加内边距 */
            margin: 10px;  /* 添加外边距 */
            background: linear-gradient(to right, #3498db, #2ecc71);  /* 添加渐变背景色 */
        }}
    </style>
    <div class="module">
        <div class="module-wrapper">
        <div class="module-header">
            <h3>
            {ygoCard.name}
            </h3>
        </div>
        <div class="module-list">
            <div class="module-list-wrapper">
            <ul>
                {caiDingStr}
            </ul>
            </div>
        </div>
        </div>
    </div>
    """
    return sss


def get_selas_time_by_id(cardId: int):
    resp = requests.get(f"https://ygocdb.com/card/{cardId}")
    pa = parsel.Selector(resp.text)
    pack = (pa.xpath('/html/body/main/div/div[2]/div[2]/div[3]/div[1]/span/a/@href').get())
    print(f"https://ygocdb.com{pack}")
    resp = requests.get(f"https://ygocdb.com{pack}")
    pa = parsel.Selector(resp.text)
    # print(resp.text)
    sale_time = pa.xpath('/html/body/main/div/div[1]/div/p/a/span[1]/text()').get()
    count_sum = pa.xpath('/html/body/main/div/div[1]/div/p/a/span[2]/text()').get()
    return sale_time, count_sum, pack[6:]


def get_card_by_pack(pack: str):
    resp = requests.get(f"https://ygocdb.com/pack/{pack}")
    pa = parsel.Selector(resp.text)
    # print(resp.text)
    card_htmls = pa.xpath('/html/body/main/div//div[contains(@class, "row card result")]').getall()
    card_ids = []
    for card_html in card_htmls:
        # print(card_html)
        # /html/body/main/div/div[3]/div[1]/a
        card_id = parsel.Selector(card_html).xpath('//div[1]/a/@href').get()
        card_ids.append(card_id[6:])
    return card_ids


def crop_image_from_url(cardId: int):
    # 下载图片
    url = f"https://cdn.233.momobako.com/ygopro/pics/{cardId}.jpg"
    response = requests.get(url)
    img = Image.open(BytesIO(response.content))

    # 裁剪图片
    cropped_img = img.crop((50, 110, 350, 400))

    blurred_img = cropped_img.filter(ImageFilter.GaussianBlur(6))

    # 展示裁剪后的图片
    # blurred_img.show()
    blurred_img.save(f"{nonebot_plugin_masterduel_img_dir}\\cai\\{cardId}.jpg")


def get_ygo_rank(top: int):
    rows = get_nonebot_plugin_masterduel(f"select * from ygorank order by count desc limit {top}")
    return rows


def ygo_rank_add(qq: int, name: str):
    rows = get_nonebot_plugin_masterduel(f'select * from ygorank where user_id = "{qq}"')
    if rows:
        row = rows[0]
        print(row)
        user_id = row[0]
        count = row[2]
        count = int(count) + 1
        set_nonebot_plugin_masterduel(
            f'update ygorank set count = "{count}", name = "{name}" where user_id = "{user_id}"')
    else:
        set_nonebot_plugin_masterduel(f'insert into ygorank (user_id, name, count) VALUES ("{qq}","{name}",1)')
