"""未用协议入口的摘除:跨 MCP SDK 版本的容错。

摘除本身是省 token 的优化(让 capability 协商只声明 tools),**不是正确性要求**。
SDK 1.x 用公有 `request_handlers`,2.x 改成私有 `_request_handlers` 且没有公开的
移除入口,所以这里按名字探;探不到必须告警放行,绝不能把 bot 的启动拦下来。
"""

from __future__ import annotations

import mcp.types as mcp_types
import pytest

from nonebot_plugin_hermes.mcp.server import (
    _SUPPRESSED_REQUEST_TYPES,
    _method_literal,
    _suppress_unused_protocol_handlers,
)

_REQ_TYPES = tuple(getattr(mcp_types, n) for n in _SUPPRESSED_REQUEST_TYPES)


def _handler_table() -> dict:
    table = {t: f"handler-{t.__name__}" for t in _REQ_TYPES}
    table[mcp_types.CallToolRequest] = "handler-CallToolRequest"
    return table


class _FakeMcp:
    def __init__(self, server) -> None:
        self._mcp_server = server


class _PublicServer:
    """mcp 1.x 形态。"""

    def __init__(self) -> None:
        self.request_handlers = _handler_table()


class _PrivateServer:
    """mcp 2.x 形态:改名成私有属性。"""

    def __init__(self) -> None:
        self._request_handlers = _handler_table()


class _AlienServer:
    """未来又改名的假想形态。"""

    def __init__(self) -> None:
        self.something_else = _handler_table()


def test_public_handler_table_is_stripped():
    srv = _PublicServer()
    _suppress_unused_protocol_handlers(_FakeMcp(srv))
    assert not any(t in srv.request_handlers for t in _REQ_TYPES)
    # 工具入口必须原样保留 —— 摘的只是 resource/prompt。
    assert mcp_types.CallToolRequest in srv.request_handlers


def test_private_handler_table_is_stripped():
    srv = _PrivateServer()
    _suppress_unused_protocol_handlers(_FakeMcp(srv))
    assert not any(t in srv._request_handlers for t in _REQ_TYPES)
    assert mcp_types.CallToolRequest in srv._request_handlers


def test_unknown_table_name_warns_but_does_not_raise():
    srv = _AlienServer()
    _suppress_unused_protocol_handlers(_FakeMcp(srv))  # 不抛
    assert len(srv.something_else) == len(_REQ_TYPES) + 1


def test_missing_mcp_server_does_not_raise():
    class _Bare:
        pass

    _suppress_unused_protocol_handlers(_Bare())  # 不抛


def test_exploding_server_does_not_raise():
    """属性访问本身抛错也必须被兜住:启动不能因为一个优化而失败。"""

    class _Explosive:
        def __getattr__(self, name):
            raise RuntimeError("simulated SDK breakage")

    _suppress_unused_protocol_handlers(_Explosive())  # 不抛


@pytest.mark.parametrize("name", _SUPPRESSED_REQUEST_TYPES)
def test_suppressed_names_exist_in_installed_sdk(name):
    """名字写错会静默失效,这里对着实装的 SDK 钉一遍。"""
    assert getattr(mcp_types, name, None) is not None


def test_real_fastmcp_capabilities_declare_tools_only():
    """对着实装的 SDK 端到端验一次:摘完之后能力协商不再声明 resources/prompts。"""
    from fastmcp import FastMCP
    from mcp.server.lowlevel import NotificationOptions

    mcp = FastMCP("suppression-probe")
    _suppress_unused_protocol_handlers(mcp)

    caps = mcp._mcp_server.get_capabilities(NotificationOptions(), {})
    assert caps.resources is None
    assert caps.prompts is None


def _method_table() -> dict:
    """mcp 2.x 形态:handler 表改用 JSON-RPC 方法名字符串做键。"""
    table = {_method_literal(t): f"handler-{t.__name__}" for t in _REQ_TYPES}
    table["tools/call"] = "handler-CallToolRequest"
    return table


class _MethodKeyedServer:
    def __init__(self) -> None:
        self._request_handlers = _method_table()


def test_method_string_keys_are_stripped():
    """键换成方法名字符串后仍要删干净 —— 只按 request 类型删会静默失效。"""
    srv = _MethodKeyedServer()
    _suppress_unused_protocol_handlers(_FakeMcp(srv))
    assert not any(_method_literal(t) in srv._request_handlers for t in _REQ_TYPES)
    assert "tools/call" in srv._request_handlers


@pytest.mark.parametrize("name", _SUPPRESSED_REQUEST_TYPES)
def test_method_literal_is_derivable_for_installed_sdk(name):
    """方法名字面量由 SDK 自己声明;推不出来会让 2.x 形态下的摘除失效。"""
    req_type = getattr(mcp_types, name)
    method = _method_literal(req_type)
    assert isinstance(method, str) and "/" in method, f"{name} -> {method!r}"


def test_no_op_is_reported_not_silent(caplog):
    """一个都没删掉时必须留下告警 —— 静默失效比报错更难发现。"""

    class _EmptyTableServer:
        def __init__(self) -> None:
            self.request_handlers = {"tools/call": "handler"}

    with caplog.at_level("WARNING"):
        _suppress_unused_protocol_handlers(_FakeMcp(_EmptyTableServer()))
