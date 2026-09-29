"""get_recent_messages: 拉取一个群的最近 N 条消息(文本 + 图数,不含图字节)。

返回每条消息的 `id` (DB 主键),供 Hermes 端后续调 `get_message_images`
按 id 取图字节。原 `image_urls: list[str]` 字段去掉 —— 让 Hermes 不要直接
读到 CDN 链接,统一走 MCP 工具拿字节(URL 可能短效,且能避免 prompt token 浪费)。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ...config import plugin_config
from ...core.routing import CallerScope
from ..auth import scope_refusal


class GetRecentMessagesInput(BaseModel):
    adapter: str
    group_id: str = Field(..., description="Group ID. M1: 私聊不支持,group_id 必须非空。")
    limit: int = Field(default=20, ge=1, le=100)
    before_ts: int | None = Field(
        default=None,
        description="Unix timestamp (ms);只返回 ts 严格小于此值的消息(分页拉取早于某点的历史)",
    )


class RecentMessageView(BaseModel):
    id: int = Field(..., description="DB 主键;调 get_message_images 时引用")
    ts: int = Field(..., description="Unix timestamp (ms)")
    user_id: str
    nickname: str
    content: str
    image_count: int = Field(..., description="该消息附带的图数量;0 表示无图")
    is_bot: bool


class GetRecentMessagesResult(BaseModel):
    messages: list[RecentMessageView]
    error: str | None = None
    """非 None 表示这次调用被拒(目前只有越权一种),messages 为空。

    走返回值而不是抛异常:FastMCP 对工具体里的任何异常都 logger.exception,越权是预期内的
    判定结果,不该在 bot 日志里刷 traceback。与 push_message 的 ok/error 形状一致。
    """


async def get_recent_messages_impl(
    inp: GetRecentMessagesInput,
    *,
    message_buffer,
    scope: CallerScope | None,
) -> GetRecentMessagesResult:
    # 读也要收敛:能读别的群的历史,隔离同样是破的。判定在取数之前。
    refusal = scope_refusal(inp.adapter, inp.group_id, scope)
    if refusal is not None:
        return GetRecentMessagesResult(messages=[], error=refusal)

    cap = plugin_config.hermes_mcp_recent_limit_max
    effective_limit = min(inp.limit, cap)
    rows = message_buffer.get_recent(
        adapter=inp.adapter,
        group_id=inp.group_id,
        limit=effective_limit,
        before_ts=inp.before_ts,
    )
    views = [
        RecentMessageView(
            id=m.id if m.id is not None else 0,
            ts=m.ts,
            user_id=m.user_id,
            nickname=m.nickname,
            content=m.content,
            image_count=len(m.image_urls),
            is_bot=m.is_bot,
        )
        for m in rows
    ]
    return GetRecentMessagesResult(messages=views)
