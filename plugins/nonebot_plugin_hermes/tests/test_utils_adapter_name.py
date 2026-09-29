"""验证 `get_adapter_name` 对 alconna.Target 和 nonebot Bot 两种输入的归一化。

回归用例: 0.3.x 加入 notice handler 时,首次有调用点传 Bot(notice 入口处还没
有 alconna 消息上下文,无法构造 Target)。早期版本只接受 Target,Bot 的 `.adapter`
是 Adapter 实例而非 str,会在 `.lower()` 处崩。
"""

from __future__ import annotations

from unittest.mock import MagicMock


def test_get_adapter_name_normalizes_target_with_str_adapter():
    from nonebot_plugin_hermes.utils import get_adapter_name

    class _T:
        adapter = "OneBot V11"

    assert get_adapter_name(_T()) == "onebotv11"


def test_get_adapter_name_handles_bot_with_adapter_instance():
    """Bot.adapter 是 Adapter 实例(无 .lower(),有 classmethod `get_name()`)。
    helper 必须先解包出 name string 再 normalize,否则会在 `.lower()` 处崩——
    线上观察到的 `AttributeError: 'Adapter' object has no attribute 'lower'`。"""

    class _AdapterLike:
        """模拟真实 Adapter 类的关键面:有 get_name(), 没有 lower()。"""

        @classmethod
        def get_name(cls):
            return "OneBot V11"

    from nonebot_plugin_hermes.utils import get_adapter_name

    bot = MagicMock(spec=["adapter"])
    bot.adapter = _AdapterLike()

    assert get_adapter_name(bot) == "onebotv11"


def test_get_adapter_name_empty_returns_unknown():
    from nonebot_plugin_hermes.utils import get_adapter_name

    class _T:
        adapter = ""

    assert get_adapter_name(_T()) == "unknown"


def test_get_adapter_name_strips_spaces_and_dots():
    from nonebot_plugin_hermes.utils import get_adapter_name

    class _T:
        adapter = "Some.Weird Adapter"

    assert get_adapter_name(_T()) == "someweirdadapter"
