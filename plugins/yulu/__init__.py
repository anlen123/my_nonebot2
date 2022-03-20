import nonebot
from nonebot.plugin import on_regex, on_startswith
from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent, MessageSegment, Message
import random as rd
import os, time, uuid, re, requests
from datetime import datetime
from typing import List

global_config = nonebot.get_driver().config
config = global_config.dict()
pathHome = os.environ["HOME"]
imgRoot = config.get('imgroot', pathHome)

export = nonebot.require("nonebot_plugin_navicat")
clien = export.redis_client  # redis的

yulu = on_regex("^语录$|^yulu$|^yl$|^来点语录$")


# 识别参数 并且给state 赋值
@yulu.handle()
async def yulu_rev(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id
    # 新建群组的文件夹
    path_prefix = f"{imgRoot}QQbotFiles/yulu/{group_id}/"
    if not os.path.exists(path_prefix):
        os.makedirs(path_prefix)

    img_list = await get_all_yl(event.group_id)
    if not img_list:
        await yulu.finish("语录库已经空了")
    else:
        rd.seed(time.time())
        path = img_list[rd.randint(0, len(img_list) - 1)]
        day = time.strftime("%m-%d")
        clien.hincrby(f"{day}:yulu", path, 1)
        await bot.send(event=event,
                       message=MessageSegment.image(await get_img_url(f"{path}")) + f"rmyl {path.split('/')[-1]}")


yulu_save = on_regex("^上传语录$")


@yulu_save.handle()
async def yulu_save_handle(event: Event):
    msg = event.message
    if str(msg) != "上传语录":
        await yulu_save.finish("后面不加参数,直接at我后,输入\"上传语录\"即可.")


@yulu_save.got(key="url", prompt="请输入图片")
async def yulu_save_got(event: GroupMessageEvent):
    msg = event.message
    url = msg[0].data['url']
    group_id = event.group_id
    today = await get_Y_M_D()
    path_prefix = f"{imgRoot}QQbotFiles/yulu/{group_id}/{today}/"
    if not os.path.exists(path_prefix):
        os.makedirs(path_prefix)
    if url:
        r = requests.get(url)
        with open(f"{imgRoot}QQbotFiles/yulu/{group_id}/{today}/{today}-{uuid.uuid4()}.png", mode="wb") as f:
            f.write(r.content)
        await yulu_save.finish("上传成功!!!")
    else:
        await yulu_save.finish("好像出错了!!!")


ylRank = on_regex(pattern="^ylRank$")


@ylRank.handle()
async def ylRank(bot: Bot, event: GroupMessageEvent):
    day = time.strftime("%m-%d")
    yuluMsg = eval(str(clien.hgetall(f"{day}:yulu")))
    yuluMsg = sorted(yuluMsg.items(), key=lambda k: -int(k[1]))[:3]
    msg = "今日语录排行榜\n"
    for index, x in enumerate(yuluMsg):
        count = re.findall("\(b'(.*?)',\ b'(.*?)'\)", str(x))[0]
        msg += f"第{index + 1}名: 出现次数:{count[1]} [CQ:image,file=file:///{imgRoot}QQbotFiles/yulu/{count[0]}]\n"
    await bot.send(event=event, message=Message(msg))


del_yl_img = on_regex(pattern="^rmyl\ ")


@del_yl_img.handle()
async def del_img_handle(event: GroupMessageEvent):
    msg = str(event.message).strip()[5:]
    if msg.endswith("png") or msg.endswith("jpg") or msg.endswith("jpeg"):
        img_list = await get_all_yl(event.group_id)
        for img in img_list:
            if img.endswith(msg):
                os.system(f"rm {img}")
                await del_yl_img.finish("语录成功删除")
                break
        await del_yl_img.finish("语录删除失败")
    else:
        await del_yl_img.finish('错误参数,例子: rmyl 1.jpg')


async def get_img_url(path: str) -> str:
    return "file:///" + path


async def get_img_list(path):
    return os.listdir(f"{path}")


async def get_Y_M_D() -> str:
    year = datetime.now().year
    month = datetime.now().month
    day = datetime.now().day
    return f"{year}_{month}_{day}"


async def get_all_yl(group_id) -> List[str]:
    path_prefix = f"{imgRoot}QQbotFiles/yulu/{group_id}/"
    queue = []
    ret = []
    queue += [path_prefix + _ for _ in os.listdir(path_prefix) if os.path.isdir(path_prefix + _)]
    ret += [path_prefix + _ for _ in os.listdir(path_prefix) if os.path.isfile(path_prefix + _)]
    while len(queue) != 0:
        top = queue.pop(0)
        queue += [top + "/" + _ for _ in os.listdir(top) if os.path.isdir(top + "/" + _)]
        ret += [top + "/" + _ for _ in os.listdir(top) if os.path.isfile(top + "/" + _)]
    return ret
