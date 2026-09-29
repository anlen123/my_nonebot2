"""BotRegistry 单元测试。"""

from __future__ import annotations

from nonebot_plugin_hermes.core.bot_registry import BotRegistry


class _FakeTarget:
    """alconna Target 的极简替身,只用于身份对比。"""

    def __init__(self, label: str):
        self.label = label

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _FakeTarget) and self.label == other.label

    def __repr__(self) -> str:
        return f"_FakeTarget({self.label!r})"


def test_upsert_and_get():
    reg = BotRegistry()
    t = _FakeTarget("g1")
    reg.upsert("ob11", "group", "g1", "bot42", t, ts=100)
    entry = reg.get("ob11", "group", "g1")
    assert entry is not None
    assert entry.bot_self_id == "bot42"
    assert entry.target == t
    assert entry.last_seen_at == 100


def test_upsert_updates_existing():
    reg = BotRegistry()
    reg.upsert("ob11", "group", "g1", "bot42", _FakeTarget("g1-old"), ts=100)
    reg.upsert("ob11", "group", "g1", "bot42", _FakeTarget("g1-new"), ts=200)
    entry = reg.get("ob11", "group", "g1")
    assert entry.target == _FakeTarget("g1-new")
    assert entry.last_seen_at == 200


def test_get_missing_returns_none():
    reg = BotRegistry()
    assert reg.get("ob11", "group", "ghost") is None


def test_known_lists_all_then_filters_by_adapter():
    reg = BotRegistry()
    reg.upsert("ob11", "group", "g1", "b", _FakeTarget("g1"), ts=1)
    reg.upsert("ob11", "private", "u1", "b", _FakeTarget("u1"), ts=2)
    reg.upsert("kook", "group", "k1", "b", _FakeTarget("k1"), ts=3)

    assert len(reg.known()) == 3
    assert {k[2] for k in reg.known(adapter="ob11")} == {"g1", "u1"}


def test_remove():
    reg = BotRegistry()
    reg.upsert("ob11", "group", "g1", "b", _FakeTarget("g1"), ts=1)
    reg.remove("ob11", "group", "g1")
    assert reg.get("ob11", "group", "g1") is None


def test_remove_missing_is_silent_noop():
    """remove() 不存在的 key 必须静默成功,不抛 KeyError。"""
    reg = BotRegistry()
    reg.remove("ob11", "group", "ghost")  # 不抛
    assert reg.get("ob11", "group", "ghost") is None


def test_known_on_empty_returns_empty_list():
    """known() 空注册表返回空列表;known(adapter=...) 同理。"""
    reg = BotRegistry()
    assert reg.known() == []
    assert reg.known(adapter="ob11") == []
