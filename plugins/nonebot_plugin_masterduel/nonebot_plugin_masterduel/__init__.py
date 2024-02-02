from typing import List, Any
from .config import Config
from nonebot.plugin import on_notice, on_regex
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment, Message, GroupMessageEvent
import aiohttp, asyncio, json, nonebot, re, httpx, meilisearch
from nonebot import get_driver
import openai
from PIL import Image
import requests
from io import BytesIO
import base64
import os

global_config = get_driver().config
config = global_config.dict()
openai.api_key = config.get('gpt_api_key')

meilisearch_host = config.get('meilisearch_host')
card_limit = 3


class Ygo_Card:

    def __init__(self, *args):
        if len(args) == 1:
            card = args[0]
            self.id = card.id
            self.name = card.name
            self.alias = card.alias
            self.desc = card.desc
            self.attribute_desc = card.attribute_desc
            self.type = card.type
            self.monsters_type = card.monsters_type
            self.ATK = card.ATK
            self.DEF = card.DEF
            self.lv = card.lv
            self.race = card.race
            self.attribute = card.attribute
            self.img = card.img
            self.card_no = card.card_no
            self.quality = card.quality
        if len(args) == 2:
            id = args[0]
            card = args[1]
            self.id = id
            self.name = card['name']
            self.alias = card['alias']
            self.desc = card['desc']
            self.attribute_desc = card['attribute_desc']
            self.type = card['type']
            self.monsters_type = card['monsters_type']
            self.ATK = card['ATK']
            self.DEF = card['DEF']
            self.lv = card['lv']
            self.race = card['race']
            self.attribute = card['attribute']
            self.img = card['img']
            self.card_no = card['card_no']
            self.quality = card['quality']


master_duel = on_regex(pattern="^ygo ")


@master_duel.handle()
async def master_duel_rev(bot: Bot, event: Event):
    content = str(event.get_plaintext()[4:]).strip()
    print(content)
    ret_by_alias = await search_card_by_alias(content)
    hits_by_alias = ret_by_alias.get('hits')
    msg = get_send_msg(hits_by_alias)

    if msg:
        message = msg[0]
        message += MessageSegment.text("--------别名精准搜索")
        await bot.send(event, message)
        return

    ret = await search_card(content, 2)
    hits = ret.get('hits')
    try:
        msg = get_send_msg(hits)
        if msg:
            message = msg[0]
            message += MessageSegment.text("--------全文精确搜索")
            if len(msg) > 1:
                name = []
                for hit in hits[1:card_limit]:
                    name.append(hit.get('name'))
                other = '\n'.join(name)
                message += ("\n其他符合条件的卡名：\n" + other)

            await bot.send(event, message)
        else:
            ret2 = await search_card(content, 1)
            hits_2 = ret2.get('hits')
            msg = get_send_msg(hits_2)
            if msg:
                message = msg[0]
                message += MessageSegment.text("--------分词模糊搜索")
                if len(msg) > 1:
                    name = []
                    for hit in hits[1:card_limit]:
                        name.append(hit.get('name'))
                    other = '\n'.join(name)
                    message += ("\n=============================\n其他符合条件的卡名：\n" + other)

                await bot.send(event, message)
            else:
                await bot.send(event, MessageSegment.text("没有查询到卡片"))
    except nonebot.adapters.onebot.v11.exception.ActionFailed as e:
        await bot.send(event, MessageSegment.text("帐号风控了！！无语住"))


def get_send_msg(hits):
    msg = []
    if hits:
        msg: List[MessageSegment] = []
    else:
        return msg
    for _ in hits:
        if _:
            img = pin_quality(_['img'], _['quality'])
            msg.append("名称：" + MessageSegment.text(_['name']) + f"({_['id']})\n"
                       + MessageSegment.image(img) + "\n"
                       + _['desc'] + f"\n====================\n品质: {_['quality']}\n编号: {_['card_no']}\n")
    return msg


def is_int(n: str):
    try:
        float_n = float(n)
        int_n = int(float_n)
    except ValueError as e:
        return False
    else:
        return float_n == int_n


async def alias_name(id: str, alias: str):
    url = f"http://{meilisearch_host}:7700/indexes/d_cards_version4/documents"

    payload = json.dumps({
        "id": id,
        "alias": alias
    })

    headers = {
        'Content-Type': 'application/json'
    }
    async with aiohttp.ClientSession() as session:
        async with session.put(url, headers=headers, data=payload) as resp:
            return await resp.json()


async def search_card_by_alias(content: str):
    url = f"http://{meilisearch_host}:7700/indexes/d_cards_version4/search"
    payload = json.dumps({
        "filter": f"alias={content}"
    })
    headers = {
        'Content-Type': 'application/json'
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, data=payload) as resp:
            return await resp.json()


async def search_card(content: str, mode: int):
    url = f"http://{meilisearch_host}:7700/indexes/d_cards_version4/search"

    payload1 = json.dumps({
        "q": content
    })

    payload2 = json.dumps({
        "q": content,
        "matchingStrategy": "all",
    })

    headers = {
        'Content-Type': 'application/json'
    }
    if mode == 1:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, data=payload1) as resp:
                ret = await resp.json()
    if mode == 2:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, data=payload2) as resp:
                ret = await resp.json()
    return ret


# 合并消息
async def send_forward_msg_group(
        bot: Bot,
        event: GroupMessageEvent,
        name: str,
        msgs: List[str],
):
    def to_json(msg):
        return {"type": "node", "data": {"name": name, "uin": bot.self_id, "content": msg}}

    messages = [to_json(msg) for msg in msgs]
    await bot.call_api(
        "send_group_forward_msg", group_id=event.group_id, messages=messages
    )


master_duel_ck_gpt = on_regex(pattern="^ygogpt ")


@master_duel_ck_gpt.handle()
async def master_duel_ck_gpt_rev(bot: Bot, event: Event):
    content = str(event.get_plaintext()[7:]).strip()
    print(content)
    content = await chat_free_gpt(content)
    print(content)
    if not content:
        await bot.send(event, MessageSegment.text(f"查询条件构造错误({content})"))
        return
    ret = await search_card_by_filter(content)
    hits = ret.get('hits')
    msg = get_send_msg(hits)
    if msg:
        message = msg[0]
        message += MessageSegment.text(f"--------gpt搜索({content})")
        if len(msg) > 1:
            name = []
            for hit in hits[1:card_limit]:
                name.append(hit.get('name'))
            other = '\n'.join(name)
            message += ("\n=============================\n其他符合条件的卡名：\n" + other)
        await bot.send(event, message)
    else:
        await bot.send(event, MessageSegment.text(f"没有查询到卡片({content})"))


master_duel_ck_ygos = on_regex(pattern="^ygos ")


@master_duel_ck_ygos.handle()
async def master_duel_ck_ygos_rev(bot: Bot, event: Event):
    content = str(event.get_plaintext()[5:]).strip()
    print(content)
    ret = await search_card_by_filter(content)
    hits = ret.get('hits')
    msg = get_send_msg(hits)
    if msg:
        message = msg[0]
        message += MessageSegment.text(f"--------gpt搜索({content})")
        if len(msg) > 1:
            name = []
            for hit in hits[1:card_limit]:
                name.append(hit.get('name'))
            other = '\n'.join(name)
            message += ("\n=============================\n其他符合条件的卡名：\n" + other)
        await bot.send(event, message)
    else:
        await bot.send(event, MessageSegment.text(f"没有查询到卡片({content})"))


async def search_card_by_filter(content: str):
    s = "ATK>1000|DEF<100|attibution=光属性|race=龙族"
    url = f"http://{meilisearch_host}:7700/indexes/d_cards_version4/search"
    filter_list = content.split("|")
    q = ""
    for x in filter_list:
        if x.startswith('q='):
            q = x[2:]
    filter_list = [x for x in filter_list if not x.startswith("q=")]

    payload = json.dumps({
        "filter": filter_list,
        "q": q
    })
    print(payload)
    headers = {
        'Content-Type': 'application/json'
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, data=payload) as resp:
            return await resp.json()


master_duel_alias = on_regex(pattern="^ygoalias\ \d+\ ")


@master_duel_alias.handle()
async def master_duel_alias_rev(bot: Bot, event: Event):
    content = str(event.get_plaintext()[9:]).strip()
    print(content)
    content = content.split(" ")
    id = content[0]
    alias = content[1]
    await write_alias_db(id, alias)
    await alias_name(id, alias)
    await bot.send(event, MessageSegment.text("别名命名成功"))


async def write_alias_db(id, alias):
    url = f"http://{meilisearch_host}:7700/indexes/d_cards_alias/documents"

    ygo = await get_card_by_id(id)

    payload = json.dumps({
        "id": id,
        "name": alias,
        "img": ygo.img
    })

    headers = {
        'Content-Type': 'application/json'
    }

    async with aiohttp.ClientSession() as session:
        async with session.put(url, headers=headers, data=payload) as resp:
            return await resp.json()


async def chat_free_gpt(message: str) -> list:
    def chat_by_rule(messages: list):
        openai.api_base = "https://api.chatanywhere.com.cn/v1"
        """为提供的对话消息创建新的回答 (流式传输)

        Args:
            messages (list): 完整的对话消息
            api_key (str): OpenAI API 密钥

        Returns:
            tuple: (results, error_desc)
        """
        try:
            response = openai.ChatCompletion.create(
                model='gpt-3.5-turbo',
                messages=messages,
                stream=True,
            )
            completion = {'role': '', 'content': ''}
            for event in response:
                if event['choices'][0]['finish_reason'] == 'stop':
                    # print(f'收到的完成数据: {completion}')
                    break
                for delta_k, delta_v in event['choices'][0]['delta'].items():
                    print(f'{delta_v}', end='')
                    completion[delta_k] += delta_v
            return completion.get("content")
        except Exception as err:
            return f'OpenAI API 异常: {err}'

    rule = """给你一个json串表示自然语言的描述对应的类型，尝试理解并消化给你的例子再帮我转换为特定格式的字符串, 并把转换结果用|分割，只用输出结果即可，不用任何解释，Let's think step by step。
注意: attribute的属性只有["场地魔法","永续魔法","永续陷阱","速攻魔法","反击陷阱"]
monsters_type的属性只有["超量","同调","灵摆","融合","LINK","通常","二重","灵魂","调整"]
火属性通通改为炎属性, 所有的大于都是大于等于, 所有的小于都是小于等于, 转换结果的键不得重复, type只能有三个值其中一个，怪兽卡, 魔法卡, 陷阱卡
lv的值只能是数学，如果不是数字，就过滤掉lv这个结果 
{
"攻击力": "ATK",
"防御力": "DEF",
"属性": "attribute",
"族": "race",
"陷阱卡": "type",
"魔法卡": "type",
"怪兽卡": "type",
"永续": "attribute",
"反击": "attribute",
"速攻": "attribute",
"场地": "attribute",
"link": "lv",
"等级": "lv"
"名字": "q"
"怪兽种类": "monsters_type"
}
例子1:  攻击力大于1000 -> ATK>1000
例子2: 龙族 -> race=龙族
例子3: 光属性->attibution=光属性
例子4: 防御力小于3000 -> DEF<3000
例子5: 永续陷阱卡 -> type=陷阱卡|attribute=永续陷阱
例子6: 速攻魔法卡 -> type=魔法卡|attribute=速攻魔法
例子7: 场地魔法卡 -> type=魔法卡|attribute=场地魔法
例子8: 等级大于10 -> lv>=10
例子9: 等级等于8的龙族怪兽 -> lv=8|race=龙族|type=怪兽卡
例子10: link5的炎属性怪兽 -> lv=5|attribute=炎属性|type=怪兽卡
例子11: 名字中带有闪刀的永续魔法 -> type=魔法卡|attribute=永续魔法|q=闪刀'
例子12: 一只光属性link2的怪兽 -> lv=2|attribute=光属性|type=怪兽卡
例子13: 龙link的反击陷阱卡 -> type=陷阱卡|attribute=反击陷阱|name=龙link
例子14: 兽带的场地 -> type=魔法卡|attribute=场地魔法|q=兽带
例子15: 名字带有黑羽的场地 -> type=魔法卡|attribute=场地魔法|q=黑羽
例子16: 名字带有闪刀的超量怪兽 -> type=怪兽卡|attribute=场地魔法|monsters_type=超量|q=闪刀
例子17: 名字带有刀的同调怪兽 -> type=怪兽卡|monsters_type=同调|q=刀
例子18: 一只又是超量又是灵摆的暗属性龙族怪兽 -> type=怪兽卡|attribute=暗属性|race=龙族|monsters_type=超量|monsters_type=灵摆
例子19: 灵摆通常怪兽 -> type=怪兽卡|monsters_type=灵摆|monsters_type=通常
例子20: 融合效果怪兽 -> type=怪兽卡|monsters_type=融合|monsters_type=效果
例子21: 灵摆 超量 暗属性 -> type=怪兽卡|monsters_type=灵摆|monsters_type=超量|attribute=暗属性
例子22: 闪刀 LINK1 水属性-> type=怪兽卡|monsters_type=LINK|attribute=水属性|lv=1|q=闪刀
例子23: LINK5 龙族 暗属性 前 -> type=怪兽卡|monsters_type=LINK|lv=5|race=龙族|attribute=暗属性|q=前"""
    messages = [{'role': 'system', 'content': f'{rule}'}, {'role': 'user', 'content': f'{message}'}, ]
    return chat_by_rule(messages)


# # 调用openai 问答示例
# async def chat_guanfang(prompt):
#     rule = """给你一个json串表示自然语言的描述对应的类型，尝试理解并消化给你的例子再帮我转换为特定格式的字符串, 并把转换结果用|分割，只用输出结果即可，不用任何解释，Let's think step by step。
#     注意: attribute的属性只有["场地魔法","永续魔法","永续陷阱","速攻魔法","反击陷阱"]
#     monsters_type的属性只有["超量","同调","灵摆","融合","LINK","通常","二重","灵魂","调整"]
#     火属性通通改为炎属性, 所有的大于都是大于等于, 所有的小于都是小于等于, 转换结果的键不得重复, type只能有三个值其中一个，怪兽卡, 魔法卡, 陷阱卡
#     lv的值只能是数学，如果不是数字，就过滤掉lv这个结果
#     {
#     "攻击力": "ATK",
#     "防御力": "DEF",
#     "属性": "attribute",
#     "族": "race",
#     "陷阱卡": "type",
#     "魔法卡": "type",
#     "怪兽卡": "type",
#     "永续": "attribute",
#     "反击": "attribute",
#     "速攻": "attribute",
#     "场地": "attribute",
#     "link": "lv",
#     "等级": "lv"
#     "名字": "q"
#     "怪兽种类": "monsters_type"
#     }
#     例子1:  攻击力大于1000 -> ATK>1000
#     例子2: 龙族 -> race=龙族
#     例子3: 光属性->attibution=光属性
#     例子4: 防御力小于3000 -> DEF<3000
#     例子5: 永续陷阱卡 -> type=陷阱卡|attribute=永续陷阱
#     例子6: 速攻魔法卡 -> type=魔法卡|attribute=速攻魔法
#     例子7: 场地魔法卡 -> type=魔法卡|attribute=场地魔法
#     例子8: 等级大于10 -> lv>=10
#     例子9: 等级等于8的龙族怪兽 -> lv=8|race=龙族|type=怪兽卡
#     例子10: link5的炎属性怪兽 -> lv=5|attribute=炎属性|type=怪兽卡
#     例子11: 名字中带有闪刀的永续魔法 -> type=魔法卡|attribute=永续魔法|q=闪刀'
#     例子12: 一只光属性link2的怪兽 -> lv=2|attribute=光属性|type=怪兽卡
#     例子13: 龙link的反击陷阱卡 -> type=陷阱卡|attribute=反击陷阱|name=龙link
#     例子14: 兽带的场地 -> type=魔法卡|attribute=场地魔法|q=兽带
#     例子15: 名字带有黑羽的场地 -> type=魔法卡|attribute=场地魔法|q=黑羽
#     例子16: 名字带有闪刀的超量怪兽 -> type=怪兽卡|attribute=场地魔法|monsters_type=超量|q=闪刀
#     例子17: 名字带有刀的同调怪兽 -> type=怪兽卡|monsters_type=同调|q=刀
#     例子18: 一只又是超量又是灵摆的暗属性龙族怪兽 -> type=怪兽卡|attribute=暗属性|race=龙族|monsters_type=超量|monsters_type=灵摆
#     例子19: 灵摆通常怪兽 -> type=怪兽卡|monsters_type=灵摆|monsters_type=通常
#     例子20: 融合效果怪兽 -> type=怪兽卡|monsters_type=融合|monsters_type=效果
#     例子21: 灵摆 超量 暗属性 -> type=怪兽卡|monsters_type=灵摆|monsters_type=超量|attribute=暗属性
#     例子22: 闪刀 LINK1 水属性-> type=怪兽卡|monsters_type=LINK|attribute=水属性|lv=1|q=闪刀
#     例子23: LINK5 龙族 暗属性 前 -> type=怪兽卡|monsters_type=LINK|lv=5|race=龙族|attribute=暗属性|q=前"""
#     response = openai.ChatCompletion.create(
#         model="gpt-3.5-turbo",
#         messages=[
#             {"role": "system", "content": f"{rule}"},
#             {"role": "user", "content": prompt},
#         ]
#     )
#     answer = response.choices[0].message.content
#     return answer


# async def chat_with_chatmindai(content: str):
#     url = "https://beta.chatmindai.net/api/chat-process"
#     payload = json.dumps({
#         "message": f"{content}",
#         "chatid": "",
#         "roleid": "229nmq5mey7kzcn0nvx1690954442548",
#         "isContextEnabled": 0
#     })
#
#     headers = {
#         'Accept': '*/*',
#         'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
#         'Authorization': 'Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJwaG9uZW51bSI6IjEzNjc2NTkxNTg5IiwiaWQiOjM1MDQyLCJzdWIiOiJBdXRoZW50aWNhdGlvbiIsImV4cCI6MTY5MTQwODA1NiwianRpIjoiMTZkYTRjMDBkZGYwNGIzYTllOTA2YjJhY2Y5MDcxYmMifQ.n4pQ0szJZSk-q2tp554RAZhGX8Ty0L73opJjBqH1PX4',
#         'Connection': 'keep-alive',
#         'Content-Type': 'application/json',
#         'Origin': 'https://beta.chatmindai.net',
#         'Referer': 'https://beta.chatmindai.net/chat',
#         'Sec-Fetch-Dest': 'empty',
#         'Sec-Fetch-Mode': 'cors',
#         'Sec-Fetch-Site': 'same-origin',
#         'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
#         'sec-ch-ua': '"Not.A/Brand";v="8", "Chromium";v="114", "Google Chrome";v="114"',
#         'sec-ch-ua-mobile': '?0',
#         'sec-ch-ua-platform': '"macOS"'
#     }
#
#     msg = ""
#
#     async with aiohttp.ClientSession() as session:
#         async with session.post(url, headers=headers, data=payload) as response:
#             while True:
#                 line = await response.content.readline()
#                 if not line:
#                     break
#                 msg += str(line.decode('utf-8'))
#     return msg


async def get_card_by_id(id: int) -> Ygo_Card:
    url = f"http://{meilisearch_host}:7700/indexes/d_cards_version4/documents/{id}"

    headers = {
        'Content-Type': 'application/json'
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            ret = await resp.json()
            return Ygo_Card(id, ret)


def pin_quality(url_image_url, quality):
    # 加载URL图片
    response = requests.get(url_image_url)
    url_image = Image.open(BytesIO(response.content))

    # 加载本地图片
    current_directory = os.getcwd()
    if quality == '暂未登录MD':
        return url_image_url

    local_image_path = f'{current_directory}/plugins/nonebot_plugin_masterduel/nonebot_plugin_masterduel/{quality}'
    local_image = Image.open(local_image_path)
    local_image.thumbnail((local_image.width * 0.5, local_image.height * 0.5))

    # 计算拼接后的图像大小
    result_width = url_image.width
    result_height = url_image.height + local_image.height

    # 创建空白图像作为拼接结果
    result_image = Image.new('RGB', (result_width, result_height))

    # 将本地图片粘贴到拼接结果的顶部
    result_image.paste(local_image, (url_image.width - local_image.width, 0))

    # 将URL图片粘贴到拼接结果的底部
    result_image.paste(url_image, (0, local_image.height))

    # 将拼接后的图像转换为Base64格式
    buffered = BytesIO()
    result_image.save(buffered, format='JPEG')
    image_base64 = base64.b64encode(buffered.getvalue()).decode()

    return "base64://" + image_base64


master_duel_help = on_regex(pattern="^ygohelp$")


@master_duel_help.handle()
async def master_duel_help_rev(bot: Bot, event: Event):
    msg = """
    1.ygo 闪刀                                 
    描述: 模糊查询闪刀字样的卡
    
    2.ygogpt 名字带有闪刀的炎属性怪兽             
    描述: 通过gpt查询游戏王卡
    例子1: 名字带有刀的同调怪兽
    例子2: 一只又是超量又是灵摆的暗属性龙族怪兽
    例子3: 等级等于8的龙族怪兽
    例子4: 名字中带有闪刀的永续魔法 

    
    3.ygoalias id 火刀            
    描述: 卡片别名设置, id来自查询卡的名字后的那个id,然后查询就先匹配别名
    
    4.ygos attribute=暗属性|type=怪兽卡|monsters_type=超量|monsters_type=灵摆          
    描述: 精确查找
    attribute = 是怪兽时: 火属性,水属性,光属性, 非怪兽时: 场地魔法, 永续魔法, 速攻魔法, 反击陷阱, 永续陷阱 
    type = 怪兽卡, 陷阱卡, 魔法卡
    monsters_type = 超量, 融合, LINK, 灵摆, 通常, 二重, 灵魂, 同调
    race = 龙族, 战士族
    ATK = 输入数字
    DEF = 输入数字
    lv = 输入数字
    q = 模糊搜索条件
    例子一: lv=1|type=怪兽卡|attribute=炎属性|type=怪兽卡|q=闪刀
    查询结果:  火刀
    """
    await bot.send(event=event, message=MessageSegment.text(msg))
