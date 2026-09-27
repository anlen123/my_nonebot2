"""单元测试配置

为单元测试提供 pytest 配置。

重要：单元测试直接测试独立模块，不触发 __init__.py 的导入链。
使用 importlib 直接加载模块文件，mock 必要的 nonebot 依赖。
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# 项目 src 目录
SRC_DIR = Path(__file__).parent.parent.parent / "src" / "nonebot_plugin_auto_emojimix"


def load_module_directly(module_name: str, file_name: str):
    """直接加载模块，绕过 __init__.py"""
    file_path = SRC_DIR / file_name
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# 在加载 service.py 之前，mock 它的 nonebot 依赖
# service.py 导入了 nonebot.log.logger 和 .config.emoji_config
_mock_logger = MagicMock()
_mock_nonebot_log = MagicMock()
_mock_nonebot_log.logger = _mock_logger

_mock_config_module = MagicMock()
_mock_config_module.plugin_config = MagicMock()
_mock_config_module.plugin_config.emojimix_explicit = True
_mock_config_module.plugin_config.emojimix_auto = True
_mock_config_module.plugin_config.emojimix_cd = 60

sys.modules.setdefault("nonebot", MagicMock())
sys.modules.setdefault("nonebot.log", _mock_nonebot_log)
sys.modules["nonebot_plugin_auto_emojimix.config"] = _mock_config_module


@pytest.fixture(scope="session", autouse=True)
async def after_nonebot_init():
    """覆盖根目录的 fixture，不执行任何初始化"""
    pass


@pytest.fixture(scope="session")
def service_module():
    """加载 service 模块"""
    return load_module_directly("nonebot_plugin_auto_emojimix.service", "service.py")
