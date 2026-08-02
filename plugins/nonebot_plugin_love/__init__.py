"""
nonebot_plugin_love —— 个人插件模板 + love 功能

【模板定位】
  这是新写插件时的起点：复制本目录 → 改名为 nonebot_plugin_xxx
  → 修改本文档说明 → 按需保留/删除下面各章节 → 在 bot.py 中加载：
      nonebot.load_plugin("plugins.nonebot_plugin_xxx")

【已集成的可复用代码】
  1. 定时任务（APScheduler）
     - 方式一：装饰器注册固定任务（已启用示例，每小时打一条日志）
     - 方式二：动态注册，按配置循环生成多个任务（参考 auto_message 插件）
  2. 消息读取
     - extract_message_parts()：解析消息段（文本 / 图片 / @ / 合并转发）
     - fetch_msg_by_id()：按 message_id 读取任意消息（含机器人自己发的）
     - send_then_takeback()：发送后拿 message_id，读取/撤回自己刚发的消息
     - has_image() / has_forward()：消息检测 Rule 示例
  3. 合并转发消息工具：send_forward_msg_group()
"""

import asyncio
from typing import List, Optional

import nonebot
from nonebot import get_driver, require
from nonebot.adapters.onebot.v11 import (
    Bot,
    Event,
    GroupMessageEvent,
    Message,
    MessageSegment,
)
from nonebot.params import T_State
from nonebot.plugin import on_message, on_regex
from nonebot.rule import Rule, to_me

# ── 1. 配置 ──────────────────────────────────────────────────────────────────
# .env 里的配置项统一从这里读，例如：
#   LOVE_IMG_ROOT=xxx
#   LOVE_ENABLE=xxx
global_config = get_driver().config
config = global_config.dict()
img_root = config.get("imgroot", "")


# ── 2. 定时任务（APScheduler） ────────────────────────────────────────────────
require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler


# 方式一：装饰器注册固定任务。模板示例已启用（每小时打一条日志），
# 复制后把函数体改成你的定时逻辑，或整体删除。
@scheduler.scheduled_job("interval", seconds=3600, id="love_demo_tick")
async def love_demo_tick():
    """模板示例：定时任务。"""
    try:
        bot: Bot = nonebot.get_bot()
    except Exception:
        nonebot.logger.warning("[love] 定时任务跳过：当前没有可用 bot")
        return
    nonebot.logger.info(f"[love] 定时任务示例触发，当前机器人 QQ：{bot.self_id}")


# 方式二：动态注册。适合把配置里的任务列表循环注册（参考 auto_message 插件）。
def _register_dynamic_job():
    async def _job():
        try:
            bot: Bot = nonebot.get_bot()
        except Exception:
            return
        nonebot.logger.info(f"[love] 动态定时任务示例触发：{bot.self_id}")

    scheduler.add_job(
        _job,
        trigger="interval",
        seconds=3600,
        id="love_demo_dynamic",
        replace_existing=True,
    )


_register_dynamic_job()


# ── 3. 命令 ──────────────────────────────────────────────────────────────────
love = on_regex(pattern="^(ll|love)$")


@love.handle()
async def love_rev(bot: Bot, event: Event):
    await bot.send(event, message="我也爱你")
    # # 发送本地图片示例（base64）：
    # with open("/root/QQbotFiles/pixivZip/97369334/97369334.gif", "rb") as f:
    #     await bot.send(
    #         event,
    #         MessageSegment.image("base64://" + base64.b64encode(f.read()).decode()),
    #     )


qqbot_des = on_regex(pattern="^菜单$", rule=to_me())


@qqbot_des.handle()
async def qqbot_des_rev(bot: Bot, event: Event):
    msg = """qqbot使用说明如下：
1.love, 描述：会给你回复love
2.st, 描述：会发一张色图(无了)
3.sx NB, 描述：通过缩写查全意
4.xr https://baidu.com, 描述：渲染网页成图片
5.yl, 描述：发送上传过的语录，使用上传语录，可以上传图片
6.输入b站的av,或者BV号，描述：给出视频的一些基本信息
7.搜图
8.pixiv pid, 描述：懂的都懂
9.ygo 闪刀，描述：游戏王查卡器
10.ck 游戏王查卡
11.dsr 你的问题（dsr是R1模型, ds3是v3模型），描述：deepseek回答你的问题, dsclear清除上下文
12.gm 你的问题，描述：gnmini回答你的问题, gmclear清除上下文
13.gmt 你的问题，描述：gnmini的推理模型回答你的问题, gmclear清除上下文
-------后续新功能会补充
    """
    await bot.send(event, message=msg)


# ── 4. 消息监听与读取 ────────────────────────────────────────────────────────


# 4.1 解析消息段：把一条消息拆成 文本 / 图片 / @ / 合并转发
def extract_message_parts(message: Message) -> dict:
    """返回 {"text": [...], "images": [...], "ats": [...], "forwards": [...]}"""
    parts = {"text": [], "images": [], "ats": [], "forwards": []}
    for seg in message:
        if seg.type == "text":
            parts["text"].append(seg.data.get("text", ""))
        elif seg.type == "image":
            parts["images"].append(seg.data.get("file", ""))
        elif seg.type == "at":
            parts["ats"].append(seg.data.get("qq", ""))
        elif seg.type == "forward":
            parts["forwards"].append(seg.data.get("id", ""))
    return parts


# 4.2 按 message_id 读取任意消息（包括机器人自己发的消息）
async def fetch_msg_by_id(bot: Bot, message_id: int) -> Optional[dict]:
    """读取一条消息详情；读不到返回 None。"""
    try:
        return await bot.get_msg(message_id=message_id)
    except Exception as e:
        nonebot.logger.warning(f"[love] 读取消息 {message_id} 失败：{e}")
        return None


# 4.3 发送后拿 message_id，读取/撤回自己刚发的消息（防风控常用写法）
async def send_then_takeback(bot: Bot, event: Event, text: str):
    """示例：发送一条消息 → 读取验证 → 10 秒后撤回。"""
    try:
        mess = await bot.send(event=event, message=MessageSegment.text(text))
    except Exception:
        nonebot.logger.warning("[love] 发送失败（可能风控）")
        return
    mid = mess["message_id"]
    detail = await fetch_msg_by_id(bot, mid)  # 读取自己刚发的消息
    nonebot.logger.info(f"[love] 已发送并读取回执：{detail is not None}")
    await asyncio.sleep(10)
    await bot.delete_msg(message_id=mid)  # 撤回自己刚发的消息


# 4.4 Rule 示例：检测消息里是否包含图片
def has_image() -> Rule:
    async def _check(bot: "Bot", event: "Event", state: T_State) -> bool:
        if event.get_type() != "message":
            return False
        return any(seg.type == "image" for seg in event.get_message())

    return Rule(_check)


# 4.5 Rule 示例：检测消息里是否包含合并转发
def has_forward() -> Rule:
    async def _check(bot: "Bot", event: "Event", state: T_State) -> bool:
        if event.get_type() != "message":
            return False
        return any(seg.type == "forward" for seg in event.get_message())

    return Rule(_check)


# 4.6 监听示例（默认注释，复制后取消注释即可用）：
#     群消息里出现合并转发时，自动展开读取转发内容
# forward_watcher = on_message(rule=has_forward(), priority=50, block=False)
#
#
# @forward_watcher.handle()
# async def forward_watcher_rev(bot: Bot, event: Event):
#     for fid in extract_message_parts(event.get_message())["forwards"]:
#         detail = await fetch_msg_by_id(bot, int(fid))
#         nonebot.logger.info(f"[love] 转发消息内容：{detail}")


# ── 5. 工具函数 ──────────────────────────────────────────────────────────────


# 5.1 合并转发：把多条文本打包成一条转发消息发到群里
async def send_forward_msg_group(
    bot: Bot,
    event: GroupMessageEvent,
    name: str,
    msgs: List[str],
):
    def to_json(msg):
        return {
            "type": "node",
            "data": {"name": name, "uin": bot.self_id, "content": msg},
        }

    messages = [to_json(msg) for msg in msgs]
    await bot.call_api(
        "send_group_forward_msg", group_id=event.group_id, messages=messages
    )
