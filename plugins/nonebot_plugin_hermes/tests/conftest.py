"""共享 pytest fixture。M1-mem 内存版无 DB,fixture 极简。"""

from __future__ import annotations

import asyncio

import nonebot
import pytest

# 初始化 NoneBot,使插件模块可以被 import(get_plugin_config 需要 driver 存在)
nonebot.init(driver="~none")


@pytest.fixture(scope="session")
def event_loop_policy():
    """让 pytest-asyncio 使用默认 policy(session 作用域,兼容后续 session-scoped 异步 fixture)。"""
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture
def fixed_now_ms() -> int:
    """固定时间戳,便于断言活跃态过期、滑动续期等时序行为。"""
    return 1_762_560_000_000  # 2025-11-08T00:00:00Z 附近的固定毫秒戳
