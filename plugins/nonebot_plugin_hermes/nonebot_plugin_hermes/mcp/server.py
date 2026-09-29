"""FastMCP app 工厂。

把 push_message / list_active_sessions / get_recent_messages 三个工具
装配进一个 FastMCP 实例,供 mcp/__init__.py 启动 uvicorn 时使用。
"""

from __future__ import annotations

import json
from typing import Any

import mcp.types as mcp_types
from fastmcp import FastMCP
from nonebot import logger
from starlette.responses import JSONResponse

from ..core.active_session import ActiveSessionManager
from ..core.bot_registry import BotRegistry
from ..core.message_buffer import MessageBuffer
from ..core.routing import resolve_caller_scope
from ..core.storage.image_cache import ImageCache
from ..core.storage.message_store import MessageStore
from .auth import caller_scope_from_request
from .tools.get_message_images import (
    GetMessageImagesInput,
    get_message_images_impl,
)
from .tools.get_recent_messages import (
    GetRecentMessagesInput,
    GetRecentMessagesResult,
    get_recent_messages_impl,
)
from .tools.list_active_sessions import (
    ListActiveSessionsInput,
    ListActiveSessionsResult,
    list_active_sessions_impl,
)
from .tools.push_message import (
    PushMessageInput,
    PushMessageResult,
    push_message_impl,
)

# capability 协商里要摘掉的 JSON-RPC 入口。FastMCP 的 _setup_handlers 无条件注册
# list_resources / list_prompts / read_resource / get_prompt /
# list_resource_templates,而 SDK 的 get_capabilities 按 handler 表里有没有这些 key
# 决定声明哪些能力 —— 于是即使我们零个 resource/prompt,这两块能力照样被声明,
# Hermes 等代理端会把每个方法包成 tool 暴露给 LLM(白占 tool/list 描述,且 LLM
# 可能误调)。摘掉后协商结果才准确反映成「我只有 tools」。
# FastMCP 顶层 mcp.list_resources() 等 Python API 不受影响 —— 那是 provider 层的
# 方法,摘的是协议入口。
_SUPPRESSED_REQUEST_TYPES = (
    "ListResourcesRequest",
    "ListResourceTemplatesRequest",
    "ReadResourceRequest",
    "ListPromptsRequest",
    "GetPromptRequest",
)


def _method_literal(req_type: Any) -> str | None:
    """取 request 类型自带的 JSON-RPC 方法名字面量(如 `resources/list`)。

    比硬编码一份方法名表好:字面量由 SDK 自己声明,键的形态换了也不用跟着改。
    """
    try:
        field = req_type.model_fields.get("method")
    except Exception:
        return None
    default = getattr(field, "default", None)
    return default if isinstance(default, str) else None


def _suppress_unused_protocol_handlers(mcp: FastMCP) -> None:
    """摘掉未实现的 resource/prompt 协议入口,让能力协商只声明 tools。

    这是纯粹的 token 优化,**绝不允许拦住启动**:摘不掉的后果只是代理端多看到
    几个空能力,而抛异常的后果是整个 bot 起不来。所以整段兜住异常并告警放行。

    SDK 没有提供公开的移除入口,只能按名字探,而且**两个维度都变过**:
    handler 表本身在 1.x 是公有 `request_handlers`、2.x 改成私有
    `_request_handlers`;表的键在 1.x 是 request 类型、2.x 换成方法名字符串。
    两种键都试着删,并统计实际删掉几个 —— 只按一种形态删会在另一形态下
    静默失效(不抛错、也没关掉任何能力),那比直接报错更难发现。
    """
    try:
        server = getattr(mcp, "_mcp_server", None)
        handlers = None
        for attr in ("request_handlers", "_request_handlers"):
            candidate = getattr(server, attr, None)
            if isinstance(candidate, dict):
                handlers = candidate
                break
        if handlers is None:
            logger.warning(
                "[HERMES MCP] 未能定位 MCP SDK 的 request handler 表,"
                "resource/prompt 能力将照常声明(只多占 tool/list 描述,不影响功能)"
            )
            return

        removed = 0
        missing: list[str] = []
        for name in _SUPPRESSED_REQUEST_TYPES:
            req_type = getattr(mcp_types, name, None)
            if req_type is None:
                missing.append(name)
                continue
            keys: list[Any] = [req_type]
            method = _method_literal(req_type)
            if method is not None:
                keys.append(method)
            for key in keys:
                if handlers.pop(key, None) is not None:
                    removed += 1

        if missing:
            logger.warning(f"[HERMES MCP] MCP SDK 无以下 request 类型,跳过摘除: {missing}")
        if removed == 0:
            logger.warning(
                "[HERMES MCP] 未摘除任何 resource/prompt 协议入口,能力协商可能仍声明这两块"
                "(只多占 tool/list 描述,不影响功能);handler 表的键形态可能又变了"
            )
        else:
            logger.debug(f"[HERMES MCP] 已摘除 {removed} 个未用协议入口,能力协商只声明 tools")
    except Exception as exc:
        logger.warning(f"[HERMES MCP] 摘除未用协议入口失败,继续启动 ({type(exc).__name__}: {exc})")


def _log_tool_call(name: str, args: str, outcome: str) -> None:
    """反向通道每次被调用留一行痕迹。

    「模型说它拿不到图」与「工具压根没被调用」是两种完全不同的故障,此前 bot 日志
    对二者都是静默的,无从区分。参数只记形状(id / 计数 / 长度),不记消息正文。

    只在调用返回后记一行:参数校验失败由 fastmcp 日志重定向单独报,工具体抛异常由
    FastMCP 自己的 "Error calling tool" 覆盖,两处合起来不留空白。
    """
    logger.info(f"[HERMES MCP] tool={name} {args} -> {outcome}")


def _clip(text: str | None, limit: int = 80) -> str:
    """错误文案可能很长(越权那段是给调用方看的完整说明),日志里只留开头。"""
    if not text:
        return ""
    return text if len(text) <= limit else text[:limit] + "…"


def _summarize_image_result(blocks: list) -> str:
    """图片工具的诊断价值全在 `available` / `reason` 上。

    「没拿到图」有四种成因(未入库 / 字节缺失 / 单图超限 / 总量超限),它们的处置
    完全不同 —— 只报一个总数等于没报。
    """
    images = sum(1 for b in blocks if type(b).__name__ == "ImageContent")
    try:
        header = json.loads(blocks[0].text)
        results = header["results"]
    except Exception:
        return f"blocks={len(blocks)} images={images}"
    missing = [
        f"m:{r.get('message_id')}#{r.get('image_idx')}={r.get('reason')}" for r in results if not r.get("available")
    ]
    return f"images={images}" + (f" unavailable=[{', '.join(missing)}]" if missing else "")


def build_mcp_app(
    *,
    message_buffer: MessageBuffer,
    active_sessions: ActiveSessionManager,
    bot_registry: BotRegistry,
    message_store: MessageStore,
    image_cache: ImageCache,
) -> Any:
    """构造 FastMCP 实例并返回 http_app(),由调用者交给 uvicorn。

    fastmcp 3.x: `http_app()` 替代旧版 `streamable_http_app()`。
    返回 Starlette ASGI app,我们在外面再包一层 Bearer 鉴权中间件。
    """

    mcp = FastMCP("nonebot-bridge")

    _suppress_unused_protocol_handlers(mcp)

    # 注:三个工具的签名都是扁平参数(非 Pydantic model 包装)。FastMCP 单 model 入参
    # 会把 schema 暴露成 {properties: {input: {...}}},逼客户端 wrap 一层 input,
    # 调用方传扁平参数会撞上"Missing required argument 'input'"+"Unexpected
    # keyword argument 'adapter/group_id/...'"的混淆错误。扁平签名让 schema 直接
    # 是 {adapter, group_id, ...},LLM 自然写法和 curl 默认形态都对得上。
    # Input 模型保留:它是 impl 层契约 + 单元测试入口。

    @mcp.tool()
    async def push_message(
        adapter: str,
        group_id: str,
        text: str,
        image_urls: list[str] | None = None,
        reply_to_msg_id: str | None = None,
        task_id: str | None = None,
    ) -> PushMessageResult:
        """Send a message to a group via nonebot. Reactive context required."""
        inp = PushMessageInput(
            adapter=adapter,
            group_id=group_id,
            text=text,
            image_urls=image_urls or [],
            reply_to_msg_id=reply_to_msg_id,
            task_id=task_id,
        )
        result = await push_message_impl(
            inp,
            active_sessions=active_sessions,
            bot_registry=bot_registry,
            message_buffer=message_buffer,
            scope=caller_scope_from_request(),
        )
        _log_tool_call(
            "push_message",
            f"adapter={adapter} group={group_id} text_len={len(text)} imgs={len(image_urls or [])}",
            f"ok={result.ok}"
            + (f" error={_clip(result.error)!r}" if result.error else "")
            + (f" skipped_imgs={len(result.skipped_images)}" if result.skipped_images else ""),
        )
        return result

    @mcp.tool()
    async def list_active_sessions(
        adapter: str | None = None,
    ) -> ListActiveSessionsResult:
        """List active reactive sessions."""
        inp = ListActiveSessionsInput(adapter=adapter)
        result = await list_active_sessions_impl(
            inp, active_sessions=active_sessions, scope=caller_scope_from_request()
        )
        _log_tool_call("list_active_sessions", f"adapter={adapter}", f"sessions={len(result.sessions)}")
        return result

    @mcp.tool()
    async def get_recent_messages(
        adapter: str,
        group_id: str,
        limit: int = 20,
        before_ts: int | None = None,
    ) -> GetRecentMessagesResult:
        """Fetch recent messages from a group's buffer. Use sparingly."""
        inp = GetRecentMessagesInput(
            adapter=adapter,
            group_id=group_id,
            limit=limit,
            before_ts=before_ts,
        )
        result = await get_recent_messages_impl(inp, message_buffer=message_buffer, scope=caller_scope_from_request())
        _log_tool_call(
            "get_recent_messages",
            f"adapter={adapter} group={group_id} limit={limit}",
            f"messages={len(result.messages)}" + (f" error={_clip(result.error)!r}" if result.error else ""),
        )
        return result

    @mcp.tool()
    async def get_message_images(
        message_ids: list[int],
        adapter: str | None = None,
        group_id: str | None = None,
    ) -> list:
        """Fetch image bytes for specific message ids (max 4 ids per call).

        Returns a content[] array combining a JSON header (per-image metadata)
        with TextContent markers + ImageContent blocks for each available image.
        Use after get_recent_messages identifies which message_id the user
        is referring to (e.g. '上图' → most recent msg with image_count > 0).
        """
        inp = GetMessageImagesInput(
            message_ids=message_ids,
            adapter=adapter,
            group_id=group_id,
        )
        blocks = await get_message_images_impl(
            inp, store=message_store, cache=image_cache, scope=caller_scope_from_request()
        )
        _log_tool_call(
            "get_message_images",
            f"ids={message_ids} adapter={adapter} group={group_id}",
            _summarize_image_result(blocks),
        )
        return blocks

    http_app = mcp.http_app()

    # Bearer 中间件 — 在 ASGI 层裹一层。
    #
    # 这里**只做鉴权**(认不出 → 401),不做授权:调用方能操作哪些群由每个工具用
    # caller_scope_from_request() 逐请求解析。不要改成在这里把 scope 塞进 ContextVar ——
    # stateful streamable HTTP 的工具体跑在 session 创建时 spawn 的 server task 里,
    # ContextVar 会被钉死在建 session 那一次请求上(实测),后续换 token 的调用读到旧值。
    async def bearer_middleware(scope, receive, send):
        if scope["type"] != "http":
            return await http_app(scope, receive, send)
        headers = {k.decode(): v.decode() for k, v in scope.get("headers", [])}
        if resolve_caller_scope(headers.get("authorization")) is None:
            response = JSONResponse({"error": "token mismatch"}, status_code=401)
            return await response(scope, receive, send)
        return await http_app(scope, receive, send)

    # 注:lifespan 通过上面 `scope["type"] != "http"` 分支自然 passthrough 到 http_app,
    # FastMCP 的启动钩子在 inner app 里被 uvicorn 直接以 lifespan scope 调到。
    # 不需要也不应在这里设置 .lifespan 属性——uvicorn 不读这个属性。
    return bearer_middleware
