"""反向通道的调用痕迹:每次 MCP 工具调用都要在 bot 日志里留一行。

「模型说它拿不到图」与「工具压根没被调用」是两种完全不同的故障,没有这行日志
无法区分 —— 所以这里穿过真实传输层调用,断言日志内容,而不是只测 impl。

顺带钉住两件此前没有测试覆盖的事:`get_message_images` 的 ImageContent 能活着
穿过 FastMCP 的序列化,以及四个工具的入参 schema 保持扁平(不被包一层 input)。
"""

from __future__ import annotations

import io
import json
import time
from dataclasses import dataclass
from typing import Any

import pytest
from PIL import Image

from nonebot_plugin_hermes.core.active_session import ActiveSessionManager
from nonebot_plugin_hermes.core.bot_registry import BotRegistry
from nonebot_plugin_hermes.core.message_buffer import BufferedMessage, MessageBuffer
from nonebot_plugin_hermes.core.storage.image_cache import ImageCache
from nonebot_plugin_hermes.core.storage.image_fetcher import ImageFetcher
from nonebot_plugin_hermes.core.storage.message_store import MessageStore

from .test_mcp_scope_transport import _GLOBAL, _serving, _streamable_http

_JPEG = None


@dataclass
class _Runtime:
    """比 scope 测试那个 fixture 多暴露几件东西 —— 这里要往库里种图。"""

    app: Any
    buffer: MessageBuffer
    store: MessageStore
    cache: ImageCache


@pytest.fixture
def _tool_runtime(monkeypatch, tmp_path):
    from nonebot_plugin_hermes.config import plugin_config
    from nonebot_plugin_hermes.mcp.server import build_mcp_app

    # 路由表留空:全局 key 的范围是补集,也就是全部,省去逐条授权。
    monkeypatch.setattr(plugin_config, "hermes_api_key", _GLOBAL)
    monkeypatch.setattr(plugin_config, "hermes_group_endpoints", {})

    store = MessageStore(db_path=tmp_path / "m.db")
    cache = ImageCache(cache_dir=tmp_path / "imgs", quota_bytes=8 * 1024 * 1024)
    buffer = MessageBuffer(store=store, fetcher=ImageFetcher(store=store, cache=cache))
    active = ActiveSessionManager(default_ttl_sec=36_000)
    active.trigger("ob11", "g1", "u1", now_ms=int(time.time() * 1000))

    app = build_mcp_app(
        message_buffer=buffer,
        active_sessions=active,
        bot_registry=BotRegistry(),
        message_store=store,
        image_cache=cache,
    )
    yield _Runtime(app=app, buffer=buffer, store=store, cache=cache)
    store.close()


def _jpeg_bytes() -> bytes:
    global _JPEG
    if _JPEG is None:
        buf = io.BytesIO()
        Image.new("RGB", (48, 48), (10, 120, 200)).save(buf, format="JPEG")
        _JPEG = buf.getvalue()
    return _JPEG


@pytest.fixture
def _captured_logs():
    from nonebot import logger as nb_logger

    lines: list[str] = []
    sink_id = nb_logger.add(lambda m: lines.append(str(m)), level="INFO")
    try:
        yield lines
    finally:
        nb_logger.remove(sink_id)


def _tool_lines(lines: list[str]) -> list[str]:
    return [ln for ln in lines if "[HERMES MCP] tool=" in ln]


@pytest.mark.asyncio
async def test_every_tool_call_leaves_a_line(_tool_runtime, _captured_logs):
    from mcp.client.session import ClientSession

    runtime = _tool_runtime
    async with (
        _serving(runtime.app) as url,
        _streamable_http(url, {"Authorization": f"Bearer {_GLOBAL}"}) as (read, write, _sid),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        await session.call_tool("list_active_sessions", {})
        await session.call_tool("get_recent_messages", {"adapter": "ob11", "group_id": "g1"})

    lines = _tool_lines(_captured_logs)
    assert len(lines) == 2, lines
    assert "tool=list_active_sessions" in lines[0] and "sessions=" in lines[0]
    assert "tool=get_recent_messages" in lines[1] and "messages=" in lines[1]


@pytest.mark.asyncio
async def test_image_tool_logs_per_image_reason(_tool_runtime, _captured_logs):
    """拿不到图时,日志必须说清是哪一种成因 —— 只报总数等于没报。"""
    from mcp.client.session import ClientSession

    runtime = _tool_runtime
    now = int(time.time() * 1000)
    have = BufferedMessage(
        adapter="ob11",
        group_id="g1",
        user_id="u1",
        nickname="ph",
        content="有图 [图片]",
        ts=now,
        image_urls=["https://cdn/ok.jpg"],
    )
    runtime.buffer.append(have)
    sha = runtime.cache.put(_jpeg_bytes(), "image/jpeg")
    runtime.store.update_image_sha(have.id, 0, sha, "image/jpeg")

    # 第二条:入了库但字节从没抓回来(sha 仍是 NULL)——线上「拉不到图」的典型形状
    pending = BufferedMessage(
        adapter="ob11",
        group_id="g1",
        user_id="u1",
        nickname="ph",
        content="抓失败 [图片]",
        ts=now + 1,
        image_urls=["https://cdn/dead.jpg"],
    )
    runtime.buffer.append(pending)

    async with (
        _serving(runtime.app) as url,
        _streamable_http(url, {"Authorization": f"Bearer {_GLOBAL}"}) as (read, write, _sid),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool("get_message_images", {"message_ids": [have.id, pending.id]})

    # ImageContent 必须活着穿过 FastMCP 的序列化
    kinds = [type(c).__name__ for c in result.content]
    assert kinds.count("ImageContent") == 1, kinds
    header = json.loads(result.content[0].text)
    assert [r["available"] for r in header["results"]] == [True, False]

    line = _tool_lines(_captured_logs)[-1]
    assert "tool=get_message_images" in line
    assert "images=1" in line
    assert f"m:{pending.id}#0=cache_miss" in line, line


@pytest.mark.asyncio
async def test_tool_input_schemas_stay_flat(_tool_runtime):
    """单 model 入参会把 schema 变成 {input: {...}},逼调用方多包一层。"""
    from mcp.client.session import ClientSession

    async with (
        _serving(_tool_runtime.app) as url,
        _streamable_http(url, {"Authorization": f"Bearer {_GLOBAL}"}) as (read, write, _sid),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tools = {t.name: t for t in (await session.list_tools()).tools}

    assert set(tools) == {"push_message", "list_active_sessions", "get_recent_messages", "get_message_images"}
    for name, expected in (
        ("get_message_images", {"message_ids", "adapter", "group_id"}),
        ("get_recent_messages", {"adapter", "group_id", "limit", "before_ts"}),
    ):
        props = set(tools[name].inputSchema.get("properties", {}))
        assert props == expected, f"{name}: {props}"
