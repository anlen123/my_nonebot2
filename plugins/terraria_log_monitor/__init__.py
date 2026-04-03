import nonebot
import re
from pathlib import Path
from typing import Optional
from nonebot import get_driver, require
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent

# 先 require 确保插件已加载
nonebot_plugin_apscheduler = require("nonebot_plugin_apscheduler")

driver = get_driver()

# 配置
LOG_FILE_PATH = r"D:\Program Files (x86)\app\steam\steamapps\common\Terraria\server.log"
TARGET_GROUP_ID = 638793706
CHECK_INTERVAL = 5  # 检查间隔（秒）

# 全局变量，记录上次读取的文件位置
last_file_position = 0
last_processed_lines = set()

# 正则表达式匹配 "has joined." 的行
joined_pattern = re.compile(r"(.+?)\s+has joined\.", re.IGNORECASE)

# 从 require 返回的模块中导入 scheduler
from nonebot_plugin_apscheduler import scheduler


def monitor_terraria_log():
    """监控 Terraria 服务器日志文件（定时任务函数）"""
    global last_file_position, last_processed_lines
    
    log_path = Path(LOG_FILE_PATH)
    
    try:
        # 检查文件是否存在
        if not log_path.exists():
            driver.logger.warning(f"日志文件不存在，等待文件创建: {LOG_FILE_PATH}")
            return
        
        # 获取文件当前大小
        file_size = log_path.stat().st_size
        
        # 如果文件被重置（变小了），从头开始读取
        if file_size < last_file_position:
            last_file_position = 0
            last_processed_lines.clear()
            driver.logger.info("日志文件已被重置，从头开始监控")
        
        # 如果文件没有变化，跳过
        if file_size == last_file_position:
            return
        
        # 读取新增的内容
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            f.seek(last_file_position)
            new_lines = f.readlines()
            last_file_position = f.tell()
        
        # 处理新增的行
        for line in new_lines:
            line = line.strip()
            if not line:
                continue
            
            # 避免重复处理同一行
            line_hash = hash(line)
            if line_hash in last_processed_lines:
                continue
            last_processed_lines.add(line_hash)
            
            # 限制缓存大小，避免内存占用过大
            if len(last_processed_lines) > 1000:
                last_processed_lines = set(list(last_processed_lines)[-500:])
            
            # 检查是否包含 "has joined."
            match = joined_pattern.search(line)
            if match:
                username = match.group(1).strip()
                nonebot.get_loop().create_task(send_player_join_message(username))
                driver.logger.info(f"检测到玩家加入: {username}")
    
    except Exception as e:
        driver.logger.error(f"监控日志文件时出错: {e}")


async def send_player_join_message(username: str):
    """发送玩家加入消息到指定群"""
    try:
        bot = nonebot.get_bot()
        message = f"🎮 Terraria 玩家 {username} 已加入游戏！"
        
        await bot.call_api(
            "send_group_msg",
            group_id=TARGET_GROUP_ID,
            message=message
        )
        
        driver.logger.info(f"已发送玩家加入消息到群 {TARGET_GROUP_ID}: {username}")
    except Exception as e:
        driver.logger.error(f"发送玩家加入消息失败: {e}")


# 在模块加载时添加定时任务
try:
    scheduler.add_job(
        monitor_terraria_log,
        "interval",
        seconds=CHECK_INTERVAL,
        id="terraria_log_monitor",
        replace_existing=True
    )
    driver.logger.info(f"已添加 Terraria 日志监控定时任务，间隔: {CHECK_INTERVAL} 秒")
except Exception as e:
    driver.logger.error(f"添加定时任务失败: {e}")