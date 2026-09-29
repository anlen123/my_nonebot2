"""get_message_images: 按消息 id 列表精确取回图字节。

返回 MCP `content[]` 数组,混合 TextContent (元信息 JSON + 每图前的 marker)
与 ImageContent (实际 base64 图字节)。Hermes Agent 端 `tools/mcp_tool.py:2231`
会把 ImageContent 块缓存为 MEDIA:<path> 并注入下一轮 LLM 调用。

返回结构示例:
  [0] TextContent({"results": [...每张图的元数据列表...]})
  [1] TextContent("[image for m:1234 idx=0]")
  [2] ImageContent(data="<base64>", mimeType="image/jpeg")
  [3] TextContent("[image for m:1234 idx=1]")
  [4] ImageContent(...)
"""

from __future__ import annotations

import base64
import json
from typing import Any

from mcp.types import ImageContent, TextContent
from nonebot import logger
from pydantic import BaseModel, Field

from ...core.routing import CallerScope


class GetMessageImagesInput(BaseModel):
    message_ids: list[int] = Field(
        ...,
        min_length=1,
        max_length=4,
        description="要取图字节的消息 id 列表;最多 4 个",
    )
    adapter: str | None = Field(default=None, description="可选防御性过滤")
    group_id: str | None = Field(default=None, description="可选防御性过滤")


_PER_IMAGE_MAX_BYTES = 5 * 1024 * 1024  # 5MB
_TOTAL_RESULT_MAX_BYTES = 25 * 1024 * 1024  # 25MB


async def get_message_images_impl(
    inp: GetMessageImagesInput,
    *,
    store,
    cache,
    scope: CallerScope | None,
) -> list:
    """按 input message_ids 顺序取图,返回 MCP content[] 数组。

    每张图的可用性记在 header 的 results 数组里;不可用时 reason 取值:
      cache_miss    — sha256 NULL 或文件被淘汰
      too_large     — 单图超 5MB
      cap_exceeded  — 累计字节超 25MB 或 cap 触发
      not_found     — message_id 在 DB 不存在,**或不在调用方范围内**
    """
    # 范围判定必须按行做:入参的 adapter/group_id 是调用方自报的(而且是可选的),
    # message_id 是自增小整数、可枚举,所以真正的归属只能从 DB 反查。
    # 越权 id 一律按 not_found 处理 —— 回一个专门的 reason 等于给越权方一个
    # "这个 id 存在"的探测口。
    owners = store.get_message_owners(inp.message_ids)
    denied = {
        mid for mid, (adapter, group_id) in owners.items() if scope is None or not scope.allows(adapter, group_id or "")
    }
    if denied:
        logger.warning(
            f"[HERMES MCP] get_message_images 越权 message_id 已按 not_found 处理: {sorted(denied)} "
            f"(caller: {'unresolved' if scope is None else scope.describe()})"
        )

    metas = store.get_message_images_meta(inp.message_ids)
    results: list[dict[str, Any]] = []
    content_blocks: list[Any] = []
    total_bytes = 0
    cap_exceeded_remaining = False

    # 严格按入参顺序处理;同条消息内按 idx 升序(get_message_images_meta 已按此排)
    for message_id in inp.message_ids:
        if message_id in denied or message_id not in metas or not metas[message_id]:
            results.append(
                {
                    "message_id": message_id,
                    "image_idx": 0,
                    "available": False,
                    "reason": "not_found",
                }
            )
            continue
        for meta in metas[message_id]:
            idx = meta["idx"]
            if cap_exceeded_remaining:
                results.append(
                    {
                        "message_id": message_id,
                        "image_idx": idx,
                        "available": False,
                        "reason": "cap_exceeded",
                    }
                )
                continue
            sha = meta["sha256"]
            mime = meta["mime_type"]
            if not sha:
                results.append(
                    {
                        "message_id": message_id,
                        "image_idx": idx,
                        "available": False,
                        "reason": "cache_miss",
                    }
                )
                continue
            payload = cache.get_bytes(sha)
            if payload is None:
                results.append(
                    {
                        "message_id": message_id,
                        "image_idx": idx,
                        "available": False,
                        "reason": "cache_miss",
                    }
                )
                continue
            bytes_, real_mime = payload
            if len(bytes_) > _PER_IMAGE_MAX_BYTES:
                results.append(
                    {
                        "message_id": message_id,
                        "image_idx": idx,
                        "available": False,
                        "reason": "too_large",
                        "byte_size": len(bytes_),
                    }
                )
                continue
            if total_bytes + len(bytes_) > _TOTAL_RESULT_MAX_BYTES:
                cap_exceeded_remaining = True
                results.append(
                    {
                        "message_id": message_id,
                        "image_idx": idx,
                        "available": False,
                        "reason": "cap_exceeded",
                    }
                )
                continue
            b64 = base64.b64encode(bytes_).decode("ascii")
            content_blocks.append(TextContent(type="text", text=f"[image for m:{message_id} idx={idx}]"))
            content_blocks.append(ImageContent(type="image", data=b64, mimeType=mime or real_mime))
            total_bytes += len(bytes_)
            results.append(
                {
                    "message_id": message_id,
                    "image_idx": idx,
                    "available": True,
                    "mime": mime or real_mime,
                    "byte_size": len(bytes_),
                }
            )

    header = TextContent(
        type="text",
        text=json.dumps({"results": results}, ensure_ascii=False),
    )
    return [header, *content_blocks]
