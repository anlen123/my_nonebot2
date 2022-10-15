import nonebot
from nonebot.plugin import on_regex, on_startswith, on_keyword
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment, Message, GroupMessageEvent
import os, uuid, requests, time
import random as rd
from datetime import datetime

global_config = nonebot.get_driver().config
config = global_config.dict()
pathHome = os.environ["HOME"]
imgRoot = config.get('imgroot', pathHome)

export = nonebot.require("nonebot_plugin_navicat")
clien = export.redis_client  # redis的

pathHome = imgRoot + "QQbotFiles/img"
if not os.path.exists(pathHome):
    os.makedirs(pathHome)

setu = on_regex("^st$|^cu$|^涩图$|^来点涩图$")


@setu.handle()
async def setu_rev(bot: Bot, event: GroupMessageEvent):
    path_prefix = f"{imgRoot}QQbotFiles/img/"
    img_list = await get_img_list(path_prefix)
    if not img_list:
        await setu.finish("色图库已经空了")
    else:
        rd.seed(time.time())
        path = img_list[rd.randint(0, len(img_list) - 1)]
        path_temp = path.replace(imgRoot+"QQbotFiles/img/","")
        try:
            await bot.send(event=event, message=MessageSegment.image(await get_img_url(path)) + f"rm {path_temp}")
        except:
            os.system(f"echo '1' >> {path_temp}")
            await bot.send(event=event, message=MessageSegment.image(await get_img_url(path)) + f"rm {path_temp}")



del_img = on_regex(pattern="^rm\ ")


@del_img.handle()
async def del_img_handle(event: GroupMessageEvent):
    msg = str(event.message).strip()[3:]
    path_prefix = f"{imgRoot}QQbotFiles/img/"
    path_yulu_prefix = f"{imgRoot}QQbotFiles/yulu/"
    path_threeciyuan_prefix = f"{imgRoot}QQbotFiles/3c/"
    path_nsfw_prefix = f"{imgRoot}QQbotFiles/nsfw/"
    if len(msg) >= 1:
        if msg.endswith("png") or msg.endswith("jpg") or msg.endswith("jpeg"):
            print(msg)
            print(f"rm {path_prefix}{msg}")
            os.system(f"rm {path_prefix}{msg}")
            os.system(f"rm {path_yulu_prefix}{msg}")
            os.system(f"rm {path_threeciyuan_prefix}{msg}")
            os.system(f"rm {path_nsfw_prefix}{msg}")
            await del_img.finish("成功删除")
    else:
        await del_img.finish('错误参数,例子: rm 1.jpg')

clear_redis = on_regex(pattern="^redis_clear$")


@clear_redis.handle()
async def clear_redis_handle(event: GroupMessageEvent):
    clien.delete(f"{imgRoot}QQbotFiles/nsfw/")
    print(f"{imgRoot}QQbotFiles/nsfw/")
    await clear_redis.finish('redis清除成功')


save = on_regex(pattern="^上传色图$")


@save.handle()
async def save_handle(event: GroupMessageEvent):
    msg = event.message
    if str(msg) != "上传色图":
        await save.finish("后面不加参数,直接at我后,输入\"上传色图\"即可.")


@save.got(key="url", prompt="请输入图片")
async def save_got(event: GroupMessageEvent):
    msg = event.message
    url = msg[0].data['url']
    if url:
        r = requests.get(url)
        with open(f"{imgRoot}QQbotFiles/img/{uuid.uuid4()}.png", mode="wb") as f:
            f.write(r.content)
        await save.finish("上传成功!!!")
    else:
        await save.finish("好像出错了!!!")


not_se = on_keyword({"不够色", "不够涩"})


@not_se.handle()
async def not_se_handle():
    await not_se.finish("反正我比另一个机器人涩!!!")


async def get_img_url(path: str) -> str:
    return "file:///" + path


async def get_img_list(path):
    print("获取文件夹图片")
    root_path = path
    temp_files = clien.get(f'{root_path}')
    if temp_files:
        print("有redis缓存了")
        return eval(temp_files)
    files = []
    folders = [f"{path}"]
    while folders:
        rd.shuffle(folders)
        pre = folders.pop(0)
        for path in os.listdir(pre):
            if os.path.isdir(pre + "/" + path):
                folders.append(pre + "/" + path)
            else:
                file = pre + "/"  + path
                if file.endswith("png") or file.endswith("jpg") or file.endswith("jpeg"):
                    files.append(file)
                    if len(files) >= 2000:
                        print("没有redis缓存")
                        clien.set(f'{root_path}',str(files))
                        return files
    print("没有redis缓存")
    clien.set(f'{root_path}',str(files))
    return files


threeciyuan = on_regex("^3c$|^3次元$")


# 识别参数 并且给state 赋值
@threeciyuan.handle()
async def threeciyuan_rep(bot: Bot, event: GroupMessageEvent):
    path_prefix = f"{imgRoot}QQbotFiles/3c/"
    img_list = await get_img_list(path_prefix)
    if not img_list:
        await threeciyuan.finish("3次元库已经空了")
    else:
        path = rd.choice(img_list)
        img_path = await get_img_url(path)
        await send_forward_msg_group(bot, event, "qqbot", [MessageSegment.image(img_path)])


async def get_Y_M_D() -> str:
    year = datetime.now().year
    month = datetime.now().month
    day = datetime.now().day
    return f"{year}_{month}_{day}"


# 合并消息
async def send_forward_msg_group(
        bot: Bot,
        event: GroupMessageEvent,
        name: str,
        msgs: [],
):
    def to_json(msg):
        return {"type": "node", "data": {"name": name, "uin": bot.self_id, "content": msg}}

    messages = [to_json(msg) for msg in msgs]
    await bot.call_api(
        "send_group_forward_msg", group_id=event.group_id, messages=messages
    )


nsfw = on_regex("^nsfw$")


# 识别参数 并且给state 赋值
@nsfw.handle()
async def nsfw_rep(bot: Bot, event: GroupMessageEvent):
    path_prefix = f"{imgRoot}QQbotFiles/nsfw/"
    img_list = await get_img_list(path_prefix)
    if not img_list:
        await nsfw.finish("nsfw已经空了")
    else:
        path = rd.choice(img_list)
        img_path = await get_img_url(path)
        print(img_path)
        try:
            await send_forward_msg_group(bot, event, "qqbot", [MessageSegment.image(img_path)])
        except:
            os.system(f"echo '1' >> {img_path}")
            await send_forward_msg_group(bot, event, "qqbot", [MessageSegment.image(img_path)])


