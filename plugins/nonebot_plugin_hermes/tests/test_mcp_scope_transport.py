"""反向通道 scope 在真实 ASGI 栈上的传播。

这个文件存在的唯一理由:纯函数单测覆盖不到"scope 到底是哪一次请求的"。
插件用的是 `mcp.http_app()`(stateful streamable HTTP),MCP SDK 在 **session 创建时**
就 `task_group.start(run_server)`,工具体跑在那个 server task 里 —— 它的 context 是
initialize 那一次请求的快照。所以任何"在 ASGI 中间件里设 ContextVar、工具里读"的方案
都会把 scope 钉死在建 session 的那把 token 上(而取不到时若回落成"不限"就是静默不设防)。

下面这条用例就是那个区分点:同一个 mcp-session-id,换一把 token 调用,
返回必须跟着**当前请求**的 token 变。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import time
from contextlib import asynccontextmanager

import pytest


@asynccontextmanager
async def _streamable_http(url: str, headers: dict[str, str]):
    """打开一条 streamable-http 连接,统一 yield `(read, write, get_session_id)`。

    MCP SDK 两代的客户端差三处:
    - 1.x 的工厂叫 streamablehttp_client 且直接收 headers;2.x 删了这个名字,改成
      streamable_http_client,headers 要挂到 httpx client 上。
    - 2.x 依赖 httpx2,client 类型与 1.x 不同 —— 用两代都带的 create_mcp_http_client
      建,就不必自己判 httpx 版本。
    - 1.x yield 三元组(含 get_session_id),2.x 只 yield 读写两条流。session id 是
      本测试的命门(要用同一个 id 换 token 再发一次),所以 2.x 下改从响应头
      `mcp-session-id` 自己抓,对调用方保持同一个三元组形状。

    部署侧装哪一代不由本仓库决定(插件常以源码目录部署,pyproject 的约束不生效),
    所以这条传输层测试必须两边都能跑。
    """
    import mcp.client.streamable_http as mod

    legacy = getattr(mod, "streamablehttp_client", None)
    if legacy is not None:
        async with legacy(url, headers=headers) as (read, write, get_session_id):
            yield read, write, get_session_id
        return

    from mcp.shared._httpx_utils import create_mcp_http_client

    seen: dict[str, str] = {}

    async def _capture(response) -> None:
        sid = response.headers.get("mcp-session-id")
        if sid:
            seen["sid"] = sid

    client = create_mcp_http_client(headers=headers)
    client.event_hooks["response"].append(_capture)
    async with client, mod.streamable_http_client(url, http_client=client) as streams:
        yield streams[0], streams[1], lambda: seen.get("sid")


_TEAM_A = "key-team-a-at-least-16"
_TEAM_B = "key-team-b-at-least-16"
_GLOBAL = "key-global-at-least-16"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def _app(monkeypatch, tmp_path):
    """真 build_mcp_app 产物 + 两个接入点的路由表。"""
    from nonebot_plugin_hermes.config import HermesEndpoint, plugin_config
    from nonebot_plugin_hermes.core.active_session import ActiveSessionManager
    from nonebot_plugin_hermes.core.bot_registry import BotRegistry
    from nonebot_plugin_hermes.core.message_buffer import MessageBuffer
    from nonebot_plugin_hermes.core.storage.image_cache import ImageCache
    from nonebot_plugin_hermes.core.storage.image_fetcher import ImageFetcher
    from nonebot_plugin_hermes.core.storage.message_store import MessageStore
    from nonebot_plugin_hermes.mcp.server import build_mcp_app

    monkeypatch.setattr(plugin_config, "hermes_api_key", _GLOBAL)
    monkeypatch.setattr(
        plugin_config,
        "hermes_group_endpoints",
        {
            "ob11:g1": HermesEndpoint(url="http://h:8642/p/team-a", key=_TEAM_A),
            "ob11:g3": HermesEndpoint(url="http://h:8643", key=_TEAM_B),
        },
    )

    store = MessageStore(db_path=tmp_path / "m.db")
    cache = ImageCache(cache_dir=tmp_path / "imgs", quota_bytes=1024 * 1024)
    buffer = MessageBuffer(store=store, fetcher=ImageFetcher(store=store, cache=cache))
    # 工具层按真实墙钟过滤 expires_at,所以种子必须用真实时间,不能用 0/1。
    now_ms = int(time.time() * 1000)
    active = ActiveSessionManager(default_ttl_sec=36_000)
    active.trigger("ob11", "g1", "u1", now_ms=now_ms)
    active.trigger("ob11", "g3", "u1", now_ms=now_ms)

    app = build_mcp_app(
        message_buffer=buffer,
        active_sessions=active,
        bot_registry=BotRegistry(),
        message_store=store,
        image_cache=cache,
    )
    yield app
    store.close()


def _reset_sse_shutdown_latch() -> None:
    """清掉 sse_starlette 的进程级「已关停」闩锁。

    它有个轮询 watcher 盯着 uvicorn Server 的 should_exit,一旦观察到为真就把
    `AppStatus.should_exit` 置成 True 且**不再复位**;此后同进程内每个新建的 SSE
    响应都会在开头自判「正在关停」而立即结束,表现成客户端的
    "SSE stream ended without a response"。

    我们靠 `should_exit = True` 停 server,于是第一个 server 一停就把后续所有用例
    毒化了。是否踩中取决于 watcher 的轮询时机,所以现象是跨 Python 版本随机挂。
    每次停完就把闩锁清掉,用例之间才互不影响。
    """
    try:
        from sse_starlette.sse import AppStatus

        AppStatus.should_exit = False
    except Exception:  # pragma: no cover - 版本里没有这个闩锁就无需清
        pass


@contextlib.asynccontextmanager
async def _serving(app):
    import uvicorn

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    if not server.started:  # pragma: no cover - 环境不允许绑端口
        server.should_exit = True
        pytest.skip("could not bind a local port for the MCP transport test")
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(task, timeout=10)
        _reset_sse_shutdown_latch()


def _groups_from_tool_result(payload: str) -> set[str]:
    """从 tools/call 的 SSE/JSON 响应里抠出 group_id 集合。"""
    body = payload
    for line in payload.splitlines():
        if line.startswith("data: "):
            body = line[len("data: ") :]
    parsed = json.loads(body)
    result = parsed["result"]
    structured = result.get("structuredContent")
    if structured is None:
        structured = json.loads(result["content"][0]["text"])
    return {s["group_id"] for s in structured["sessions"]}


@pytest.mark.asyncio
async def test_scope_follows_the_current_request_not_the_session_owner(_app):
    """同一个 MCP session,换 token 调用 → 范围必须跟着当前请求变。

    ContextVar 方案在这里会失败(两次都返回建 session 那把 token 的范围)。
    """
    import httpx
    from mcp.client.session import ClientSession

    async with (
        _serving(_app) as url,
        _streamable_http(url, {"Authorization": f"Bearer {_TEAM_A}"}) as (
            read,
            write,
            get_session_id,
        ),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        session_id = get_session_id()
        # 没拿到 session id 这条测试就会空转通过(服务端会为原始 POST 另开一个
        # session,断言照样成立却什么都没验到)。
        assert session_id, "未取到 mcp-session-id,复用 session 换 token 这一步失去意义"

        first = await session.call_tool("list_active_sessions", {})
        assert {s["group_id"] for s in json.loads(first.content[0].text)["sessions"]} == {"g1"}

        # 同一个 session id,换成 team-b 的 token 直接发原始 POST。
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                json={
                    "jsonrpc": "2.0",
                    "id": 99,
                    "method": "tools/call",
                    "params": {"name": "list_active_sessions", "arguments": {}},
                },
                headers={
                    "Authorization": f"Bearer {_TEAM_B}",
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                    "mcp-session-id": session_id or "",
                    "mcp-protocol-version": "2025-06-18",
                },
            )

        assert resp.status_code == 200
        assert _groups_from_tool_result(resp.text) == {"g3"}, (
            "scope 没跟着当前请求走 —— 大概是又改回 ASGI 中间件里的 ContextVar 了"
        )


@pytest.mark.asyncio
async def test_unknown_token_is_401_at_the_asgi_layer(_app):
    import httpx

    async with _serving(_app) as url, httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={
                "Authorization": "Bearer definitely-not-a-known-key",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_endpoint_key_authenticates_at_the_asgi_layer(_app):
    """接入点自己的 key 就是它的 MCP token —— 不需要第二张表。"""
    import httpx

    async with _serving(_app) as url, httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={
                "Authorization": f"Bearer {_TEAM_B}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code != 401


@pytest.mark.asyncio
async def test_out_of_scope_read_is_a_normal_result_not_a_traceback(_app, caplog):
    """越权读:回一条正常结果(带 error 字段),bot 日志里**不能**出现 FastMCP 的异常栈。

    FastMCP 对工具体里抛出的任何异常都走 logger.exception —— 包括 ToolError,因为它也是
    FastMCPError。越权是预期内的判定结果,所以工具必须把它折进返回值。
    """
    import logging

    from mcp.client.session import ClientSession
    from nonebot import logger as nb_logger

    caplog.set_level(logging.INFO)
    # bot 侧那条 WARNING 走 loguru,caplog(stdlib logging)抓不到 —— 自己挂个 sink。
    warned: list[str] = []
    sink_id = nb_logger.add(lambda m: warned.append(str(m)), level="WARNING")

    try:
        async with (
            _serving(_app) as url,
            # 全局 key = 补集 scope;g1 被路由到 team-a,所以补集不含它。
            _streamable_http(url, {"Authorization": f"Bearer {_GLOBAL}"}) as (read, write, _sid),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.call_tool("get_recent_messages", {"adapter": "ob11", "group_id": "g1"})
    finally:
        nb_logger.remove(sink_id)

    assert result.isError is False, "越权不该被当成工具调用失败"
    payload = json.loads(result.content[0].text)
    assert payload["messages"] == []
    assert "not authorized" in payload["error"]
    assert "do not retry" in payload["error"]
    # 别的群的路由配置不能随错误信息漏给调用方
    assert "ob11:g3" not in payload["error"]

    # FastMCP 的异常日志(以及任何带 exc_info 的记录)都不该出现
    assert not [r for r in caplog.records if r.exc_info], "工具越权时不应有异常栈"
    assert not [r for r in caplog.records if "Error calling tool" in r.getMessage()]
    # 但 bot 侧那条可诊断的 WARNING 必须在(它带完整范围,含被排除的别群标签)
    assert any("拒绝越权操作" in w for w in warned)
