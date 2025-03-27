import yaml
import time
import os
import jmcomic
from PIL import Image
from pathlib import Path

import nonebot
from typing import List
from nonebot import get_driver
from nonebot import on_command, on_startswith, on_keyword, on_message
from nonebot.plugin import on_notice, on_regex
from nonebot.rule import Rule, regex, to_me
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment, Message, GroupMessageEvent
from nonebot.params import T_State
import asyncio
import re

jm = on_regex(pattern="^(jm) ")

@jm.handle()
async def jm_rev(bot: Bot, event: Event):
    if isinstance(event, GroupMessageEvent):
        group_id = event.group_id
        lock_file = f"jm_{group_id}.lock"
        if os.path.exists(lock_file):
            await bot.send(event, "jm功能已关闭，开启后请重新发送jm命令")
            return
    # 获取用户输入的JM ID
    jm_id = event.get_plaintext()[3:].strip()

    # ID有效性验证
    if not jm_id.isnumeric():
        await bot.send(event, "🚫 ID格式错误！请输入6位以上数字的JM作品ID")
        return

    # 定义锁文件路径
    lock_file = f"jm_task_{jm_id}.lock"

    try:
        # 尝试创建锁文件（带3次重试）
        for retry in range(3):
            try:
                fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                break
            except FileExistsError:
                if retry == 2:
                    await bot.send(event, "⏳ 当前有正在进行的下载任务，请稍后查询进度")
                    return
                await asyncio.sleep(2)

        await bot.send(event, f"🛠️ 开始处理 JM{jm_id}：\n"
                                        "▫️ 正在连接下载服务器...\n"
                                        "▫️ 预计需要3-5分钟，请稍候")

        # 记录开始时间
        start_time = time.time()

        # 执行核心任务
        try:
            file_name = load_pdf(jm_id)
            file_path = f"D:/nb2/imgroot/QQbotFiles/jm_book/{file_name}"
        except Exception as e:
            raise RuntimeError(f"文件生成失败: {str(e)}")

        # 验证文件生成
        if not os.path.exists(file_path):
            raise FileNotFoundError("生成文件未找到")

        # 上传文件到群
        try:
            msg = await bot.call_api(
                "upload_group_file",
                group_id=event.group_id,
                file=file_path,
                name=f"{file_name}"
            )
            print(msg)
        except Exception as e:
            raise RuntimeError(f"文件上传失败: {str(e)}")

        # 计算耗时
        duration = time.time() - start_time

        # 更新进度为完成
        await bot.send(
            event=event,
            message=f"✅ 处理完成 JM{jm_id}：\n"
                    f"▫️ 文件名称：{file_name}.pdf\n"
                    f"▫️ 处理耗时：{duration:.1f}秒\n"
                    "📢 文件已成功上传至群文件"
        )

    except Exception as e:
        # 错误处理
        error_msg = (
            f"❌ 处理 JM{jm_id} 失败：\n"
            f"▫️ 错误原因：{str(e)}\n"
            "🔧 建议操作：\n"
            "1. 检查ID是否正确\n"
            "2. 等待10分钟后重试\n"
            "3. 联系管理员查看服务器日志"
        )
        await bot.send(event = event, message=error_msg)
    finally:
        # 清理锁文件
        try:
            os.remove(lock_file)
        except:
            pass

def all2PDF(input_folder, pdfpath, pdfname):
    start_time = time.time()
    paht = input_folder
    zimulu = []  # 子目录（里面为image）
    image = []  # 子目录图集
    sources = []  # pdf格式的图

    with os.scandir(paht) as entries:
        for entry in entries:
            if entry.is_dir():
                zimulu.append(int(entry.name))
    # 对数字进行排序
    zimulu.sort()

    for i in zimulu:
        with os.scandir(paht + "/" + str(i)) as entries:
            for entry in entries:
                if entry.is_dir():
                    print("这一级不应该有自录")
                if entry.is_file():
                    image.append(paht + "/" + str(i) + "/" + entry.name)

    if "jpg" in image[0]:
        output = Image.open(image[0])
        image.pop(0)

    for file in image:
        if "jpg" in file:
            img_file = Image.open(file)
            if img_file.mode == "RGB":
                img_file = img_file.convert("RGB")
            sources.append(img_file)

    pdf_file_path = pdfpath + "/" + pdfname
    if pdf_file_path.endswith(".pdf") == False:
        pdf_file_path = pdf_file_path + ".pdf"
    output.save(pdf_file_path, "pdf", save_all=True, append_images=sources)
    end_time = time.time()
    run_time = end_time - start_time
    print("运行时间：%3.2f 秒" % run_time)


def load_pdf(id: str):
    
    # 自定义设置：
    config = "D:/nb2/my_nonebot2/plugins/jm/config.yml"
    loadConfig = jmcomic.JmOption.from_file(config)
    # 如果需要下载，则取消以下注释
    manhua = [id]
    for id in manhua:
        jmcomic.download_album(id, loadConfig)

    with open(config, "r", encoding="utf8") as f:
        data = yaml.load(f, Loader=yaml.FullLoader)
        path = data["dir_rule"]["base_dir"]

    with os.scandir(path) as entries:
        for entry in entries:
            if entry.is_dir():
                if os.path.exists(os.path.join(path + '/' + entry.name + ".pdf")):
                    print("文件：《%s》 已存在，跳过" % entry.name)
                else:
                    print("开始转换：%s " % entry.name)
                    all2PDF(path + "/" + entry.name, path, entry.name)
    return get_latest_pdf(path)

# 获取最新PDF文件
def get_latest_pdf(path) -> str:
    pdf_files = []
    for entry in os.scandir(path):
        if entry.is_file() and entry.name.endswith(".pdf"):
            mtime = entry.stat().st_mtime
            pdf_files.append((mtime, entry.name))

    if not pdf_files:
        raise FileNotFoundError("未找到PDF文件")

    # 按修改时间降序排序
    pdf_files.sort(reverse=True, key=lambda x: x[0])
    return pdf_files[0][1]



# 合并消息
async def send_forward_msg_group(bot: Bot, event: GroupMessageEvent, name: str, msgs: List[str], ):
    def to_json(msg):
        return {"type": "node", "data": {"name": name, "uin": bot.self_id, "content": msg}}

    messages = [to_json(msg) for msg in msgs]
    await bot.call_api("send_group_forward_msg", group_id=event.group_id, messages=messages)
    
    
jm_close = on_regex(pattern="^(关闭jm功能)$")


@jm_close.handle()
async def jm_close_rev(bot: Bot, event: Event):
    if int(event.get_user_id()) not in (1928906357,1761512493):
        await bot.send(event, "你没有权限关闭jm功能")
        return
    if isinstance(event,GroupMessageEvent):
        group_id = event.group_id
        lock_file = f"jm_{group_id}.lock"
        fd = os.open(lock_file, os.O_CREAT |os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        await bot.send(event,"已关闭jm功能")

jm_open = on_regex(pattern="^(开启jm功能)$")


@jm_open.handle()
async def jm_open_rev(bot: Bot, event: Event):
    if int(event.get_user_id()) not in (1928906357, 1761512493):
        await bot.send(event, "你没有权限开启jm功能")
        return
    if isinstance(event, GroupMessageEvent):
        group_id = event.group_id
        lock_file = f"jm_{group_id}.lock"
        os.remove(lock_file)
        await bot.send(event, "已开启jm功能")