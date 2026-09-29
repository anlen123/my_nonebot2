"""活跃态过期清理。"""

from __future__ import annotations

import time

from nonebot import logger, require

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from .. import mcp as _mcp  # lazy access to runtime singletons
from ..config import plugin_config


def register_expire_active_sessions() -> None:
    interval = plugin_config.hermes_active_sweep_interval_sec

    @scheduler.scheduled_job("interval", seconds=interval, id="hermes_expire_active_sessions")
    async def _sweep() -> None:
        if _mcp.active_sessions is None:
            return
        now_ms = int(time.time() * 1000)
        expired = _mcp.active_sessions.sweep_expired(now_ms)
        if expired:
            logger.info(
                f"[HERMES] swept {len(expired)} expired active session(s): "
                + ", ".join(f"({s.adapter}, {s.group_id})" for s in expired)
            )
