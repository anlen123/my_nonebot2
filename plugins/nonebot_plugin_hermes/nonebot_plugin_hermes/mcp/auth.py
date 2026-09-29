"""MCP 鉴权 + 工具上下文校验。

三个层次:
  1. HTTP 层身份:resolve_caller_scope(authorization) —— 认不出就是 401(见 mcp/server.py)
  2. 工具层范围:caller_scope_from_request() + assert_scope_allows(...)
  3. 工具层上下文:validate_push_context(...)(活跃 session + BotRegistry 路由)

第 1、2 层用的是同一个函数:身份不需要独立的 token 表,呈上哪个接入点的 key 就说明是
哪个接入点,范围从路由表派生(见 core/routing.py 的 CallerScope)。
"""

from __future__ import annotations

from nonebot import logger

from ..core.active_session import ActiveSessionManager
from ..core.bot_registry import BotRegistry
from ..core.routing import CallerScope, resolve_caller_scope


class AuthError(Exception):
    """HTTP 层鉴权失败,应映射成 401。"""


class PushContextError(Exception):
    """工具上下文不满足(含越权),应映射成 422。"""


def caller_scope_from_request() -> CallerScope | None:
    """从**当前**这一次 MCP 请求解析调用方范围。None = 认不出 → 拒。

    走 FastMCP 的 get_http_headers 而不是自己在 ASGI 中间件里放 ContextVar:
    stateful streamable HTTP 的工具体跑在 session 创建时 spawn 的 server task 里,
    ContextVar 会被钉死在建 session 那一次请求的 token 上(实测),后续换 token 调用
    读到的仍是旧值 —— 而把取不到当成"不限"就是静默不设防。get_http_headers 读的是
    MCP SDK 逐消息挂载的 request_context,才是当前这一次调用的头。
    """
    try:
        from fastmcp.server.dependencies import get_http_headers

        # get_http_headers 默认剔除 authorization,必须显式 include。
        headers = get_http_headers(include={"authorization"})
    except Exception:  # pragma: no cover - 没有 HTTP 上下文(直调 impl 的单测)
        return None
    return resolve_caller_scope(headers.get("authorization"))


def scope_refusal(adapter: str, group_id: str, scope: CallerScope | None) -> str | None:
    """判定范围;放行返回 None,拒绝返回**给调用方看的**一句话(并留 bot 侧 WARNING)。

    工具实现优先用这个而不是 assert_scope_allows:FastMCP 对**任何**从工具体里抛出的异常
    都走 logger.exception(ToolError 也是 FastMCPError,同样落进那一支),于是每次越权都会在
    bot 日志里刷一大段 traceback —— 而越权是预期内的正常判定结果,不是崩溃。
    """
    if scope is not None and scope.allows(adapter, group_id):
        return None
    # 拒绝必须留痕:否则现场症状是"bot 偶尔不吭声、只有某个群不行",极难查。
    # 这里用 describe()(含被排除的别群标签),它只进日志;回给调用方的是 describe_for_caller()。
    detail = "unresolved caller" if scope is None else scope.describe()
    logger.warning(f"[HERMES MCP] 拒绝越权操作 ({adapter}, {group_id}) —— caller: {detail}")
    if scope is None:
        hint = "the bridge could not identify this caller's endpoint"
    else:
        hint = scope.describe_for_caller()
    return (
        f"not authorized for ({adapter}, {group_id}): {hint}. "
        "This is a configuration boundary, not a transient failure — do not retry. "
        "If it should be allowed, the operator has to align this endpoint's MCP token with "
        "HERMES_GROUP_ENDPOINTS on the bot side."
    )


def assert_scope_allows(adapter: str, group_id: str, scope: CallerScope | None) -> None:
    """scope_refusal 的抛异常版本,供已经把错误折进返回值的调用点使用
    (push_message 的 validate_push_context 链)。

    Raises:
        PushContextError: 目标群不在该调用方范围内。
    """
    refusal = scope_refusal(adapter, group_id, scope)
    if refusal is not None:
        raise PushContextError(refusal)


def validate_push_context(
    *,
    adapter: str,
    group_id: str,
    active_sessions: ActiveSessionManager,
    bot_registry: BotRegistry,
    now_ms: int,
    scope: CallerScope | None,
) -> None:
    """守卫 push_message MCP 工具调用的前置上下文。

    M1 规则:必须存在 (adapter, group_id) 的活跃 reactive session,
    且 BotRegistry 有对应路由(target+bot_self_id)才允许 push。
    检查顺序:范围 → session → target。session 缺失比 target 缺失更常见(TTL 过期),
    优先报那条更直观;但**范围必须排在两者之前**,否则越权调用方能通过报错差异
    探知别的群有没有活跃会话。

    M2 将增加 bg_task 路径(执行中的任务允许 push 即使无 reactive session)。

    Raises:
        PushContextError: 任一前置不满足;调用方映射 HTTP 422。
    """
    assert_scope_allows(adapter, group_id, scope)
    if not active_sessions.is_active(adapter, group_id, now_ms):
        raise PushContextError(f"no active reactive session for ({adapter}, {group_id})")
    if bot_registry.get(adapter, "group", group_id) is None:
        raise PushContextError(f"unknown target ({adapter}, {group_id}) — wait for next group message to repopulate")
