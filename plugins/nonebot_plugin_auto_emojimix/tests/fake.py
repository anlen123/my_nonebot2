"""虚拟事件工厂

用于测试的虚拟 OneBot V11 消息事件。
"""

from time import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent


def make_group_message_event(**field) -> "GroupMessageEvent":
    """创建虚拟群消息事件"""
    from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
    from nonebot.adapters.onebot.v11.event import Sender

    defaults = {
        "time": int(time()),
        "self_id": 123456,
        "post_type": "message",
        "sub_type": "normal",
        "user_id": 12345678,
        "message_type": "group",
        "group_id": 87654321,
        "message_id": 1,
        "message": Message("test"),
        "raw_message": "test",
        "original_message": Message("test"),
        "font": 0,
        "sender": Sender(card="", nickname="test", role="member"),
    }
    defaults.update(field)

    # 确保 original_message 与 message 同步
    if "message" in field and "original_message" not in field:
        defaults["original_message"] = field["message"]
    if "message" in field and "raw_message" not in field:
        defaults["raw_message"] = field["message"].extract_plain_text()

    return GroupMessageEvent(**defaults)


def make_private_message_event(**field) -> "PrivateMessageEvent":
    """创建虚拟私聊消息事件"""
    from nonebot.adapters.onebot.v11 import Message, PrivateMessageEvent
    from nonebot.adapters.onebot.v11.event import Sender

    defaults = {
        "time": int(time()),
        "self_id": 123456,
        "post_type": "message",
        "sub_type": "friend",
        "user_id": 12345678,
        "message_type": "private",
        "message_id": 1,
        "message": Message("test"),
        "raw_message": "test",
        "original_message": Message("test"),
        "font": 0,
        "sender": Sender(nickname="test"),
    }
    defaults.update(field)

    if "message" in field and "original_message" not in field:
        defaults["original_message"] = field["message"]
    if "message" in field and "raw_message" not in field:
        defaults["raw_message"] = field["message"].extract_plain_text()

    return PrivateMessageEvent(**defaults)
