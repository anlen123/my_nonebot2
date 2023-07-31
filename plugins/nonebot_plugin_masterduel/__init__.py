from typing import List, Any
from .config import Config
from nonebot.plugin import on_notice, on_regex
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment, Message, GroupMessageEvent
import aiohttp, asyncio, json, nonebot, re, httpx

master_duel = on_regex(pattern="^ck ")


@master_duel.handle()
async def master_duel_rev(bot: Bot, event: Event):
    content = str(event.get_plaintext()[3:]).strip()
    print(content)
    number = 1
    # number = str(content.split(" ")[-1])
    # if is_int(number):
    #     if int(number) > 5:
    #         number = 5
    #     else:
    #         number = int(number)
    # else:
    #     if number == 'all':
    #         number = 99999
    #     else:
    #         number = 1
    ret_by_alias = await search_card_by_alias(content)
    hits_by_alias = ret_by_alias.get('hits')
    msg = get_send_msg(hits_by_alias, 1)

    if msg:
        msg += MessageSegment.text("--------别名精准搜索")
        await bot.send(event, msg)
        return

    ret = await search_card(content, 2)
    hits = ret.get('hits')
    try:
        msg = get_send_msg(hits, number)
        if msg:
            msg += MessageSegment.text("--------全文精确搜索")
            await bot.send(event, msg)
        else:
            ret2 = await search_card(content, 1)
            hits_2 = ret2.get('hits')
            msg = get_send_msg(hits_2, number)
            if msg:
                msg += MessageSegment.text("--------分词模糊搜索")
                await bot.send(event, msg)
            else:
                await bot.send(event, MessageSegment.text("没有查询到卡片"))
    except nonebot.adapters.onebot.v11.exception.ActionFailed as e:
        await bot.send(event, MessageSegment.text("帐号风控了！！无语住"))


def get_send_msg(hits, number: int):
    msg = []
    if hits:
        msg: List[MessageSegment] = []
    else:
        return msg
    for _ in hits[:number]:
        if _:
            msg += ("名称：" + MessageSegment.text(_['name']) + f"({_['id']})\n"
                    + MessageSegment.text(_['attribute']) + "\n"
                    + MessageSegment.image(_['img']) + "\n"
                    + "召唤条件和效果：" + _['desc'])
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
    url = "http://localhost:7700/indexes/d_cards_version3/documents"

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
    url = "http://localhost:7700/indexes/d_cards_version3/search"
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
    url = "http://localhost:7700/indexes/d_cards_version3/search"

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


master_duel_ck_gpt = on_regex(pattern="^ckgpt ")


@master_duel_ck_gpt.handle()
async def master_duel_ck_gpt_rev(bot: Bot, event: Event):
    content = str(event.get_plaintext()[6:]).strip()
    print(content)
    content = await gpt3(content)
    print(content)
    ret = await search_card_by_gpt(content)
    hits = ret.get('hits')
    msg = get_send_msg(hits, 1)
    if msg:
        msg += MessageSegment.text(f"--------gpt搜索({content})")
        await bot.send(event, msg)
    else:
        await bot.send(event, MessageSegment.text("没有查询到卡片"))


async def search_card_by_gpt(content: str):
    s = "ATK>1000|DEF<100|attibution=光属性|race=龙族"
    url = "http://localhost:7700/indexes/d_cards_version3/search"
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


master_duel_alias = on_regex(pattern="^ckalias\ \d+\ ")


@master_duel_alias.handle()
async def master_duel_alias_rev(bot: Bot, event: Event):
    content = str(event.get_plaintext()[8:]).strip()
    print(content)
    content = content.split(" ")
    id = content[0]
    alias = content[1]
    await alias_name(id, alias)
    await bot.send(event, MessageSegment.text("别名命名成功"))


async def gpt3(ask: str) -> str:
    api_key = "Zc8xBwdP1eg6i7s_qnE3QJkxAu8_SDZ4VGAqV0e5pf4"
    url = "https://chimeragpt.adventblocks.cc/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    rule = """
给你一个json串表示自然语言的描述对应的类型，尝试理解并消化给你的例子再帮我转换为特定格式的字符串, 并把转换结果用|分割，只用输出结果即可，不用任何解释。
{
"攻击力": "ATK",
"防御力": "DEF",
"属性": "attibution",
"族": "race",
}
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
}
例子1:  攻击力大于1000 -> ATK>1000
例子2: 龙族 -> race=龙族
例子3: 光属性->attibution=光属性
例子4: 防御力小于3000 -> DEF<3000
例子5: 永续陷阱卡 -> type=陷阱卡|attribute=永续陷阱
例子6: 速攻魔法卡 -> type=魔法卡|attribute=速攻魔法
例子7: 场地魔法卡 -> type=魔法卡|attribute=场地魔法
例子8: 等级大于10 -> lv>10
例子9: 等级等于8的龙族怪兽 -> lv=8|race=龙族|type=怪兽卡
例子10: link5的炎属性怪兽 -> lv=5|attribute=炎属性|type=怪兽卡
例子11: 名字中带有闪刀的永续魔法 -> type=魔法卡|attribute=永续魔法|q=闪刀'
例子12: 一只光属性link2的怪兽 -> lv=2|attribute=光属性|type=怪兽卡
例子13: 龙link的反击陷阱卡 -> type=陷阱卡|attribute=反击陷阱|name=龙link
"""
    data = {
        "model": "gpt-3.5-turbo-16k",
        "messages": [
            {
                "role": "system",
                "content": f"{rule}"
            },
            {
                "role": "user",
                "content": f"输入: {ask}"
            }
        ]
    }
    ci = 0

    while ci < 4:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, headers=headers, json=data)
                result = response.json()
                for message in result["choices"]:
                    message = message.get('message')
                    if message:
                        if message["role"] == "assistant":
                            ret = message.get('content')
                            print(ret)
                            return ret
                    else:
                        return "网络超时，稍后重试！！"
        except:
            ci += 1
