"""统一出向发送器。

封装 alconna UniMessage 拼装 + send。
被两条路径共享:
  1. handlers/message.py 中收到结构化 reply_text 后发送
  2. mcp/tools/push_message.py 反向通道收到 push_message 后发送
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Sequence

import nonebot_plugin_alconna as alconna
from nonebot import logger
from nonebot.adapters import Bot

from ..config import plugin_config

# api_server 把本地生成的图片内联成 data:image/…;base64 URL——没有可抓取的
# http 地址,必须解码成字节直接附加。仅支持 base64 形态;RFC 2397 的
# percent-encoded 形态不支持(上游不产生)。
_IMAGE_DATA_URL_RE = re.compile(r"^data:(image/[\w.+-]+);base64,(.+)$", re.DOTALL)


def _parse_image_data_url(url: str) -> tuple[bytes, str, str] | None:
    """解析 base64 图片 data: URL → (raw_bytes, mimetype, b64_payload);不合法返回 None。

    宽容只做无损的两件事:去掉空白(payload 内联进 JSON 后可能被折行)、补齐缺失的
    `=` padding(补 padding 还原出的字节与原图一致)。**不做**有损修补:丢字符或
    放宽字符集校验能让半截 base64 也"解出来",但那是坏图——平台要么拒收要么显示
    破图,还把「上游把回复截断了」伪装成「发图成功」,连日志都查不到。宁可跳过。
    """
    m = _IMAGE_DATA_URL_RE.match(url)
    if m is None:
        return None
    mimetype = m.group(1)
    payload = re.sub(r"\s+", "", m.group(2))
    if not payload:
        return None

    rem = len(payload) % 4
    if rem in (2, 3):
        payload += "=" * (4 - rem)

    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        return None

    if not raw:
        return None

    return raw, mimetype, payload


# 截断 suffix 长度 = 13 字符;最终消息 = max_len + 13 字符,故意溢出 max_len。
# 所有已知 adapter 上限 ≥ 4500,默认 max_len=4000,溢出安全。
_TRUNCATION_SUFFIX = "\n\n…（消息过长,已截断）"

# Bot self-nickname per process (per bot.self_id). Used for forward-node "name" field.
# get_login_info is an extra OneBot RPC; cache result for the lifetime of the process.
_bot_nickname_cache: dict[str, str] = {}


async def get_bot_nickname(bot: Bot) -> str:
    """Return the bot's display nickname, cached per self_id. Fallback 'Bot' on failure.

    返回值一定是非空 str:除 forward-node 的 "name" 外,它还会进 prompt 的
    runtime_state.you 和 <recent_messages> 的 bot 行 —— 实现端返回 null / 非字符串
    时不能把那个对象原样渲染进 prompt。
    """
    self_id = bot.self_id
    if self_id in _bot_nickname_cache:
        return _bot_nickname_cache[self_id]
    nickname = "Bot"
    try:
        info = await bot.call_api("get_login_info")
        raw = info.get("nickname") if isinstance(info, dict) else None
        if isinstance(raw, str) and raw.strip():
            nickname = raw.strip()
    except Exception as exc:
        logger.debug(f"[OUTBOUND] get_login_info failed (self_id={self_id}): {exc}")
    _bot_nickname_cache[self_id] = nickname
    return nickname


def _split_into_forward_nodes(
    text: str,
    *,
    bot_self_id: str,
    bot_nickname: str,
    max_chars_per_node: int = 3500,
) -> list[dict]:
    """Split a long text into OneBot v11 forward-node dicts.

    Strategy:
      1. Split by '\\n\\n' (paragraph boundaries). Keep paragraphs that fit; if a
         paragraph alone exceeds max_chars_per_node, recursively split by '\\n',
         then by hard cut.
      2. Accumulate paragraphs into the same node until adding another would overshoot
         max_chars_per_node. Then start a new node.
      3. Each node carries a text segment in standard OneBot v11 node format.

    Returns a list of at least one node. Empty text returns one node with empty text.
    """

    def _make_node(segment: str) -> dict:
        return {
            "type": "node",
            "data": {
                "name": bot_nickname,
                "uin": bot_self_id,
                "content": [{"type": "text", "data": {"text": segment}}],
            },
        }

    def _split_chunk(chunk: str) -> list[str]:
        """Return list of strings each ≤ max_chars_per_node."""
        if len(chunk) <= max_chars_per_node:
            return [chunk]
        # Try splitting by newline
        parts = chunk.split("\n")
        if len(parts) > 1:
            return _accumulate(parts)
        # Hard cut as last resort
        result = []
        while chunk:
            result.append(chunk[:max_chars_per_node])
            chunk = chunk[max_chars_per_node:]
        return result

    def _accumulate(pieces: list[str]) -> list[str]:
        """Pack pieces into buckets of ≤ max_chars_per_node."""
        buckets: list[str] = []
        current = ""
        for piece in pieces:
            if len(piece) > max_chars_per_node:
                if current:
                    buckets.append(current)
                    current = ""
                buckets.extend(_split_chunk(piece))
            elif current and len(current) + 1 + len(piece) > max_chars_per_node:
                buckets.append(current)
                current = piece
            else:
                current = (current + "\n" + piece) if current else piece
        if current:
            buckets.append(current)
        return buckets

    paragraphs = text.split("\n\n")
    segments: list[str] = []
    for para in paragraphs:
        if len(para) > max_chars_per_node:
            segments.extend(_split_chunk(para))
        else:
            segments.append(para)

    # Accumulate paragraph-level segments into nodes
    nodes: list[dict] = []
    current_text = ""
    for seg in segments:
        separator = "\n\n" if current_text else ""
        if current_text and len(current_text) + len(separator) + len(seg) > max_chars_per_node:
            nodes.append(_make_node(current_text))
            current_text = seg
        else:
            current_text = current_text + separator + seg

    nodes.append(_make_node(current_text))
    return nodes


async def send_text_with_media(
    *,
    bot: Bot,
    target: alconna.Target,
    text: str,
    media_urls: Sequence[str] = (),
    at_user_id: str | None = None,
    reply_to_msg_id: str | None = None,
    adapter_name: str | None = None,
) -> bool:
    """组装并发送一条消息,返回是否发送成功。

    - target 是 alconna Target(只在内存可用)
    - 群聊默认在文本前加 At(at_user_id);为 None 不 at
    - reply_to_msg_id: alconna Reply 段在多 adapter 下兼容性不一,M1 不强制使用;
      保留此参数供未来兼容扩展(noqa: ARG001 直到接入)。
    - 空消息(text='' 且无有效媒体——http/https URL 或可解码的 data:image)直接返回
      False 不发,防 Task 12 push 路径在 Hermes 回空时构造空 UniMessage 发送出去。
    """
    original_len = len(text)
    max_len = plugin_config.hermes_max_length

    # B-1.2: 群聊 + OneBot v11 + 超长 → 合并转发 (而非截断)
    # 私聊永远走截断 (send_private_forward_msg 实现端覆盖差);
    # 非 onebotv11 同样保持截断。
    if (
        text
        and not target.private
        and adapter_name == "onebotv11"
        and plugin_config.hermes_long_reply_forward
        and original_len > max_len
    ):
        try:
            nickname = await get_bot_nickname(bot)
            nodes = _split_into_forward_nodes(
                text,
                bot_self_id=bot.self_id,
                bot_nickname=nickname,
            )
            # Media: append at end of last node so it ships in the same forward bundle.
            forward_media_count = 0
            if media_urls:
                last_node_content = nodes[-1]["data"]["content"]
                for u in media_urls:
                    if u.startswith(("http://", "https://")):
                        last_node_content.append({"type": "image", "data": {"url": u}})
                        forward_media_count += 1
                    elif u.startswith("data:"):
                        parsed = _parse_image_data_url(u)
                        if parsed is None:
                            logger.warning(f"[OUTBOUND] unsupported data: URL skipped (len={len(u)})")
                            continue
                        # OneBot v11 image 段的 base64:// file 形态,免落盘直传字节。
                        last_node_content.append({"type": "image", "data": {"file": f"base64://{parsed[2]}"}})
                        forward_media_count += 1
            # The actual merged forward send.
            await bot.call_api("send_group_forward_msg", group_id=int(target.id), messages=nodes)
            # At-mention: send AFTER the forward bundle so the @ ping is the most recent
            # message in the chat list. Moving it here also guarantees no double-@ if the
            # forward call failed and fell through to the truncation path.
            if at_user_id:
                try:
                    at_msg = alconna.UniMessage([alconna.At("user", at_user_id), " "])
                    await at_msg.send(target=target, bot=bot)
                except Exception as exc:
                    logger.debug(f"[OUTBOUND] post-forward @-ping failed: {exc}")
            logger.debug(
                f"[OUTBOUND] sent-via-forward target={target} text_len={original_len} "
                f"nodes={len(nodes)} media={forward_media_count}"
            )
            return True
        except Exception as exc:
            logger.warning(f"[OUTBOUND] long-reply forward failed, falling back to truncation: {exc}")
            # Fall through to existing truncation path

    msg = alconna.UniMessage()

    if not target.private and at_user_id:
        msg += alconna.UniMessage([alconna.At("user", at_user_id), " "])

    truncated = False
    if text:
        if original_len > max_len:
            text = text[:max_len] + _TRUNCATION_SUFFIX
            truncated = True
        msg += alconna.UniMessage(text)

    sent_media_count = 0
    for u in media_urls:
        if u.startswith(("http://", "https://")):
            msg += alconna.UniMessage(alconna.Image(url=u))
            sent_media_count += 1
        elif u.startswith("data:"):
            parsed = _parse_image_data_url(u)
            if parsed is None:
                # 不打 URL 本体——base64 可能几 MB,日志只记长度。
                logger.warning(f"[OUTBOUND] unsupported data: URL skipped (len={len(u)})")
                continue
            raw, mimetype, _ = parsed
            msg += alconna.UniMessage(alconna.Image(raw=raw, mimetype=mimetype))
            sent_media_count += 1
        else:
            # 其余 scheme(主机本地路径 / file:// / 相对路径)bot 侧取不到字节。
            # 以前这里没有 else,直接静默丢:调用方(尤其 MCP push_message)拿到
            # "发送成功",用户却没看到图,连日志都查不到。
            logger.warning(f"[OUTBOUND] 无法投递的媒体引用,已跳过(需要 http(s) 或 data:): {u[:120]}")

    # 空消息守卫:连 At 都没有(私聊 / 没传 at_user_id),text 空,无合法媒体 → 不发
    if not msg:
        logger.warning(
            f"[OUTBOUND] empty message skipped target={target} "
            f"(text_len={original_len} input_media={len(media_urls)} "
            f"valid_media={sent_media_count})"
        )
        return False

    try:
        await msg.send(target=target, bot=bot)
        logger.debug(
            f"[OUTBOUND] sent target={target} text_len={original_len} "
            f"truncated={truncated} media_sent={sent_media_count}/{len(media_urls)}"
        )
        return True
    except Exception as exc:
        logger.error(f"[OUTBOUND] 发送失败 target={target}: {exc}")
        return False
