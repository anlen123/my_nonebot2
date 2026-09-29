"""定时清理:消息日志 vacuum + 图片缓存配额淘汰。"""

from __future__ import annotations

import time

from nonebot import logger, require

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from .. import mcp as _mcp  # lazy access to runtime singletons
from ..config import plugin_config


def register_storage_vacuum() -> None:
    """每小时整点过 37 分跑一次。

    错峰避开 expire_active_sessions(那个跑秒级 interval),且 :37 是个安静时段
    (与各种整点报告 / 业务监控错开)。

    注:apscheduler 的 @scheduled_job 装饰器内部已经在 add_job 时硬编码
    replace_existing=True(base.py:560 的 positional True),如果我们再传一遍
    kwarg 会撞 `multiple values for argument` —— 启动直接挂。同样不要传 `replace_existing`。
    """

    @scheduler.scheduled_job("cron", minute=37, id="hermes_storage_vacuum")
    async def _vacuum() -> None:
        if _mcp.message_store is None or _mcp.image_cache is None:
            return
        retention_days = plugin_config.hermes_storage_message_retention_days
        max_rows = plugin_config.hermes_storage_message_max_rows
        min_ts = int(time.time() * 1000) - retention_days * 86400 * 1000
        msg_deleted = _mcp.message_store.vacuum(min_ts=min_ts, max_rows=max_rows)
        img_bytes_evicted = _mcp.image_cache.evict_if_over_quota()
        if msg_deleted or img_bytes_evicted:
            logger.info(f"[storage-vacuum] messages_deleted={msg_deleted} image_bytes_evicted={img_bytes_evicted}")
