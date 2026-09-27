"""jm ── 禁漫（JM）本子下载、转 PDF、发群文件

命令（群聊）：
  jm <作品ID>       下载该作品 → 转 PDF → 上传到群文件
  关闭jm功能         仅限管理员：关掉本群的 jm 命令
  开启jm功能         仅限管理员：重新打开

说明：
  * 同一本用 jm_task_<ID>.lock 做互斥，重复请求会被告知「稍后查询进度」；
  * 「已关闭」状态用 jm_<群号>.lock 记录，锁文件都落在进程工作目录下；
  * 下载配置读 plugins/jm/config.yml，转换目录取自它的 dir_rule.base_dir。

注意（历史遗留的绝对路径，本次未改动，详见报告）：
  _CONFIG_PATH 与 _JM_BOOK_DIR 写死为迁移前的 D:/nb2/...，而本机没有 D 盘、仓库
  实际位于 C:/Users/Administrator/Desktop/nb2/my_nonebot2 —— 也就是说这两个路径在
  本机是失效的；要真正启用，需要连同 config.yml 的 dir_rule.base_dir 一起改。
"""

from __future__ import annotations

import asyncio
import os
import time

import jmcomic
import nonebot
import yaml
from PIL import Image
from nonebot import on_regex
from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent

# ── 常量 ──────────────────────────────────────────────────────────────────────
_ADMINS = (1928906357, 1761512493)  # 允许开关 jm 功能的 QQ 号（沿用旧脚本里的硬编码）
_CONFIG_PATH = "D:/nb2/my_nonebot2/plugins/jm/config.yml"  # 下载配置，见文件头说明
_JM_BOOK_DIR = "D:/nb2/imgroot/QQbotFiles/jm_book"  # 成品目录，见文件头说明

_LOCK_RETRIES = 3  # 抢下载锁的重试次数
_LOCK_RETRY_INTERVAL = 2  # 每次重试的间隔（秒）


# ── 锁文件 ────────────────────────────────────────────────────────────────────
def _group_lock(group_id: int) -> str:
    """「本群已关闭 jm」的标记文件：存在即视为关闭。"""
    return f"jm_{group_id}.lock"


async def _try_lock(lock_file: str) -> bool:
    """独占创建锁文件，最多重试 _LOCK_RETRIES 次；抢不到返回 False。"""
    for attempt in range(_LOCK_RETRIES):
        try:
            fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if attempt >= _LOCK_RETRIES - 1:
                return False
            await asyncio.sleep(_LOCK_RETRY_INTERVAL)
        else:
            os.close(fd)
            return True
    return False  # 只有 _LOCK_RETRIES 被配成 0 才会走到这里


# ── 下载与转 PDF ──────────────────────────────────────────────────────────────
def all2PDF(input_folder: str, pdfpath: str, pdfname: str) -> None:
    """把 input_folder 下按数字命名的章节目录里的图片合成一个 PDF。

    文件名含 "jpg" 的第一张图片作为 PDF 首页，其余 jpg 依次追加，非 jpg 跳过。
    """
    start_time = time.time()
    zimulu = []  # 章节号（目录名是数字）
    image = []  # 按章节号排好的图片路径
    sources = []  # 追加进 PDF 的图片对象

    with os.scandir(input_folder) as entries:
        for entry in entries:
            if entry.is_dir():
                zimulu.append(int(entry.name))
    zimulu.sort()

    for i in zimulu:
        with os.scandir(f"{input_folder}/{i}") as entries:
            for entry in entries:
                if entry.is_dir():
                    nonebot.logger.warning(f"[jm] 这一级不应该有子目录，已忽略：{entry.path}")
                elif entry.is_file():
                    image.append(f"{input_folder}/{i}/{entry.name}")

    if "jpg" in image[0]:
        output = Image.open(image[0])
        image.pop(0)

    for file in image:
        if "jpg" in file:
            img_file = Image.open(file)
            if img_file.mode == "RGB":
                img_file = img_file.convert("RGB")
            sources.append(img_file)

    pdf_file_path = f"{pdfpath}/{pdfname}"
    if not pdf_file_path.endswith(".pdf"):
        pdf_file_path += ".pdf"
    output.save(pdf_file_path, "pdf", save_all=True, append_images=sources)

    nonebot.logger.info(f"[jm] 运行时间：{time.time() - start_time:3.2f} 秒")


def load_pdf(album_id: str) -> str:
    """下载该作品，并把下载目录下各章节目录转成 PDF，返回最新的 PDF 文件名。"""
    option = jmcomic.JmOption.from_file(_CONFIG_PATH)
    jmcomic.download_album(album_id, option)

    with open(_CONFIG_PATH, "r", encoding="utf8") as f:
        base_dir = yaml.safe_load(f)["dir_rule"]["base_dir"]

    with os.scandir(base_dir) as entries:
        for entry in entries:
            if not entry.is_dir():
                continue
            if os.path.exists(os.path.join(f"{base_dir}/{entry.name}.pdf")):
                nonebot.logger.info(f"[jm] 文件：《{entry.name}》 已存在，跳过")
                continue
            nonebot.logger.info(f"[jm] 开始转换：{entry.name} ")
            all2PDF(f"{base_dir}/{entry.name}", base_dir, entry.name)

    return get_latest_pdf(base_dir)


def get_latest_pdf(path: str) -> str:
    """返回 path 下修改时间最新的 PDF 文件名；一个都没有时抛 FileNotFoundError。"""
    pdf_files = [
        (entry.stat().st_mtime, entry.name)
        for entry in os.scandir(path)
        if entry.is_file() and entry.name.endswith(".pdf")
    ]
    if not pdf_files:
        raise FileNotFoundError("未找到PDF文件")

    pdf_files.sort(reverse=True, key=lambda item: item[0])
    return pdf_files[0][1]


# ── 命令 ──────────────────────────────────────────────────────────────────────
jm = on_regex(pattern="^(jm) ")


@jm.handle()
async def jm_rev(bot: Bot, event: Event) -> None:
    """jm <作品ID>：下载 → 转 PDF → 上传群文件。"""
    if isinstance(event, GroupMessageEvent) and os.path.exists(
        _group_lock(event.group_id)
    ):
        await bot.send(event, "jm功能已关闭，开启后请重新发送jm命令")
        return

    album_id = event.get_plaintext()[3:].strip()

    # ID有效性验证
    if not album_id.isnumeric():
        await bot.send(event, "🚫 ID格式错误！请输入6位以上数字的JM作品ID")
        return

    lock_file = f"jm_task_{album_id}.lock"
    try:
        if not await _try_lock(lock_file):
            await bot.send(event, "⏳ 当前有正在进行的下载任务，请稍后查询进度")
            return

        await bot.send(
            event,
            f"🛠️ 开始处理 JM{album_id}：\n"
            "▫️ 正在连接下载服务器...\n"
            "▫️ 预计需要3-5分钟，请稍候",
        )
        start_time = time.time()

        # 生成文件：任何失败都换成看得懂的说法，方便原样回给用户
        try:
            file_name = load_pdf(album_id)
            file_path = f"{_JM_BOOK_DIR}/{file_name}"
        except Exception as exc:  # noqa: BLE001 —— 统一包装成用户可读的失败原因
            raise RuntimeError(f"文件生成失败: {exc}")

        if not os.path.exists(file_path):
            raise FileNotFoundError("生成文件未找到")

        try:
            await bot.call_api(
                "upload_group_file",
                group_id=event.group_id,
                file=file_path,
                name=file_name,
            )
        except Exception as exc:  # noqa: BLE001 —— 同上
            raise RuntimeError(f"文件上传失败: {exc}")

        duration = time.time() - start_time
        await bot.send(
            event=event,
            message=f"✅ 处理完成 JM{album_id}：\n"
            f"▫️ 文件名称：{file_name}.pdf\n"
            f"▫️ 处理耗时：{duration:.1f}秒\n"
            "📢 文件已成功上传至群文件",
        )
    except Exception as exc:  # noqa: BLE001 —— 任何失败都要如实回给用户，不能吞掉
        error_msg = (
            f"❌ 处理 JM{album_id} 失败：\n"
            f"▫️ 错误原因：{exc}\n"
            "🔧 建议操作：\n"
            "1. 检查ID是否正确\n"
            "2. 等待10分钟后重试\n"
            "3. 联系管理员查看服务器日志"
        )
        await bot.send(event=event, message=error_msg)
    finally:
        # 锁文件可能已被并发流程清掉，删失败不影响主流程，记一条日志即可
        try:
            os.remove(lock_file)
        except OSError:
            nonebot.logger.warning(f"[jm] 锁文件清理失败，已忽略：{lock_file}")


jm_close = on_regex(pattern="^(关闭jm功能)$")


@jm_close.handle()
async def jm_close_rev(bot: Bot, event: Event) -> None:
    """关闭jm功能：仅管理员，且只在群聊里生效。"""
    if int(event.get_user_id()) not in _ADMINS:
        await bot.send(event, "你没有权限关闭jm功能")
        return
    if isinstance(event, GroupMessageEvent):
        fd = os.open(_group_lock(event.group_id), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        await bot.send(event, "已关闭jm功能")


jm_open = on_regex(pattern="^(开启jm功能)$")


@jm_open.handle()
async def jm_open_rev(bot: Bot, event: Event) -> None:
    """开启jm功能：仅管理员，且只在群聊里生效。"""
    if int(event.get_user_id()) not in _ADMINS:
        await bot.send(event, "你没有权限开启jm功能")
        return
    if isinstance(event, GroupMessageEvent):
        os.remove(_group_lock(event.group_id))
        await bot.send(event, "已开启jm功能")
