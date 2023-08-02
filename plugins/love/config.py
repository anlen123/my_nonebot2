from typing import List, Optional

from nonebot import get_driver
from pydantic import BaseModel


# 其他地方出现的类似 from .. import config，均是从 __init__.py 导入的 Config 实例
class Config(BaseModel):
    meilisearch_host = ""


driver = get_driver()
global_config = driver.config
plugin_config = Config.parse_obj(global_config)
