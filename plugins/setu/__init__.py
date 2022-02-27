from pathlib import Path
from nonebot import on_command
from nonebot.plugin import on_regex, on_shell_command, on_startswith,on_keyword
from nonebot.rule import to_me
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment, Message, GroupMessageEvent
from nonebot.params import T_State,State
import os
import random as rd
import uuid
import re
import requests
import nonebot
import time 

_sub_plugins = set()
_sub_plugins |= nonebot.load_plugins(
    str((Path(__file__).parent / "plugins").
    resolve()))

global_config = nonebot.get_driver().config
imgRoot=global_config.dict()['imgroot']

def pingbi(event:Event)->bool:
    goup_id = event.dict().get('group_id')
    if goup_id:
        if str(goup_id) in ['389410891']:
            return True
    return False
export = nonebot.require("nonebot_plugin_navicat")
clien = export.redis_client # redis的

pathHome = imgRoot+"QQbotFiles/img" 
if not os.path.exists(pathHome):
    os.makedirs(pathHome)

setu = on_regex("^st$|^cu$|^涩图$|^来站涩图$")

@setu.handle()
async def setu_rev(bot: Bot, event: Event, state: T_State=State()):
    if pingbi(event):
        return 
    path_prefix = f"{imgRoot}QQbotFiles/img/"
    img_list = await get_img_list(path_prefix)
    if not img_list:
        await setu.finish("色图库已经空了")
    else:
        rd.seed(time.time())
        path = img_list[rd.randint(0, len(img_list)-1)]
        await bot.send(event=event, message=MessageSegment.image(await get_img_url(path_prefix + path)) + f"rm {path}")


del_img = on_startswith(msg="rm")

@del_img.handle()
async def del_img_handle(bot: Bot, event: Event, state: T_State=State()):
    msg = str(event.message).strip().split(" ")[1:]
    path_prefix = f"{imgRoot}QQbotFiles/img/"
    path_yulu_prefix = f"{imgRoot}QQbotFiles/yulu/"
    path_threeciyuan_prefix = f"{imgRoot}QQbotFiles/3c/"
    if len(msg) == 1:
        if msg[0].endswith("png") or msg[0].endswith("jpg") or msg[0].endswith("jpeg"):
            print(f"rm {path_prefix}{msg[0]}")
            os.system(f"rm {path_prefix}{msg[0]}")
            os.system(f"rm {path_yulu_prefix}{msg[0]}")
            os.system(f"rm {path_threeciyuan_prefix}{msg[0]}")
            await del_img.finish("成功删除")
    else:
        await del_img.finish('错误参数,例子: rm 1.jpg')


async def get_img_url(path: str) -> str:
    return "file:///" + path


async def get_img_list(path):
    return os.listdir(f"{path}")


update_file = on_keyword(set(["更新图库","更新语录","更新色图"]),rule=to_me())


@update_file.handle()
async def update_file_handle(bot: Bot, event: Event, state: T_State=State()):
    if pingbi(event):
        return 
    os.system(f"{imgRoot}QQbotFiles/QQbotFiles_update.sh")
    await update_file.finish("图库更新完成")


save = on_regex(pattern="^上传色图$")


@save.handle()
async def save_handle(bot: Bot, event: Event, state: T_State=State()):
    if pingbi(event):
        return 
    msg = event.message
    if str(msg)!="上传色图":
        await save.finish("后面不加参数,直接at我后,输入\"上传色图\"即可.")


@save.got(key="url", prompt="请输入图片")
async def save_got(bot: Bot, event: Event, state: T_State=State()):
    msg = event.message
    url = msg[0].data['url']
    if url:
        r = requests.get(url)
        with open(f"{imgRoot}QQbotFiles/img/{uuid.uuid4()}.png", mode="wb") as f:
            f.write(r.content)
        await save.finish("上传成功!!!")
    else:
        await save.finish("好像出错了!!!")


bugouse = on_keyword(set(["不够色", "不够涩"]))


@bugouse.handle()
async def bugouse_handle(bot: Bot, event: Event, state: T_State=State()):
    await bugouse.finish("反正我比另一个机器人涩!!!")


yulu = on_regex("^语录$|^yulu$|^yl$|^来点语录$")

# 识别参数 并且给state 赋值
@yulu.handle()
async def yulu_rev(bot: Bot, event: GroupMessageEvent, state: T_State=State()):
    group_id = str(event.dict().get('group_id'))
    if pingbi(event):
        return 
    path_prefix = f"{imgRoot}QQbotFiles/yulu/{group_id}/"
    if not os.path.exists(path_prefix):
        os.makedirs(path_prefix)
    img_list = await get_img_list(path_prefix)
    if not img_list:
        await yulu.finish("语录库已经空了")
    else:
        rd.seed(time.time())
        path = img_list[rd.randint(0, len(img_list)-1)]
        day = time.strftime("%m-%d")
        clien.hincrby(f"{day}:yulu",path,1)
        await bot.send(event=event, message=MessageSegment.image(await get_img_url(path_prefix + path)) + f"rm {path}")


yulu_save = on_regex("^上传语录$")


@yulu_save.handle()
async def yulu_save_handle(bot: Bot, event: Event, state: T_State=State()):
    if pingbi(event):
        return 
    msg = event.message
    if str(msg)!="上传语录":
        await yulu_save.finish("后面不加参数,直接at我后,输入\"上传语录\"即可.")


@yulu_save.got(key="url", prompt="请输入图片")
async def yulu_save_got(bot: Bot, event: GroupMessageEvent, state: T_State=State()):
    msg = event.message
    url = msg[0].data['url']
    group_id = str(event.dict().get('group_id'))
    path_prefix = f"{imgRoot}QQbotFiles/yulu/{group_id}/"
    if not os.path.exists(path_prefix):
        os.makedirs(path_prefix)
    if url:
        r = requests.get(url)
        with open(f"{imgRoot}QQbotFiles/yulu/{group_id}/{uuid.uuid4()}.png", mode="wb") as f:
            f.write(r.content)
        await yulu_save.finish("上传成功!!!")
    else:
        await yulu_save.finish("好像出错了!!!")

threeciyuan = on_regex("^3c$|^3次元$")


# 识别参数 并且给state 赋值
@threeciyuan.handle()
async def threeciyuan_rep(bot: Bot, event: GroupMessageEvent, state: T_State=State()):
    if pingbi(event):
        return 
    path_prefix = f"{imgRoot}QQbotFiles/3c/"
    img_list = await get_img_list(path_prefix)
    if not img_list:
        await yulu.finish("3次元库已经空了")
    else:
        path = rd.choice(img_list)
        await bot.send(event=event, message=MessageSegment.image(await get_img_url(path_prefix + path)) + f"rm {path}")
threeciyuan_save = on_keyword(set(["上传真人"]))


@threeciyuan_save.handle()
async def yulu_save_handle(bot: Bot, event: Event, state: T_State=State()):
    if pingbi(event):
        return 
    msg = event.message
    if msg:
        await yulu_save.finish("后面不加参数,直接at我后,输入\"上传真人\"即可.")


@threeciyuan_save.got(key="url", prompt="请输入图片")
async def yulu_save_got(bot: Bot, event: Event, state: T_State=State()):
    msg = str(event.message)
    # print(msg)
    url = re.findall("\[CQ:image,file=.*?,url=(.*?)\]", msg)
    if url:
        state['url'] = url[0]
        # print(url[0])
        r = requests.get(url[0])
        with open(f"{imgRoot}QQbotFiles/3c/{uuid.uuid4()}.png", mode="wb") as f:
            f.write(r.content)
        await yulu_save.finish("上传成功!!!")
    else:
        await yulu_save.finish("好像出错了!!!")


ylRank = on_regex(pattern="^ylRank$")


@ylRank.handle()
async def ylRank(bot: Bot, event: Event, state: T_State=State()):
    # user = str(event.dict()['sender']['user_id'])+ ":"+str(event.dict()['sender']['nickname'])
    # clien.hincrby("rank",user,1)
    day = time.strftime("%m-%d")
    yuluMsg = eval(str(clien.hgetall(f"{day}:yulu")))
    yuluMsg = sorted(yuluMsg.items(),key=lambda k:-int(k[1]))[:3]
    print(yuluMsg)
    msg = "今日语录排行榜\n"
    for index,x in enumerate(yuluMsg):
        count = re.findall("\(b'(.*?)',\ b'(.*?)'\)",str(x))[0]
        msg+=f"第{index+1}名: 出现次数:{count[1]} [CQ:image,file=file:///{imgRoot}QQbotFiles/yulu/{count[0]}]\n"
    await bot.send(event=event,message=Message(msg))
