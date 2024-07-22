import os
import random
import re, os
import time

from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment, Message, GroupMessageEvent, MessageEvent
from nonebot.plugin import on_regex, on_message
from nonebot.rule import Rule, to_me
from nonebot.typing import T_State
from nonebot.params import Depends

from .config import Config
from .config import Config
from .mapper import *
from .model.Card import YgoCard
from .utils import imgUtils
from .utils import rarityUtils

from nonebot_plugin_waiter import waiter
from nonebot_plugin_userinfo import get_user_info

global_config = nonebot.get_driver().config
config = global_config.dict()

nonebot_plugin_masterduel_img_dir = config.get('nonebot_plugin_masterduel_img_dir')
nonebot_plugin_masterduel_root_dir = config.get('nonebot_plugin_masterduel_root_dir')

master_duel = on_regex(pattern="^ck")
master_duel_CK = on_regex(pattern="^CK")


@master_duel.handle()
@master_duel_CK.handle()
async def master_duel_rev(bot: Bot, event: Event):
    cmd = event.get_plaintext()[2:]
    cmd = cmd.strip()
    ygoCard = get_ygo(cmd)
    await send_card(bot, event, ygoCard)


def get_ygo(cmd: str):
    if not cmd:
        return
    ygoCard = None
    if cmd.isdigit():
        ygoCard = mapper.get_card_info_by_id(int(cmd))

    if not ygoCard:
        ygoCard = mapper.get_card_info_by_alias(cmd)
        if not ygoCard:
            cardId = get_max_like_id(cmd)
            if cardId:
                ygoCard = get_card_info_by_id(cardId)
    return ygoCard


async def send_card(bot: Bot, event: Event, ygoCard: YgoCard, pack: bool = True, desc: bool = True):
    if ygoCard:
        messageSegment = await get_send_msg(ygoCard, pack, desc)
        await retry_send(bot, event, messageSegment)
        # await bot.send(event, messageSegment)
        directory = f"{nonebot_plugin_masterduel_img_dir}\\{ygoCard.id}"
        if os.path.exists(directory):
            files = os.listdir(directory)
            print(directory)
            print(random.choice(files))
            file = directory + "\\" + random.choice(files)
            print(file)
            await retry_send(bot, event,
                             MessageSegment.image(file))  # await bot.send(event, MessageSegment.image(file))

    else:
        await retry_send(bot, event, MessageSegment.text("未查询到卡片"))


async def get_send_msg(card: YgoCard, pack: bool = True, desc: bool = True) -> MessageSegment:
    pack_name = None
    rarity = rarityUtils.get_rarity(card.id)
    urlBase64 = imgUtils.pin_quality(int(card.id), rarity)
    sale_time = None
    if pack:
        sale_time, count_sum, pack_name = get_selas_time_by_id(card.id)
    print(pack)
    print(sale_time)
    print(desc)
    msg = MessageSegment.text(f"卡号：{card.id}\n")
    if pack:
        msg += MessageSegment.text(f"发售时间：{sale_time}\n卡包号：{pack_name}\n{card.name}\n")
    if not rarity:
        msg += MessageSegment.text("暂未登录MD")
    msg += MessageSegment.image(urlBase64)
    if desc:
        msg += MessageSegment.text(f"{card.desc}")
    return msg


alias_card = on_regex(pattern="^别名")
alias_card2 = on_regex(pattern="^外号")


@alias_card.handle()
@alias_card2.handle()
async def alias_card_rev(bot: Bot, event: Event):
    cmd = event.get_plaintext()[2:]
    cmd = cmd.strip()
    ags = cmd.split(" ")
    if len(ags) != 2 and is_int(ags[0]):
        await bot.send(event, "例子：别名 30459350 王宫壁")
    cardId = ags[0]
    aliasName = ags[1]
    mapper.set_card_alias_by_id(int(cardId), aliasName)
    await bot.send(event, "别名设置成功！")


def is_int(n: str):
    try:
        float_n = float(n)
        int_n = int(float_n)
    except ValueError as e:
        return False
    else:
        return float_n == int_n


master_like_duel = on_regex(pattern="^lck")


@master_like_duel.handle()
async def master_like_duel_rev(bot: Bot, event: Event):
    cmd = event.get_plaintext()[3:]
    cmd = cmd.strip()

    ygoCardList = mapper.get_card_info_like_names(cmd)
    if not ygoCardList:
        await bot.send(event, MessageSegment.text("未查询到卡片"))
        return
    ygoCardList = ygoCardList[:20]
    msgs = ""
    for card in ygoCardList:
        msgs += MessageSegment.text(f"卡号：{card.id}      {card.name}\n")
    await bot.send(event=event, message=msgs)


def push_ygo_card() -> Rule:
    async def push_ygo_card_(bot: "Bot", event: "Event") -> bool:
        if event.get_type() != "message":
            return False
        msg = str(event.get_plaintext())
        if re.findall("^上传游戏王卡图\ \d+$", msg):
            return True
        return False

    return Rule(push_ygo_card_)


master_duel_push_img = on_message(rule=push_ygo_card())


@master_duel_push_img.handle()
async def master_duel_push_img_rev(bot: Bot, event: MessageEvent, state: T_State):
    card_id = event.get_plaintext().strip().split(" ")[-1].strip()
    if card_id:
        state['card_id'] = card_id
        ygoCard = mapper.get_card_info_by_id(int(card_id))
        if not ygoCard:
            await bot.send(event=event, message="没有找到这张卡")
            state['img'] = "None"
            return


@master_duel_push_img.got("img", prompt="图呢？")
async def get_setu(bot: Bot, event: MessageEvent, state: T_State):
    if state['img'] == "None":
        return
    msg = event.get_message()
    card_id = state['card_id']
    try:
        if msg[0].type == "image":
            url = msg[0].data["url"]  # 图片链接
            imgUtils.down_img(f"{nonebot_plugin_masterduel_img_dir}\\{card_id}\\", url,
                              f"{random.randint(1, 10000000)}.jpg")
            await bot.send(event=event, message="上传成功！")
    except Exception as e:
        await bot.send(event=event, message="错误的上传图片")


my_deck = on_regex(pattern="^卡组码 ")


@my_deck.handle()
async def master_duel_rev(bot: Bot, event: Event):
    cmd = event.get_plaintext()[3:]
    cmd = cmd.strip()
    zipCard = mapper.get_card_by_md_shard_deck_code(cmd)

    if not zipCard:
        await bot.send(event=event, message="未找到卡组")
        return
    mainCardList, exCardList = zipCard
    sss = imgUtils.get_all_temp(mainCardList, exCardList)
    pngName = f"{os.getcwd()}\\{random.randint(1, 99999999999)}.png"
    imgUtils.screenshot(sss, pngName)
    print(pngName)
    await bot.send(event=event, message=MessageSegment.image(pngName))
    os.remove(pngName)


cai_ding = on_regex(pattern="^裁定")


@cai_ding.handle()
async def cai_ding_rev(bot: Bot, event: Event):
    cmd = event.get_plaintext()[2:]
    cmd = cmd.strip()
    ygoCard = mapper.get_card_info_by_id(int(cmd))
    if ygoCard:
        messageSegment = await get_send_msg(ygoCard)
        # await bot.send(event, messageSegment)
        htmlStr = get_cai_ding_html(ygoCard.id)
        pngName = f"{os.getcwd()}\\{random.randint(1, 99999999999)}.png"
        imgUtils.screenshot(htmlStr, pngName)
        print(pngName)
        await bot.send(event=event, message=messageSegment + MessageSegment.image(pngName))
        os.remove(pngName)
    else:
        await bot.send(event=event, message="请输入正确的卡密")


def chunk_string(s, chunk_size):
    return [s[i:i + chunk_size] for i in range(0, len(s), chunk_size)]


card_pack = on_regex(pattern="^卡包")


@card_pack.handle()
async def card_pack_rev(bot: Bot, event: Event):
    cmd = event.get_plaintext()[2:]
    cmd = cmd.strip()
    cardIds = mapper.get_card_by_pack(pack=cmd)
    sss = imgUtils.get_all_temp(cardIds, None)
    pngName = f"{os.getcwd()}\\{random.randint(1, 99999999999)}.png"
    imgUtils.screenshot(sss, pngName)
    print(pngName)
    await bot.send(event=event, message=MessageSegment.image(pngName))
    os.remove(pngName)


ygo_rank = on_regex(pattern="^游戏王高手排名")


@ygo_rank.handle()
async def ygo_rank_rev(bot: Bot, event: Event):
    rows = mapper.get_ygo_rank(10)
    msgs: MessageSegment = MessageSegment.text("游戏王高手排行榜\n\n")
    for row in rows:
        msgs += MessageSegment.text(f"{row[1]:<10}: {row[2]}\n")
    await bot.send(event=event, message=msgs)


cai_card = on_regex(pattern="^我是游戏王高手$")


@cai_card.handle()
async def cai_card_rev(bot: Bot, event: Event):
    if os.listdir(f"{nonebot_plugin_masterduel_img_dir}\\cai"):
        await bot.send(event=event, message="已经开了一题了，做完上一题再说")
        return
    cardAndNameList = mapper.get_id_and_name_all()
    cardId, name = random.choice(cardAndNameList)
    mapper.crop_image_from_url(cardId)
    print(cardId)
    fileUrl = f"{nonebot_plugin_masterduel_img_dir}\\cai\\{cardId}.jpg"
    try:

        await bot.send(event=event, message=MessageSegment.image(fileUrl))

        @waiter(waits=["message"])
        async def check(event_: Event):
            print("111111")
            cmd = event_.get_plaintext()
            if cmd.strip().startswith("ck") or cmd.strip().startswith("CK"):
                print("22222")
                user_info = await get_user_info(bot, event, event.get_user_id())
                return cmd[2:].strip(), user_info.user_id, user_info.user_name

        async for resp in check(timeout=180, retry=4, prompt="错误喔，请仔细看看捏"):

            if resp is None:
                try:
                    await retry_send(bot, event, MessageSegment.text("上述题目已失效 全部回答错误！！！公布答案"))
                    ygoCard = get_card_info_by_id(cardId)
                    await send_card(bot, event, ygoCard, pack=False, desc=False)
                finally:
                    pass
                break

            cmd_, user_id, user_name = resp

            ygoCard = get_ygo(cmd_)
            if ygoCard:
                try:
                    await send_card(bot, event, ygoCard, pack=False, desc=False)
                finally:
                    pass
                if int(ygoCard.id) == int(cardId):
                    mapper.ygo_rank_add(int(user_id), user_name)
                    await retry_send(bot, event,
                                     MessageSegment.text(f"牛逼啊，回答正确了 积分+1  输入\"游戏王高手排名\" 查看排名"))
                    break
        else:
            await retry_send(bot, event, MessageSegment.text("全部回答错误！！！公布答案"))
            ygoCard = get_card_info_by_id(cardId)
            try:
                await send_card(bot, event, ygoCard, pack=False, desc=False)
            except Exception as e:
                time.sleep(3)
                print(e)
                await send_card(bot, event, ygoCard, pack=False, desc=False)
    finally:
        if os.path.exists(fileUrl):
            os.remove(fileUrl)


async def retry_send(bot: Bot, event: Event, msg: MessageSegment):
    count = 1
    for x in range(5):
        try:
            await bot.send(event=event, message=msg)
            break
        except Exception as e:
            print("有可能风控")
            time.sleep(2)
            print(e, count)


# 合并消息
async def send_forward_msg_group(bot: Bot, event: GroupMessageEvent, name: str, msgs: list):
    def to_json(msg):
        return {"type": "node", "data": {"name": name, "uin": bot.self_id, "content": msg}}

    messages = [to_json(msg) for msg in msgs]
    await bot.call_api("send_group_forward_msg", group_id=event.group_id, messages=messages)
