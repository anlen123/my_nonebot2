import nonebot_plugin_alconna as alconna
from nonebot.adapters import Event

from .config import plugin_config


def get_adapter_name(source) -> str:
    """提取并归一化 adapter 名。

    支持两种输入:
      - `alconna.Target` (`.adapter` 是 str) — 消息处理路径主用
      - `nonebot.adapters.Bot` (`.adapter` 是 Adapter 实例,有 classmethod `get_name()`)
        — notice handler 路径用,因为还没有 alconna 消息上下文构造 Target
    """
    adapter = getattr(source, "adapter", "") or ""
    if hasattr(adapter, "get_name"):
        adapter = adapter.get_name()
    return adapter.lower().replace(" ", "").replace(".", "") or "unknown"


def check_isolation(event: Event, target: alconna.Target) -> bool:
    """
    检查当前消息是否在隔离白名单中。
    如果未通过白名单检查，返回 False。
    """
    user_id = event.get_user_id() or "user"

    if target.private:
        # 私聊触发检查
        if plugin_config.hermes_private_trigger == "allowlist" and user_id not in plugin_config.hermes_allow_users:
            return False
    else:
        # 群聊触发检查
        group_id = target.id
        if plugin_config.hermes_allow_groups and group_id not in plugin_config.hermes_allow_groups:
            return False

    return True
