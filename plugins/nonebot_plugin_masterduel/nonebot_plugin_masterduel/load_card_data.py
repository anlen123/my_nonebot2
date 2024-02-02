#!/Users/liuhq/miniconda3/bin/python
# -*- coding: utf-8 -*-


import meilisearch, json, re, time, requests, zipfile, os, httpx
from bs4 import BeautifulSoup

client = meilisearch.Client('http://localhost:7700')
new_index_name = 'd_cards_version4'
try:
    client.delete_index(new_index_name)
    client.create_index(new_index_name)
except:
    pass
# time.sleep(30)
client.create_index(new_index_name)
new_index = client.get_index(new_index_name)
alias_index = client.get_index('d_cards_alias')
base_index = client.get_index('d_cards')


class Ygo_Card:

    def __init__(self):
        self.id = None
        self.card_no = None
        self.name = None
        self.alias = None
        self.desc = None
        self.attribute_desc = None
        self.type = None
        self.monsters_type = None
        self.ATK = None
        self.DEF = None
        self.lv = None
        self.race = None
        self.attribute = None
        self.img = None
        self.quality = None

    def json_format(self):
        return {
            "id": self.id,
            "card_no": self.card_no,
            "name": self.name,
            "alias": self.alias,
            "attribute_desc": self.attribute_desc,
            "type": self.type,
            "ATK": self.ATK,
            "DEF": self.DEF,
            "lv": self.lv,
            "race": self.race,
            "attribute": self.attribute,
            "desc": self.desc,
            "img": self.img,
            "monsters_type": self.monsters_type,
            "quality": self.quality
        }


def gou_zao(ygo: dict, quality: dict):
    card = Ygo_Card()
    name = ygo.get('cn_name') if ygo.get('cn_name') else ygo.get('jp_name')
    card.id = ygo.get('cid')
    try:
        alias_name = alias_index.get_document(str(card.id)).name
        card.alias = alias_name
    except meilisearch.errors.MeilisearchApiError as ee:
        card.alias = name
    card.attribute_desc = ygo.get('text').get('types')
    card.name = name
    card.desc = ygo.get('text').get('desc')
    card.card_no = ygo.get('id')

    # 品质
    quality = quality.get(str(card.id))
    if quality:
        if quality == 4:
            card.quality = "UR"
        elif quality == 3:
            card.quality = "SR"
        elif quality == 2:
            card.quality = "R"
        elif quality == 1:
            card.quality = "N"
    else:
        card.quality = "暂未登录MD"

    if '怪兽' in card.attribute_desc:
        card.type = '怪兽卡'
        race_regex = re.findall("\ (.*?)\/(.*?)\n", card.attribute_desc)
        # 种族
        card.race = race_regex[0][0] + "族"
        # 属性
        card.attribute = race_regex[0][1] + "属性"
        card.lv = ygo.get('data').get('level')
        card.ATK = ygo.get('data').get('atk')
        card.DEF = ygo.get('data').get('def')
        monsters_type_flag = False
        monsters_type = []

        if '通常' in card.attribute_desc:
            monsters_type.append('通常')
            monsters_type_flag = True
        if '效果' in card.attribute_desc:
            monsters_type.append('效果')
            monsters_type_flag = True
        if '同调' in card.attribute_desc:
            monsters_type.append('同调')
            monsters_type_flag = True
        if '超量' in card.attribute_desc:
            monsters_type.append('超量')
            monsters_type_flag = True
        if 'LINK' in card.attribute_desc:
            monsters_type.append('LINK')
            monsters_type_flag = True
        if '灵摆' in card.attribute_desc:
            monsters_type.append('灵摆')
            monsters_type_flag = True
        if '融合' in card.attribute_desc:
            monsters_type.append('融合')
            monsters_type_flag = True
        if '二重' in card.attribute_desc:
            monsters_type.append('二重')
            monsters_type_flag = True
        if '灵魂' in card.attribute_desc:
            monsters_type.append('灵魂')
            monsters_type_flag = True
        if '调整' in card.attribute_desc:
            monsters_type.append('调整')
            monsters_type_flag = True
        if not monsters_type_flag:
            monsters_type.append("未知")
        card.monsters_type = monsters_type
    elif '陷阱' in card.attribute_desc:
        card.type = '陷阱卡'
        card.race = "无"
        card.lv = "-1"
        card.ATK = "-1"
        card.DEF = "-1"
        card.monsters_type = "非怪兽卡"
        flag = False
        if '永续' in card.attribute_desc:
            card.attribute = "永续陷阱"
            flag = True
        if '反击' in card.attribute_desc:
            card.attribute = "反击陷阱"
            flag = True
        if not flag:
            card.attribute = "通常陷阱"
    elif '魔法' in card.attribute_desc:
        card.type = '魔法卡'
        card.race = "无"
        card.lv = "-1"
        card.ATK = "-1"
        card.DEF = "-1"
        card.monsters_type = "非怪兽卡"
        flag = False
        if '永续' in card.attribute_desc:
            card.attribute = "永续魔法"
            flag = True
        elif '速攻' in card.attribute_desc:
            card.attribute = "速攻魔法"
            flag = True
        elif '场地' in card.attribute_desc:
            card.attribute = "场地魔法"
            flag = True
        if not flag:
            card.attribute = "通常魔法"

    if is_include_id_by_index(card.id, base_index):
        x = base_index.get_document(card.id)
        card.img = x.img
    else:
        img = get_card_img(card.id)
        card.img = img
    # print(card.json_format())
    return card


def read_cards_ids() -> tuple:
    cards_list = []
    with open('cards.json') as f:
        cards_dict = json.loads(f.read())
    for card in list(cards_dict.values()):
        id = card.get('cid')
        cards_list.append(str(id))

    return cards_list, cards_dict


def read_cards_quality() -> dict:
    cards_list = []
    with open('CardList.json') as f:
        cards_dict = json.loads(f.read())
    return cards_dict


def update(index: meilisearch.index.Index):
    # 下载
    # down_ygo_card()
    # down_duel_quality()
    ids, cards_dict = read_cards_ids()
    quality_dict = read_cards_quality()
    ygos = []
    # ids = [id for id in ids if id == '13668']
    for id in ids:
        if not is_include_id_by_index(id, index):
            card = gou_zao(cards_dict.get(id), quality_dict)
            print(card.name)
            ygos.append(card.json_format())
            print("更新")
        else:
            print("存在")
        if len(ygos) >= 200:
            index.add_documents_json(ygos)
            ygos = []
    while ygos:
        index.add_documents_json(ygos)
        ygos = []


def is_include_id_by_index(id: str, index: meilisearch.models.document.Document):
    try:
        index.get_document(id)
        return True
    except meilisearch.errors.MeilisearchApiError as ee:
        return False


def write_card_to_meilisearch(cards):
    client = meilisearch.Client('http://localhost:7700')
    client.delete_index('d_cards')
    client.create_index('d_cards')
    index = client.index('d_cards')
    index.delete_all_documents()
    documents = []
    for card in cards:
        documents.append(
            {'id': card.idd, 'name': card.name, 'desc': card.desc, 'attribute': card.attribute, 'img': card.img})
    index.add_documents(documents)


def get_card_img(idd: int) -> str:
    headers = {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Connection': 'keep-alive',
        'Referer': 'https://ygocdb.com',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
    }
    url = f'https://ygocdb.com/?search={idd}'
    try:
        response = httpx.get(url, headers=headers)
        html_data = response.text
        soup = BeautifulSoup(html_data, 'html.parser')
        img_tag = soup.find('img', {'data-original': True})
        data_original = img_tag['data-original']
        if data_original:
            data_original = data_original[:-5]
        return data_original
    except:
        return ""


def down_duel_quality():
    url = "https://github.com/pixeltris/YgoMaster/raw/master/YgoMaster/Data/CardList.json"
    resp = requests.get(url)
    with open('CardList.json', 'wb') as f:
        f.write(resp.content)


def down_ygo_card():
    zip_name = 'ygo_card.zip'
    if os.path.exists(zip_name):
        os.remove(zip_name)
    url = "https://ygocdb.com/api/v0/cards.zip"
    resp = requests.get(url)
    with open(zip_name, "wb") as f:
        f.write(resp.content)
    with zipfile.ZipFile(zip_name, 'r') as zip_f:
        zip_f.extractall('.')

    if os.path.exists(zip_name):
        os.remove(zip_name)


if __name__ == '__main__':
    # new_index.delete_all_documents()
    time.sleep(3)
    update(new_index)
    client.index('d_cards_version4').update_settings({
        "distinctAttribute": "id",
        "filterableAttributes": [
            "alias",
            "attribute",
            "race",
            "lv",
            "type",
            "ATK",
            "DEF",
            "name",
            "monsters_type",
            "card_no",
            "quality"
        ],
        "searchableAttributes": [
            "alias",
            "attribute",
            "race",
            "lv",
            "type",
            "ATK",
            "DEF",
            "name",
            "desc",
            "attribute_desc"
            "monsters_type",
            "card_no",
            "quality"
        ],
        "sortableAttributes": [
            "lv",
            "ATK",
            "DEF"
        ],
        "pagination": {
            "maxTotalHits": 5000
        },
        "faceting": {
            "maxValuesPerFacet": 200
        }
    })
