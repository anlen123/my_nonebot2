"""
data_source —— love 插件数据层示例

模板定位：业务数据逻辑（查数据库 / 请求 API / 读文件）统一放这里，
`__init__.py` 只负责事件处理，保持职责分离。

复制后把下面的示例函数改成你的业务逻辑即可。
"""

import random

import aiohttp

# 示例数据：本地常量池
LOVE_TEXTS = ["我也爱你", "爱你哟~", "么么哒"]


async def fetch_random_love_text() -> str:
    """示例：从常量池随机取一句（可直接替换为请求远程 API）。"""
    return random.choice(LOVE_TEXTS)


async def fetch_json(url: str) -> dict:
    """示例：通用 GET 请求封装，返回 JSON（带 UA 头，规避部分风控）。"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.json()
