"""nonebot-bridge MCP server 启动钩子。"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

import uvicorn
from nonebot import get_driver, logger

from ..config import plugin_config
from ..core.active_session import ActiveSessionManager
from ..core.bot_registry import BotRegistry
from ..core.inflight import InflightRegistry
from ..core.message_buffer import MessageBuffer
from ..core.session import session_manager
from ..core.storage.image_cache import ImageCache
from ..core.storage.image_fetcher import ImageFetcher
from ..core.storage.message_store import MessageStore
from ..core.storage.session_key_store import SessionKeyStore
from .server import build_mcp_app


class _ToolValidationLogRedirect(logging.Filter):
    """把 FastMCP 'Error validating tool' 日志 drop 掉,用 nonebot logger 重发一行。

    FastMCP 在 server.py 对客户端参数校验失败用 logger.exception() 打成
    ERROR + 完整栈,但这其实是 *客户端错*——错误已通过 structured response
    (isError=true) 回给调用方了,服务端再打满屏 traceback 看起来像 server
    crash 但不是。
    丢弃原 stdlib record,通过 nonebot 的 loguru logger 发一行简洁 WARNING,
    与 bot 其它日志格式对齐(右对齐时间戳 / 颜色 / 文件位置)。
    其它真实异常路径('Error calling tool ...')不在前缀范围内,保持原样。
    """

    _PREFIX = "Error validating tool "
    _TOOL_RE = re.compile(r"^Error validating tool '([^']+)'")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        if not msg.startswith(self._PREFIX):
            return True

        m = self._TOOL_RE.match(msg)
        tool_name = m.group(1) if m else "?"

        detail = ""
        if record.exc_info and record.exc_info[1] is not None:
            # ValidationError str 多行,collapse 成一行并截断
            detail = str(record.exc_info[1]).replace("\n", " | ")[:200]

        logger.warning(
            f"[HERMES MCP] tool '{tool_name}' validation failed: {detail}"
            if detail
            else f"[HERMES MCP] tool '{tool_name}' validation failed (no detail)"
        )
        return False  # 丢弃原 stdlib record


# 模块级安装一次。fastmcp 用 logging.getLogger("fastmcp.server.server"),
# import 期 attach filter,所有后续 record 都过这条。
_FASTMCP_TOOL_LOGGER = logging.getLogger("fastmcp.server.server")
if not any(isinstance(f, _ToolValidationLogRedirect) for f in _FASTMCP_TOOL_LOGGER.filters):
    _FASTMCP_TOOL_LOGGER.addFilter(_ToolValidationLogRedirect())

# 全局单例(plugin __init__.py 在 startup 钩子里装配)
message_buffer: MessageBuffer | None = None
active_sessions: ActiveSessionManager | None = None
bot_registry: BotRegistry | None = None
inflight: InflightRegistry | None = None
message_store: MessageStore | None = None
image_cache: ImageCache | None = None
image_fetcher: ImageFetcher | None = None
session_key_store: SessionKeyStore | None = None

_server_task: asyncio.Task | None = None
_uvicorn_server: uvicorn.Server | None = None


def _default_db_path() -> Path:
    """SQLite 默认存储位置:走 nonebot-plugin-localstore 的 plugin_data_dir。

    具体路径由 localstore 解析(通常 ~/.local/share/nonebot2/nonebot_plugin_hermes/);
    可被环境变量 LOCALSTORE_DATA_DIR / LOCALSTORE_PLUGIN_DATA_DIR 重定向,这是
    NoneBot 官方约定的存储位置管理方式,优于硬编码 XDG_DATA_HOME。
    """
    import nonebot_plugin_localstore as store

    return store.get_plugin_data_dir() / "messages.db"


def _default_image_cache_dir() -> Path:
    """图字节缓存默认位置:走 localstore 的 plugin_cache_dir。

    图字节是 regenerable 资源(可重新从 URL 抓),走 cache_dir 而非 data_dir 是对的——
    cache_dir 默认在 XDG_CACHE_HOME 下,系统/用户可放心整目录清理。
    """
    import nonebot_plugin_localstore as store

    return store.get_plugin_cache_dir() / "images"


def init_runtime_state() -> None:
    """由 plugin __init__.py 在 startup 钩子里调用,装配全局对象。"""
    global message_buffer, active_sessions, bot_registry, inflight
    global message_store, image_cache, image_fetcher, session_key_store

    db_path_str = plugin_config.hermes_storage_db_path or ""
    db_path = Path(db_path_str) if db_path_str else _default_db_path()
    if message_store is None:
        message_store = MessageStore(db_path=db_path)
    if session_key_store is None:
        # session key 映射跟着消息库同目录走:两者都是「重启必须还在」的运行时状态。
        # 分文件存是因为消息库归 hermes-purge-media 的 vacuum 管,别让按天数/行数
        # 淘汰的逻辑误伤会话映射。
        session_key_store = SessionKeyStore(db_path=db_path.parent / "session_keys.db")
        session_manager.bind_store(session_key_store)
    if image_cache is None:
        cache_dir_str = plugin_config.hermes_image_cache_dir or ""
        cache_dir = Path(cache_dir_str) if cache_dir_str else _default_image_cache_dir()
        image_cache = ImageCache(
            cache_dir=cache_dir,
            quota_bytes=plugin_config.hermes_image_cache_quota_mb * 1024 * 1024,
        )
        image_cache.evict_if_over_quota()
    if image_fetcher is None:
        image_fetcher = ImageFetcher(
            store=message_store,
            cache=image_cache,
            timeout_s=plugin_config.hermes_image_fetch_timeout_s,
            max_attempts=plugin_config.hermes_image_fetch_max_attempts,
        )
    if message_buffer is None:
        message_buffer = MessageBuffer(store=message_store, fetcher=image_fetcher)
    if active_sessions is None:
        active_sessions = ActiveSessionManager(
            default_ttl_sec=plugin_config.hermes_active_session_ttl_sec,
        )
    if bot_registry is None:
        bot_registry = BotRegistry()
    if inflight is None:
        inflight = InflightRegistry()


async def start_storage() -> None:
    """启动 image_fetcher 异步 worker。"""
    if image_fetcher is not None:
        await image_fetcher.start()


async def stop_storage() -> None:
    """关闭 image_fetcher 与 message_store。"""
    if image_fetcher is not None:
        await image_fetcher.stop()
    if message_store is not None:
        message_store.close()
    if session_key_store is not None:
        session_key_store.close()


def _on_server_task_done(task: asyncio.Task) -> None:
    """uvicorn.serve() 在 task 里跑;端口绑定失败 / 中途异常都在这里捕获。
    没有这个 callback 的话,asyncio 只会以 'Task exception was never retrieved'
    打到 stderr,我们的应用日志却以为已 'started'。"""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(f"[HERMES MCP] server task died: {exc!r}")


async def start_mcp_server() -> None:
    global _server_task, _uvicorn_server

    if not plugin_config.hermes_mcp_enabled:
        logger.info("[HERMES MCP] disabled (HERMES_MCP_ENABLED=false)")
        return
    if _server_task is not None and not _server_task.done():
        logger.warning("[HERMES MCP] start_mcp_server called twice; ignoring second call")
        return
    if (
        message_buffer is None
        or active_sessions is None
        or bot_registry is None
        or message_store is None
        or image_cache is None
    ):
        logger.error("[HERMES MCP] runtime state not initialized; skipping")
        return

    asgi_app = build_mcp_app(
        message_buffer=message_buffer,
        active_sessions=active_sessions,
        bot_registry=bot_registry,
        message_store=message_store,
        image_cache=image_cache,
    )

    config = uvicorn.Config(
        asgi_app,
        host=plugin_config.hermes_mcp_host,
        port=plugin_config.hermes_mcp_port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    _uvicorn_server = server
    _server_task = asyncio.create_task(server.serve(), name="hermes-mcp-server")
    _server_task.add_done_callback(_on_server_task_done)
    # 注:create_task 立刻返回,uvicorn 还在异步绑定端口;此 log 仅声明意图,
    # 真正起来 / 失败由 _on_server_task_done 捕获或 uvicorn 自身 stderr 日志反映。
    logger.info(f"[HERMES MCP] starting on {plugin_config.hermes_mcp_host}:{plugin_config.hermes_mcp_port}")


async def stop_mcp_server() -> None:
    global _server_task, _uvicorn_server
    if _uvicorn_server is not None:
        _uvicorn_server.should_exit = True
    if _server_task is not None:
        forced = False
        try:
            # asyncio.wait_for 内部已在超时时取消 task,无需再 cancel
            await asyncio.wait_for(_server_task, timeout=5)
        except asyncio.TimeoutError:
            forced = True
        if forced:
            logger.warning("[HERMES MCP] graceful stop timed out; uvicorn was force-cancelled")
        else:
            logger.info("[HERMES MCP] stopped")
        _server_task = None
    _uvicorn_server = None


def register_lifecycle() -> None:
    """注册 nonebot 启动 / 停止钩子。"""
    driver = get_driver()
    driver.on_startup(start_mcp_server)
    driver.on_shutdown(stop_mcp_server)
