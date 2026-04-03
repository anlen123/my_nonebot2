from typing import Dict, List
from pydantic import BaseSettings


class Config(BaseSettings):
    # uid (str) -> 群号列表 (List[str])
    # 示例：{"12345678": ["111111111", "222222222"], "87654321": ["111111111"]}
    bilibili_live_uids: Dict[str, List[str]] = {}

    # 轮询间隔（秒），默认 60 秒
    bilibili_live_interval: int = 60

    class Config:
        extra = "ignore"
