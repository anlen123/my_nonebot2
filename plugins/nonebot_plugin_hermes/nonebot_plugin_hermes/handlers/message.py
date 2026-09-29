"""
消息处理器

priority=1 perception:写 MessageBuffer + BotRegistry,非阻塞
priority=98 main:触发判断 → reactive 决策 → 出向
"""

from __future__ import annotations

import asyncio
import re
import time
from contextlib import asynccontextmanager

import nonebot_plugin_alconna as alconna
from nonebot import logger, on_message
from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher
from nonebot.rule import Rule

from .. import mcp as _mcp  # lazy access to runtime singletons
from ..config import plugin_config
from ..core.hermes_client import (
    ChatResult,
    extract_response_media,
    hermes_client,
    maybe_extract_decision_reply_text,
    preview_raw,
)
from ..core.image_inline import inline_image_urls
from ..core.message_buffer import BufferedMessage
from ..core.outbound import get_bot_nickname, send_text_with_media
from ..core.prompt_builder import (
    build_passive_system_prompt,
    build_passive_user_content,
    build_reactive_system_prompt,
    build_reactive_user_content,
    select_followup_window,
)
from ..core.routing import resolve_target
from ..core.session import session_manager
from ..utils import check_isolation, get_adapter_name


async def _ignore_rule(event: Event) -> bool:
    try:
        msg_text = event.get_plaintext().strip()
    except Exception:
        return False
    if not msg_text:
        return True
    for prefix in plugin_config.hermes_ignore_prefix:
        if msg_text.startswith(prefix):
            return False
    return True


receive_message = on_message(rule=Rule(_ignore_rule), priority=98, block=True)
perception_message = on_message(priority=1, block=False)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _is_bot_at(uni_msg: alconna.UniMessage, bot_self_id: str) -> bool:
    if uni_msg.has(alconna.At):
        for seg in uni_msg[alconna.At]:
            if str(seg.target) == str(bot_self_id):
                return True
    return False


def _has_at_bot_in_original(event: Event, bot_self_id: str) -> bool:
    """检查 event.original_message 里是否有 @bot 段。

    OneBot v11 adapter 在分发事件前调 `_check_at_me`,把消息**开头/结尾**的 @bot 段
    从 event.message 中删除并置 to_me=True。下游 alconna 从 event.message 解析
    UniMessage 时已看不到那个 @bot,导致:
      - `_is_bot_at(uni_msg, ...)` 返回 False
      - 「@bot @other 怎么看」被当成只 @ 他人,is_mentioned 错误地为 False
      - emoji ack / chat 都不触发

    event.original_message 是 v11 在 _check_at_me 跑之前深拷贝的,@bot 还在那里。
    本函数兜底翻原始消息,告诉上游「用户确实 @ 了我」。

    非 OneBot adapter 一般无此剥离行为、不一定保留 original_message;`getattr` +
    try/except 让其他 adapter 安全返回 False(不退化原行为)。
    """
    original = getattr(event, "original_message", None)
    if not original:
        return False
    try:
        for seg in original:
            if getattr(seg, "type", None) != "at":
                continue
            data = getattr(seg, "data", None)
            if not isinstance(data, dict):
                continue
            if str(data.get("qq", "")) == str(bot_self_id):
                return True
    except Exception:
        pass
    return False


def _msg_at_only_other_users(uni_msg: alconna.UniMessage, bot_self_id: str) -> bool:
    """消息含 At 段、且所有 At target 都不是 bot 自身 → True。

    用于 reactive 入口 C 层过滤(模式 1 修复):active 窗口内,若消息明确 @ 了
    其他用户但**未点名 bot**,视作非本路径触发,只让 perception matcher 写 buffer,
    不进 chat() 决策。无 At 段时返回 False(走原有路径)。
    """
    if not uni_msg.has(alconna.At):
        return False
    return not _is_bot_at(uni_msg, bot_self_id)


# 单条昵称在 prompt 里的最大字符数。
# 中文 12 字 / 英文 24 字符,覆盖正常昵称;超长名片(常被用来塞动作短语/
# 系统消息伪装)会被截断成「前缀…」,降低被 LLM 当系统信号读的风险。
_MAX_NICKNAME_LEN = 24


def _sanitize_nickname(value) -> str | None:
    """清洗外部输入昵称,失败返 None。

    防御目标(顺手卫生 + 阻挡 [user=…] 定界符伪装):
    1. 控制字符 / 换行 / 零宽 → 全删,防止把多行片段塞进 prompt
    2. `]` 全角化,防止把 [user=…]: 标签提前闭合伪装成系统标签
    3. 长度 cap = _MAX_NICKNAME_LEN,超长截断 + 加省略号
    """
    if value is None:
        return None
    s = str(value)
    s = "".join(ch for ch in s if ch.isprintable())  # Cc/Cf/Cs 全过滤,ASCII space 保留
    s = s.replace("]", "］")  # 全角 `]`,与半角 `]` 视觉相近不破坏外观,但不闭合 [user=…] 标签
    s = s.strip()
    if not s:
        return None
    if len(s) > _MAX_NICKNAME_LEN:
        s = s[:_MAX_NICKNAME_LEN] + "…"
    return s


def _extract_sender_nickname(event: Event, adapter_name: str) -> str | None:
    """从 event 抽真实昵称(群名片优先),失败回 None。所有命中字段都过 _sanitize_nickname。

    保持 cross-adapter,不 import adapter-specific 类型,全靠 getattr 链——
    各 adapter event 形状不一致,Python 重命名/缺失字段都吞掉。

    覆盖的形状:
    - OneBot v11/v12: event.sender.card(群名片) → event.sender.nickname
    - QQ Official / Kook 等 .author: event.author.{global_name,nickname,username,name}
    - Discord: event.member.nick(server 名片) → 同上 author 链兜底
    - Telegram: event.from_.first_name + last_name → username
    """
    sender = getattr(event, "sender", None)
    if sender is not None:
        n = _sanitize_nickname(getattr(sender, "card", None)) or _sanitize_nickname(getattr(sender, "nickname", None))
        if n:
            return n

    member = getattr(event, "member", None)
    if member is not None:
        n = _sanitize_nickname(getattr(member, "nick", None)) or _sanitize_nickname(getattr(member, "nickname", None))
        if n:
            return n

    author = getattr(event, "author", None)
    if author is not None:
        for attr in ("global_name", "nickname", "username", "name"):
            n = _sanitize_nickname(getattr(author, attr, None))
            if n:
                return n

    from_user = getattr(event, "from_", None) or getattr(event, "from_user", None)
    if from_user is not None:
        first = _sanitize_nickname(getattr(from_user, "first_name", None))
        last = _sanitize_nickname(getattr(from_user, "last_name", None))
        if first or last:
            # 合并后再过一次 sanitize,确保拼接结果也受长度上限约束
            return _sanitize_nickname(" ".join(p for p in (first, last) if p))
        n = _sanitize_nickname(getattr(from_user, "username", None))
        if n:
            return n

    return None


_ACK_CANCEL_UNSUPPORTED_WARNED: set[str] = set()
"""Bot self_id 集合: 已经 WARN 过"撤销 emoji 在此 OneBot 实现端不支持"的 bot。

模块级 dedupe, 避免每个 turn 都刷 WARN。重启清空 (set), 不持久。"""


@asynccontextmanager
async def _ack_scope(
    bot: Bot,
    event: Event,
    *,
    adapter_name: str,
    is_explicit_trigger: bool,
    is_private: bool,
):
    """B-0: OneBot v11 emoji ack 回执 (set 进 / clear 出)。

    适用条件 (全部满足):
      - hermes_ack_feedback_enabled = True
      - is_explicit_trigger = True (仅用户主动 @ bot, bystander/notice 不贴)
      - adapter_name = 'onebotv11'
      - is_private = False (QQ NT 协议:emoji reactions 是群聊 only,
        私聊调 set_msg_emoji_like 实现端会 raise "只支持群聊消息")
      - event.message_id 可取

    撤销路径两套兼容:
      - LLOneBot 风格: 独立 endpoint `unset_msg_emoji_like`
      - NapCat 风格:   同 endpoint `set_msg_emoji_like` 加 set=False
    先试 LLOneBot 路径, 失败 fallback NapCat 路径。

    silently-fail 路径: 任何 API 错误吞掉, 绝不阻塞真实回复。
    set 失败 → 不尝试 clear (避免无意义错误日志); set 成功 → clear 在 finally 内,
    chat() 抛异常 / 取消 / Ctrl-C 都会触发清理。

    **已知限制**: 老版本 LLOneBot (< 大约 2024 中) 两条撤销路径都不支持
    (unset endpoint 不存在, set=False 也不被识别)——emoji 会持久存在,
    我们一次性 WARN 告知用户、建议升级 / 切 NapCat / 接受持久标记。

    notice 触发的 synthesized 路径 (戳一戳/入群) 不进入本 scope——它们没有'原消息'可贴。
    """
    enabled = (
        plugin_config.hermes_ack_feedback_enabled
        and is_explicit_trigger
        and adapter_name == "onebotv11"
        and not is_private
    )
    if not enabled:
        yield
        return

    msg_id = getattr(event, "message_id", None)
    if msg_id is None:
        yield
        return

    emoji_id = plugin_config.hermes_ack_emoji_id
    set_ok = False
    try:
        # 添加表情两边一致: set_msg_emoji_like(message_id, emoji_id) 即可
        # (NapCat 的 set 参数 Optional 默认 true, LLOneBot 无该参数)。
        await bot.call_api("set_msg_emoji_like", message_id=msg_id, emoji_id=emoji_id)
        set_ok = True
    except Exception as e:
        logger.debug(f"[HERMES ack] set failed (msg_id={msg_id} emoji_id={emoji_id}): {e}")

    try:
        yield
    finally:
        if set_ok:
            # 撤销表情两边风格不同:
            #   - LLOneBot: 独立 endpoint `unset_msg_emoji_like`
            #   - NapCat:   同 endpoint `set_msg_emoji_like` 加 set=False
            # 先试 LLOneBot 路径, 失败 fallback 到 NapCat 路径。两边都不识别就静默。
            cleared = False
            unset_unsupported = False
            try:
                await bot.call_api("unset_msg_emoji_like", message_id=msg_id, emoji_id=emoji_id)
                cleared = True
            except Exception as e:
                err_str = str(e).lower()
                # OneBot 标准 retcode 1404 = "不支持的 api"; 不同实现端也可能用 "unsupported"
                # 或直接走 retcode != 0 + message 含 "不支持". 字串兜底, 即使匹配漏了
                # 也只是少一次 WARN, 主功能不受影响。
                unset_unsupported = "1404" in err_str or "unsupported" in err_str or "不支持" in err_str
                logger.debug(f"[HERMES ack] unset failed (LLOneBot path, msg_id={msg_id}): {e}")
            if not cleared:
                try:
                    await bot.call_api(
                        "set_msg_emoji_like",
                        message_id=msg_id,
                        emoji_id=emoji_id,
                        set=False,
                    )
                except Exception as e:
                    logger.debug(f"[HERMES ack] clear-via-set=false failed (NapCat path, msg_id={msg_id}): {e}")
                else:
                    # set=False 没抛错。但如果是老 LLOneBot, 它会接受请求却不真正撤销
                    # (LLOneBot 早期 set 参数不识别)。和 unset 不支持是同一类版本陈旧问题——
                    # 一次性 WARN 告知用户。NapCat / 新 LLOneBot 不会进这个分支
                    # (走 unset 那条已 cleared=True)。
                    if unset_unsupported:
                        bot_id = str(bot.self_id)
                        if bot_id not in _ACK_CANCEL_UNSUPPORTED_WARNED:
                            _ACK_CANCEL_UNSUPPORTED_WARNED.add(bot_id)
                            logger.warning(
                                f"[HERMES ack] bot {bot_id} 所在 OneBot 实现端不支持撤销 emoji "
                                f"(unset_msg_emoji_like 1404; set=False 也可能空转)。emoji 将永久标记。"
                                f"建议: 升级 LLOneBot / 改用 NapCat / 设 HERMES_ACK_FEEDBACK_ENABLED=false。"
                            )


async def _emit_busy_notice(
    bot: Bot,
    adapter_name: str,
    original_msg_id: str | int | None,
) -> None:
    """Depth-cap 触顶丢 explicit pending 时, 在原消息上贴 busy emoji, 不撤销。

    限制:
      - adapter 非 onebotv11 / msg_id 缺失 → no-op + WARN 日志
      - emoji API 报错 → swallow + DEBUG 日志, 不文本兜底

    与 _ack_scope 的区别: ack 走"工作中→撤销"两阶段,busy 是"工作不下去→留印记"一次性,
    生命周期不耦合, emoji_id 也不同 (busy 默认 hermes_busy_emoji_id = 97 /擦汗,
    ack 默认 341 /打招呼)。
    """
    if adapter_name != "onebotv11" or original_msg_id is None:
        logger.warning(
            f"[HERMES busy_notice] no-op: adapter={adapter_name} "
            f"msg_id={original_msg_id} (only onebotv11 group supports emoji notice)"
        )
        return
    try:
        await bot.call_api(
            "set_msg_emoji_like",
            message_id=original_msg_id,
            emoji_id=plugin_config.hermes_busy_emoji_id,
        )
    except Exception as e:
        logger.debug(f"[HERMES busy_notice] emit failed (msg_id={original_msg_id}): {e}")


async def _extract_image_urls(uni_msg: alconna.UniMessage, bot: Bot, adapter_name: str) -> list[str]:
    """从 UniMessage 中抽出图片 URL 列表(可直接 HTTP GET 拿字节的那种)。

    多 adapter 行为不一致:
    - OneBot v11 / QQ Official / Discord 等:alconna 直接在 Image 段上填好 `.url`,
      最廉价路径,优先用
    - Telegram:alconna 只填 `.id`(就是 file_id),URL 必须二次调
      `bot.get_file(file_id)` 拿到 `file_path` 后拼成 `https://api.telegram.org/
      file/bot<TOKEN>/<file_path>`。这条路径 URL 里**带 token**,但 fetcher
      和 DB 都本地,落地可接受;且 file_path 只有 ~1h 有效,异步 fetcher 必须
      及时抓字节进 ImageCache,后面 MCP 工具读 cache 不再依赖 URL
    - 其他 adapter:只看 `.url`,没的话就放弃这张图(不抛、不 fail bot)

    本函数是 async 因为 telegram 分支要 await bot.get_file。
    """
    urls: list[str] = []
    if not uni_msg.has(alconna.Image):
        return urls
    adapter_lc = (adapter_name or "").lower()
    for img in uni_msg[alconna.Image]:
        # B-0: QQ 大表情包不进 vision URL list (语义价值极低、白烧 vision token)
        if getattr(img, "sticker", False):
            continue
        url = getattr(img, "url", None)
        if url and isinstance(url, str) and url.startswith(("http://", "https://")):
            urls.append(url)
            continue
        file_id = getattr(img, "id", None)
        if not file_id:
            continue
        if "telegram" in adapter_lc:
            resolved = await _resolve_telegram_file_url(bot, file_id)
            if resolved:
                urls.append(resolved)
                continue
        # 其他 adapter 但 Image 没 .url 的情况:debug 一行,不当错误处理
        logger.debug(
            f"[image] skipped image segment with no resolvable URL (adapter={adapter_lc} id={file_id[:24]}...)"
        )
    return urls


def _collect_nontext_placeholders(uni_msg: alconna.UniMessage) -> list[str]:
    """扫描非文本/普通图段,返回占位文本列表 (顺序近似按段类型聚合)。

    覆盖:
      - Image.sticker=True (QQ 大表情包) → [表情包]
      - Voice → [语音]
      - Video → [视频]
      - Emoji (QQ face 段) → [表情:<name>] 或 [表情] (name 缺失时)
      - File → [文件:<name>] 或 [文件:未命名] (name 缺失时)

    与现有 [图片] 占位策略一致——仅追加到 msg_text 末尾,不试图与文本段 interleave。
    普通 (非 sticker) Image 不在本函数处理,沿用 _extract_image_urls + [图片] 占位流。
    """
    placeholders: list[str] = []
    if uni_msg.has(alconna.Image):
        for img in uni_msg[alconna.Image]:
            if getattr(img, "sticker", False):
                placeholders.append("[表情包]")
    if uni_msg.has(alconna.Voice):
        for _v in uni_msg[alconna.Voice]:
            placeholders.append("[语音]")
    if uni_msg.has(alconna.Video):
        for _v in uni_msg[alconna.Video]:
            placeholders.append("[视频]")
    if uni_msg.has(alconna.Emoji):
        for face in uni_msg[alconna.Emoji]:
            name = getattr(face, "name", None)
            placeholders.append(f"[表情:{name}]" if name else "[表情]")
    if uni_msg.has(alconna.File):
        # File 与 Emoji 的 fallback 风格故意不一致:文件 metadata 几乎只剩"有/没有名字"这两个信号,
        # 用 [文件:未命名] 保住"曾有文件且名字丢了"的语义,比裸 [文件] 更具信息量。
        for f in uni_msg[alconna.File]:
            name = getattr(f, "name", None) or "未命名"
            placeholders.append(f"[文件:{name}]")
    return placeholders


def _collect_at_placeholders(
    uni_msg: alconna.UniMessage,
    *,
    event: Event | None = None,
    bot_self_id: str | None = None,
) -> list[str]:
    """扫描 At / AtAll 段,返回 ['@全体', '@<user_id>', ...] 占位列表。

    alconna `extract_plain_text()` 默认丢掉 At 段,Hermes 在 prompt 里看不到
    "当前消息 @ 了谁"。在引用 bot 旧消息 + @ 群友 这类场景下,LLM 只能凭
    引用关系判归属,误判"归你"。把 At 信息按 `@<target>` 回填到 plain text
    末尾,让 decision_protocol 的"@ 了别人 → 不归你"规则有可用证据。

    AtAll 放在 At 之前 — `@全体` 是更强的寻址信号,优先呈现。

    target 一律按字符串输出。bot 自己被 @ 时不特判,Hermes 通过对比 recent_messages
    里 [bot] 行的 `id=` 字段即可识别。

    若同时给出 event + bot_self_id 且 uni_msg 里没 @bot 但 event.original_message 里
    有(OneBot v11 _check_at_me 剥走的情况),把 `@<bot_self_id>` 补到列表开头 ——
    否则 Hermes 在多 @ 场景下看到「只 @ 了他人」会按 decision_protocol 抑制回复。
    """
    placeholders: list[str] = []
    if uni_msg.has(alconna.AtAll):
        for _ in uni_msg[alconna.AtAll]:
            placeholders.append("@全体")
    if uni_msg.has(alconna.At):
        for at in uni_msg[alconna.At]:
            placeholders.append(f"@{at.target}")
    if event is not None and bot_self_id is not None:
        bot_at_visible = any(p == f"@{bot_self_id}" for p in placeholders)
        if not bot_at_visible and _has_at_bot_in_original(event, bot_self_id):
            placeholders.insert(0, f"@{bot_self_id}")
    return placeholders


# --- Phase B-1: 合并转发消息提取 ---


def _node_summary(node: dict) -> str | None:
    """将 OneBot get_forward_msg 返回的单条节点转成单行摘要。

    节点结构在不同 OneBot 实现端之间不完全统一,主要存在两种:
      A) flat + content (NapCat / LuckyLilliaBot 现行):
         {"sender": {"nickname": "...", "card": "..."}, "content": [...segments...],
          "time": ..., "message_format": "array", "message_type": "..."}
      B) flat + message (旧 go-cqhttp / 部分 LLOneBot 版本):
         {"sender": {...}, "name": "...", "message": [...segments...]}
    本函数对二者都接;优先取 content,缺则取 message。

    空内容节点返回 None(调用方跳过,避免裸 'Unknown: ' 行)。
    """
    sender = node.get("sender", {})
    nickname = sender.get("nickname") or sender.get("card") or node.get("name") or "Unknown"
    parts: list[str] = []
    segs = node.get("content") or node.get("message") or []
    for seg in segs:
        seg_type = seg.get("type", "")
        data = seg.get("data", {}) if isinstance(seg.get("data"), dict) else {}
        if seg_type == "text":
            text = data.get("text", "")
            if text:
                parts.append(text)
        elif seg_type == "image":
            parts.append("[图片]")
        elif seg_type == "record":
            parts.append("[语音]")
        elif seg_type == "video":
            parts.append("[视频]")
        elif seg_type == "file":
            name = data.get("name") or data.get("file") or "未命名"
            parts.append(f"[文件:{name}]")
        elif seg_type == "face":
            name = data.get("name")
            parts.append(f"[表情:{name}]" if name else "[表情]")
        elif seg_type == "forward":
            content = data.get("content")
            count = len(content) if isinstance(content, list) else "?"
            parts.append(f"[嵌套合并转发 ({count} 条)]")
        # other types → skip
    joined = "".join(parts).strip()
    if not joined:
        return None
    return f"{nickname}: {joined}"


async def _extract_forward_full(
    uni_msg: alconna.UniMessage,
    bot: Bot,
    *,
    adapter_name: str,
) -> str | None:
    """提取合并转发消息,返回 <forwarded_messages count="N">...</forwarded_messages> 块。

    仅支持 onebotv11/onebotv12。其他适配器返回 None(调用方自行降级)。
    调用 get_forward_msg 失败时返回自闭合 fetch_failed 标签,不抛出。
    """
    # TODO(B-1.3): onebotv12 uses the same `get_forward_msg` API name based on OneBot
    # spec convergence, but this has not been verified against a live v12 deployment.
    # If the v12 API name differs, the call below will raise and we'll return the
    # fetch_failed self-closing tag — degraded but non-crashing. Verify with a real
    # v12 adapter and either confirm or branch the call.
    if adapter_name not in {"onebotv11", "onebotv12"}:
        return None

    refs = uni_msg[alconna.Reference]
    ref_id: str | None = None
    for ref in refs:
        if ref.id:
            ref_id = ref.id
            break
    if ref_id is None:
        return None

    try:
        resp = await bot.call_api("get_forward_msg", id=ref_id)
    except Exception as exc:
        logger.warning(
            f"[HERMES forward] get_forward_msg failed adapter={adapter_name} "
            f"ref_id={ref_id}: {type(exc).__name__}: {exc}"
        )
        return '<forwarded_messages count="?" status="fetch_failed"/>'

    nodes: list = []
    if isinstance(resp, dict):
        nodes = resp.get("messages") or []
    elif isinstance(resp, list):
        nodes = resp

    total_nodes = len(nodes)

    max_nodes = plugin_config.hermes_forward_extract_max_nodes
    max_chars = plugin_config.hermes_forward_extract_max_chars

    lines: list[str] = []
    total_chars = 0
    omitted = 0  # default: loop finished naturally, nothing hidden

    for i, node in enumerate(nodes):
        summary = _node_summary(node)
        if summary is None:
            continue
        next_len = total_chars + len(summary) + (1 if lines else 0)
        if next_len > max_chars and lines:
            # Char-limit truncation requires at least one line already collected — better to
            # overshoot max_chars with a single huge first node than to emit a content-less
            # wrapper. Rare in practice (a 2KB single node under an 800-char cap), but the
            # policy is "always show something."
            lines.append("[...因字符上限截断]")
            omitted = 0  # char path doesn't report a numeric "other N"
            break
        lines.append(summary)
        total_chars = next_len
        if len(lines) >= max_nodes:
            # node truncation: count remaining indices not yet examined
            omitted = total_nodes - (i + 1)
            if omitted > 0:
                lines.append(f"[...另有 {omitted} 条已省略]")
            break

    if not lines:
        return '<forwarded_messages count="?" status="fetch_failed"/>'

    content = "\n".join(lines)
    return f'<forwarded_messages count="{total_nodes}">\n{content}\n</forwarded_messages>'


# 自闭合 forwarded_messages 标签(fetch_failed / preview 两种变体共用此形态)。
# _summarize_forward 的透传判断与 _forward_full_for_store 的落库过滤共用。
_FORWARD_SELF_CLOSING_RE = re.compile(r"<forwarded_messages [^>]+/>")


def _summarize_forward(full_block: str, *, max_chars: int = 120) -> str:
    """将 _extract_forward_full 返回的多行块压缩为单行自闭合预览标签。

    用于 MessageBuffer / perception buffer 存储,避免 <recent_messages> 过度膨胀。
    自闭合输入(含 fetch_failed 变体)原样返回。
    """
    # Already self-closing → unchanged
    if _FORWARD_SELF_CLOSING_RE.match(full_block.strip()):
        return full_block.strip()

    # Extract count attribute
    count_match = re.search(r'count="([^"]*)"', full_block)
    count_val = count_match.group(1) if count_match else "?"

    # Extract inner lines (between opening and closing tag)
    inner_match = re.search(r"<forwarded_messages [^>]*>\n(.*?)\n</forwarded_messages>", full_block, re.DOTALL)
    if not inner_match:
        return f'<forwarded_messages count="{count_val}" preview=""/>'

    inner = inner_match.group(1)
    raw_lines = [ln.strip() for ln in inner.splitlines() if ln.strip()]

    # Overhead: '<forwarded_messages count="N" preview=""/>'
    overhead = len(f'<forwarded_messages count="{count_val}" preview=""/>')
    budget = max_chars - overhead

    preview_parts: list[str] = []
    used = 0
    for line in raw_lines:
        compressed = line[:30] + "…" if len(line) > 30 else line
        # separator cost
        sep_cost = len(" / ") if preview_parts else 0
        if used + sep_cost + len(compressed) > budget:
            break
        preview_parts.append(compressed)
        used += sep_cost + len(compressed)

    preview = " / ".join(preview_parts)
    # Escape to keep attribute XML-safe: & first, then < and >, then "
    preview = preview.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "'")

    return f'<forwarded_messages count="{count_val}" preview="{preview}"/>'


def _forward_full_for_store(forward_full: str | None) -> str | None:
    """真实展开块才值得持久化;fetch_failed 自闭合变体无内容,不落库。"""
    if forward_full is None:
        return None
    if _FORWARD_SELF_CLOSING_RE.match(forward_full.strip()):
        return None
    return forward_full


# Telegram `bot.get_file(file_id)` → URL 短期缓存。
# 同一事件经过 priority=1 perception + priority=98 main handler 两个 matcher,
# 各自跑一次 _extract_image_urls,如果不缓存就要打两次 Telegram API
# (~300-500ms 网络往返/次)。
#
# TTL 设 60 秒:Telegram 自己返的 file_path 大约 1 小时有效,我们 60s 内
# 复用足够覆盖事件突发,且远小于真实失效窗口,不引入隐患。
_RESOLVED_URL_TTL_S = 60.0
_resolved_url_cache: dict[tuple[str, str], tuple[str, float]] = {}


async def _resolve_telegram_file_url(bot: Bot, file_id: str) -> str | None:
    """Telegram file_id → 可拉的 HTTPS URL。失败返 None,perception 不崩。

    URL 里含 token,只在 plugin 本地 DB / fetcher 流转(不会进 prompt / MCP 返回)。
    file_path 一般 ~1h 失效,fetcher 必须及时抓——本设计走 perception 异步触发,
    秒级到达 fetcher,不会拖到失效。

    短期缓存:同一 (bot_self_id, file_id) 60s 内复用上次 resolve 结果,避免
    perception + main handler 两层各调一次 Telegram getFile API。
    """
    cache_key = (str(getattr(bot, "self_id", "?")), file_id)
    now = time.monotonic()
    cached = _resolved_url_cache.get(cache_key)
    if cached and cached[1] > now:
        return cached[0]
    try:
        file = await bot.get_file(file_id=file_id)
    except Exception as exc:
        logger.warning(f"[image] telegram get_file failed for file_id={file_id[:24]}...: {exc}")
        return None
    file_path = getattr(file, "file_path", None)
    token = getattr(getattr(bot, "bot_config", None), "token", None)
    if not file_path or not token:
        logger.warning(f"[image] telegram get_file returned no file_path/token (file_id={file_id[:24]}...)")
        return None
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    _resolved_url_cache[cache_key] = (url, now + _RESOLVED_URL_TTL_S)
    # 偶尔顺手清掉过期 entry,避免长期跑爆字典(O(N) 但 N 很小)
    if len(_resolved_url_cache) > 256:
        expired = [k for k, (_u, exp) in _resolved_url_cache.items() if exp <= now]
        for k in expired:
            _resolved_url_cache.pop(k, None)
    return url


@perception_message.handle()
async def handle_perception(bot: Bot, event: Event):
    """记录消息到 MessageBuffer + 维护 BotRegistry。"""
    if _mcp.message_buffer is None or _mcp.bot_registry is None:
        return

    try:
        target = alconna.get_target()
        adapter_name = get_adapter_name(target)
        user_id = event.get_user_id()
    except Exception:
        return

    if user_id == str(bot.self_id):
        return

    try:
        uni_msg = alconna.UniMessage.generate_without_reply(event=event, bot=bot)
    except Exception:
        return

    msg_text = uni_msg.extract_plain_text().strip()
    image_urls = await _extract_image_urls(uni_msg, bot, adapter_name)
    nickname = _extract_sender_nickname(event, adapter_name) or user_id

    # 文本太长截断
    max_len = plugin_config.hermes_perception_text_length
    if msg_text and len(msg_text) > max_len:
        msg_text = msg_text[:max_len] + "..."

    if image_urls and plugin_config.hermes_perception_image_mode != "none":
        placeholder = " [图片]"
        msg_text = (msg_text + placeholder) if msg_text else placeholder.strip()

    # B-0: 非文本段占位 (sticker/voice/video/emoji)
    nontext_placeholders = _collect_nontext_placeholders(uni_msg)
    if nontext_placeholders:
        suffix = " ".join(nontext_placeholders)
        msg_text = (msg_text + " " + suffix) if msg_text else suffix

    # At 段回填:plain_text 默认丢 At,补回让历史里 "@C 你看" 这类指向可读
    # 传 event + bot_self_id 让 helper 检测「OneBot v11 _check_at_me 剥走的 @bot」并补回
    at_placeholders = _collect_at_placeholders(uni_msg, event=event, bot_self_id=str(bot.self_id))
    if at_placeholders:
        suffix = " ".join(at_placeholders)
        msg_text = (msg_text + " " + suffix) if msg_text else suffix

    # B-1.1: 合并转发提取 (perception 入站存 summary 版,避免 buffer 膨胀)
    forward_full = await _extract_forward_full(uni_msg, bot, adapter_name=adapter_name)
    if forward_full is not None:
        summary = _summarize_forward(forward_full)
        msg_text = f"{msg_text}\n{summary}" if msg_text else summary

    if not msg_text and not image_urls:
        return

    now = _now_ms()
    group_id = None if target.private else target.id

    # 写 MessageBuffer
    if plugin_config.hermes_perception_enabled or plugin_config.hermes_active_session_enabled:
        _mcp.message_buffer.append(
            BufferedMessage(
                ts=now,
                adapter=adapter_name,
                group_id=group_id,
                user_id=user_id,
                nickname=nickname,
                content=msg_text,
                image_urls=image_urls,
                is_bot=False,
                forward_content=_forward_full_for_store(forward_full),
            )
        )

    # 写 BotRegistry
    scope = "private" if target.private else "group"
    scope_id = user_id if target.private else (group_id or "")
    if scope_id:
        _mcp.bot_registry.upsert(
            adapter=adapter_name,
            scope=scope,
            scope_id=scope_id,
            bot_self_id=str(bot.self_id),
            target=target,
            ts=now,
        )

    logger.debug(
        f"[HERMES perception] {adapter_name}/{scope}/{scope_id} user={user_id} "
        f"text_len={len(msg_text)} imgs={len(image_urls)}"
    )


@receive_message.handle()
async def handle_message(bot: Bot, event: Event, matcher: Matcher):
    if _mcp.message_buffer is None or _mcp.active_sessions is None:
        return

    try:
        target = alconna.get_target()
    except Exception:
        matcher.skip()

    adapter_name = get_adapter_name(target)
    user_id = event.get_user_id() or "user"
    if user_id == str(bot.self_id):
        matcher.skip()

    if not check_isolation(event, target):
        logger.debug(
            f"[HERMES skip] isolation_denied adapter={get_adapter_name(target)} "
            f"target={target.id} private={target.private} user={user_id}"
        )
        matcher.skip()

    # 引用消息提取
    replied_text = ""
    replied_image_urls: list[str] = []
    if hasattr(event, "reply") and event.reply:
        try:
            replied_message = await alconna.UniMessage.generate(message=event.reply.message, bot=bot)
            replied_text = replied_message.extract_plain_text().strip()
            replied_image_urls = await _extract_image_urls(replied_message, bot, adapter_name)
            replied_at = _collect_at_placeholders(replied_message)
            if replied_at:
                suffix = " ".join(replied_at)
                replied_text = (replied_text + " " + suffix).strip() if replied_text else suffix
            # 引用折叠消息:被引 UniMessage 里的 forward 段带 id,直接展开内联。
            # fetch_failed 自闭合变体也拼——「这里有个取不到的折叠」比空白强。
            replied_forward = await _extract_forward_full(replied_message, bot, adapter_name=adapter_name)
            if replied_forward is not None:
                replied_text = f"{replied_text}\n{replied_forward}" if replied_text else replied_forward
            if replied_image_urls and not replied_text:
                replied_text = "[图片]"
        except Exception as e:
            logger.warning(f"[HERMES] 提取引用消息失败: {e}")

    try:
        uni_msg = alconna.UniMessage.generate_without_reply(event=event, bot=bot)
    except Exception:
        matcher.skip()

    msg_text = uni_msg.extract_plain_text().strip()
    if replied_text:
        msg_text = f"(引用: {replied_text}) {msg_text}".strip()

    image_urls = await _extract_image_urls(uni_msg, bot, adapter_name)
    image_urls.extend(replied_image_urls)
    nickname = _extract_sender_nickname(event, adapter_name) or user_id

    # B-0: 非文本段占位拼到 msg_text 末尾。注意这里**不重复**对 replied_message 做
    # collect: 引用消息已通过 replied_text 走 (引用:...) 前缀进来,
    # 引用里的 voice/video 用户感知较低,且会让占位重复堆叠。
    nontext_placeholders = _collect_nontext_placeholders(uni_msg)
    if nontext_placeholders:
        suffix = " ".join(nontext_placeholders)
        msg_text = (msg_text + " " + suffix).strip() if msg_text else suffix

    # 在 at_placeholders 注入**之前**快照:@ 是寻址不是内容,纯 @-only 消息
    # (用户 tab 补全误发 / 单 @ 当 ping) 不应触发 chat。perception 入口仍会写
    # buffer (历史里能看到 @),只是 main handler 不进 chat。
    has_real_content = bool(msg_text) or bool(image_urls)

    # At 段回填:让 Hermes 在 decision_protocol 里能看到当前消息 @ 了谁;keyword
    # 模式的 startswith 检测不受影响(at_placeholders 拼在末尾,不在前缀)
    # 传 event + bot_self_id 让 helper 检测「OneBot v11 _check_at_me 剥走的 @bot」并补回
    at_placeholders = _collect_at_placeholders(uni_msg, event=event, bot_self_id=str(bot.self_id))
    if at_placeholders:
        suffix = " ".join(at_placeholders)
        msg_text = (msg_text + " " + suffix).strip() if msg_text else suffix

    # B-1.1: 合并转发提取 (main path 用 full 版,LLM 当前 turn 看到展开后的转发内容)
    # 在 keyword-stripping 之前追加:keyword 前缀作用于 msg_text 开头的手打文本,
    # 转发块拼在末尾,startswith(kw) 仍能匹配前缀,strip 后转发块完整保留。
    forward_full = await _extract_forward_full(uni_msg, bot, adapter_name=adapter_name)
    if forward_full is not None:
        msg_text = f"{msg_text}\n{forward_full}" if msg_text else forward_full
        has_real_content = True

    logger.debug(
        f"[HERMES recv] adapter={adapter_name} target={target.id} private={target.private} "
        f"user={user_id} nick={nickname!r} is_tome={event.is_tome()} self_id={bot.self_id!r} "
        f"at_targets={[str(s.target) for s in uni_msg[alconna.At]] if uni_msg.has(alconna.At) else []} "
        f"text_len={len(msg_text)} imgs={len(image_urls)}"
    )

    if not has_real_content:
        logger.debug(f"[HERMES skip] empty adapter={adapter_name} user={user_id}")
        matcher.skip()

    group_id = None if target.private else target.id
    now = _now_ms()

    # --- 触发判断 ---
    # addressed_to_bot 是与触发正交的寻址事实:平台确认「这条冲 bot 说的」
    # (真 @ / 引用 bot 的消息 / 唤起词命中)。它随消息进 prompt 的 runtime_state,
    # 让归属判断不必从回填的裸 `@<id>` 去猜。与 trigger 模式无关:`all` 下泛聊天
    # explicit 但不寻址,真 @bot 则照样寻址。
    is_explicit_trigger = False
    addressed_to_bot = False
    if target.private:
        is_explicit_trigger = True
    else:
        # event.is_tome() 在用户引用 bot 旧消息时也会被置 True;若当前消息含 @ 段但
        # 全部指向他人,视作"用户在跟别人讲话,只是顺手引用了 bot 那条",不算显式 @bot。
        #
        # has_bot_at 要兜底翻 event.original_message:OneBot v11 adapter 在 _check_at_me
        # 里会把消息开头/结尾的 @bot 段从 event.message 中剥掉(并置 to_me=True),下游
        # uni_msg 里看不到 @bot,但用户其实 @ 了。否则「@bot @other 怎么看」会因
        # _msg_at_only_other_users=True 触发 quoted-only 抑制,完全不响应(连 emoji 都没)。
        has_bot_at = _is_bot_at(uni_msg, str(bot.self_id)) or _has_at_bot_in_original(event, str(bot.self_id))
        is_mentioned = has_bot_at or (event.is_tome() and not _msg_at_only_other_users(uni_msg, str(bot.self_id)))
        addressed_to_bot = is_mentioned
        trigger_mode = plugin_config.hermes_group_trigger
        if trigger_mode == "at":
            is_explicit_trigger = is_mentioned
        elif trigger_mode == "all":
            is_explicit_trigger = True
        elif trigger_mode == "keyword":
            for kw in plugin_config.hermes_keywords:
                if msg_text.startswith(kw):
                    msg_text = msg_text[len(kw) :].strip()
                    is_explicit_trigger = True
                    addressed_to_bot = True  # 唤起词就是点名
                    break
            if not is_explicit_trigger and is_mentioned:
                is_explicit_trigger = True

    # --- M1 核心:活跃态分支 ---
    in_active_window = (
        not target.private
        and plugin_config.hermes_active_session_enabled
        and group_id is not None
        and _mcp.active_sessions.is_active(adapter_name, group_id, now)
    )

    if not is_explicit_trigger and not in_active_window:
        logger.debug(f"[HERMES skip] not_active_not_explicit adapter={adapter_name} group={group_id} user={user_id}")
        matcher.skip()

    # C: 活跃窗口内,若消息只 @ 他人未点名 bot,视作非本路径触发,只让 perception
    # 写 buffer,不进 chat() 决策。修「跨目标 @ 误抢」模式 1。
    # 显式触发(at-bot)早已在上面计入 is_explicit_trigger,不会到这里被过滤。
    if in_active_window and not is_explicit_trigger and _msg_at_only_other_users(uni_msg, str(bot.self_id)):
        logger.debug(
            f"[HERMES reactive] skip: msg @s only other users (adapter={adapter_name} group={group_id} user={user_id})"
        )
        matcher.skip()

    # 显式触发:进入 / 续期活跃态(群聊场景)
    if is_explicit_trigger and not target.private and group_id and plugin_config.hermes_active_session_enabled:
        _mcp.active_sessions.trigger(adapter_name, group_id, user_id, now_ms=now)
        logger.info(f"[HERMES] active_session triggered/renewed: {adapter_name}/{group_id} by {user_id}")

    if not target.private:
        logger.info(
            f"[HERMES] dispatch: group={group_id} explicit={is_explicit_trigger} "
            f"in_active={in_active_window} mode="
            f"{'reactive' if plugin_config.hermes_active_session_enabled else 'passive'}"
        )

    # --- 调用 Hermes (用 _ack_scope 包住, 显式触发会在用户消息上贴 emoji 回执) ---
    async with _ack_scope(
        bot,
        event,
        adapter_name=adapter_name,
        is_explicit_trigger=is_explicit_trigger,
        is_private=target.private,
    ):
        if target.private or not plugin_config.hermes_active_session_enabled:
            # 原 v0.1.6 等价路径:passive 模式,raw_text 直接当回复
            await _handle_passive_path(
                bot=bot,
                target=target,
                adapter_name=adapter_name,
                user_id=user_id,
                nickname=nickname,
                group_id=group_id,
                text=msg_text,
                image_urls=image_urls,
                is_private=target.private,
                now_ms=now,
                event_msg_id=getattr(event, "message_id", None),
            )
            return

        # 群聊 + 活跃态启用 → reactive 决策
        await _handle_reactive_path(
            bot=bot,
            target=target,
            adapter_name=adapter_name,
            user_id=user_id,
            nickname=nickname,
            group_id=group_id,
            text=msg_text,
            image_urls=image_urls,
            is_explicit_trigger=is_explicit_trigger,
            addressed_to_bot=addressed_to_bot,
            now_ms=now,
            event_msg_id=getattr(event, "message_id", None),
        )


# 上游 locked 类持久化失败 = 别的 Hermes 进程正在写 state.db,上游要求「过一会再发一次」。
# 等一拍再重试,让持有写锁的一方先落库;不做指数退避,单次补偿够覆盖锁窗口,
# 拖长只会把群里的回复延迟摊得更明显。
_PERSISTENCE_RETRY_DELAY_S = 1.0


# 同 turn 去重的判据。刻意很窄:只认「近乎逐字复述」。
#
# 早期规则是"本 turn 内 push 过就把 submit_decision 整条抑制",它把最自然的用法
# ——push 一句「在查」、decision 给真答案——也一起打死了:答案被静默吞掉,而 agent
# 以为自己发出去了,还会向用户汇报已发送。包含关系不能当判据,「引用刚说的话 + 补上
# 新内容」同样是包含。所以失败方向明确选:宁可群里多一条啰嗦的,不可吞掉答案。
_RESTATEMENT_NOISE = str.maketrans(
    "",
    "",
    " \t\r\n，。！？、；：~～…!?,.;:「」『』\"'“”‘’()()【】[]",
)


def _is_restatement(pushed: str, reply: str) -> bool:
    """reply 是否只是把刚发出去的 pushed 又说了一遍。

    归一化(去空白/标点/大小写)后完全相同,或一方包含另一方且多出来的部分几乎为零,
    才算复述。pushed 为空(不知道原文)一律返回 False。
    """
    a = pushed.translate(_RESTATEMENT_NOISE).casefold()
    b = reply.translate(_RESTATEMENT_NOISE).casefold()
    if not a or not b:
        return False
    if a == b:
        return True
    longer, shorter = (b, a) if len(b) >= len(a) else (a, b)
    if shorter not in longer:
        return False
    return len(longer) - len(shorter) <= max(4, len(longer) // 10)


async def _chat_and_adopt_rotation(**chat_kwargs) -> ChatResult:
    """调 chat();上游本轮换过 session(自动压缩触发)就立刻采纳新 id。

    采纳必须发生在每一次 chat 之后而不只是最后一次:重试那轮同样可能碰上轮换,
    漏掉就又把会话钉回已关闭的父会话。
    """
    result = await hermes_client.chat(**chat_kwargs)
    if result.effective_session_key:
        session_manager.adopt_session_key(chat_kwargs["session_key"], result.effective_session_key)
    return result


async def _chat_with_persistence_retry(label: str, /, **chat_kwargs) -> ChatResult:
    """调 chat();命中瞬时(locked)持久化失败时用**同一个** session_key 重试一次。

    label 走 positional-only,余下 kwargs 原样转给 hermes_client.chat,
    调用点因此和直接调 chat() 读起来一样。

    不主动换 session_key:上游三类 cause(locked / disk / unknown)都指向同一个 state.db,
    换 key 只是新开一个会话写同一个库,治不了锁也治不了满盘,代价是整个群的
    Hermes 侧上下文当场清零。disk / unknown 直接不重试 —— 重发也写不进去,而持久化
    失败是在 turn 收尾阶段判定的(工具此时已经跑过),白重试一次等于让副作用再来一遍。

    **例外**:上游自己轮换了会话(同一轮里既压缩又撞锁)时,重试要跟上采纳后的新 key。
    那不是"我们换 key",而是上游告知"活的会话已经是这个了";继续用旧 key 是往一个
    已关闭的父会话里写,必然失败。
    """
    result = await _chat_and_adopt_rotation(**chat_kwargs)
    if not result.is_persistence_error:
        return result

    if result.persistence_cause != "locked":
        # disk / unknown 要人介入(清盘、修 state.db 权限、hermes doctor),
        # 拉到 error 级别让运维扫日志能看见;回复本身走 transport_error 兜底屏蔽。
        logger.error(
            f"[HERMES {label}] 上游 Session 持久化失败且不可自动恢复 "
            f"(cause={result.persistence_cause}),不重试;请检查 Hermes 侧磁盘 / state.db"
        )
        return result

    if result.effective_session_key:
        # 采纳只更新了 SessionManager 的映射,本轮的 kwargs 还攥着旧 key;
        # 不同步过来,重试就把已关闭的父会话又钉回去了。
        chat_kwargs["session_key"] = result.effective_session_key

    logger.warning(f"[HERMES {label}] 上游 state.db 写锁冲突,{_PERSISTENCE_RETRY_DELAY_S}s 后重试一次")
    await asyncio.sleep(_PERSISTENCE_RETRY_DELAY_S)
    retried = await _chat_and_adopt_rotation(**chat_kwargs)
    if retried.is_persistence_error:
        logger.error(f"[HERMES {label}] 重试后仍持久化失败 (cause={retried.persistence_cause}),放弃本轮")
    return retried


# 重置丢掉的是 Hermes 侧的 transcript,不是全部上下文:群聊每轮仍会注入插件自己的
# `<recent_messages>` 窗口(私聊不注入),近处的对话照样看得见,接不上的是窗口之外、
# 更早的那部分。措辞因此只说"再往前的",写成"什么都不记得了"在群聊里就是假的。
_SESSION_RESET_NOTICE = "(上下文满了,我重开了一段对话 —— 再往前的就接不上了)"


async def _chat_with_overflow_recovery(label: str, /, *, reply_bot: Bot, reply_target, **chat_kwargs) -> ChatResult:
    """调 chat();撞上「上下文溢出 + 上游自动压缩已关」时重置会话并重发一次。

    上游在 compression.enabled: false 下禁掉了包括会话轮换在内的全部自动恢复,只回一段
    提示文本。而窗口只涨不缩:不管它的话下一轮更大,这条会话从此每轮都失败。

    换 session key 是对症的唯一手段 —— 那段历史窗口本身就是病因。这与持久化失败**正好
    相反**(见 _chat_with_persistence_retry:那一类刻意不换 key,因为换了既治不了写锁也
    治不了满盘,却白白清空整群上下文)。两处的判断不要互相抄。

    只重发一次:新会话仍然溢出说明病因不在历史,而在单轮本身(system prompt + 历史窗口
    就已经超了),再转一圈只会连着烧 generation 且每次都失败。

    reply_bot / reply_target 刻意不叫 bot / target:chat_kwargs 里的 `target` 是出向接入点
    (HermesTarget),与发消息用的会话目标同名不同物。
    """
    result = await _chat_with_persistence_retry(label, **chat_kwargs)
    if not result.is_context_overflow:
        return result

    adapter_name = chat_kwargs["adapter_name"]
    is_private = chat_kwargs["is_private"]
    user_id = chat_kwargs["user_id"]
    group_id = chat_kwargs["group_id"]
    logger.error(
        f"[HERMES {label}] 上游上下文溢出且自动压缩已关,重置会话后重发一次 "
        f"(group={group_id} stale_key={chat_kwargs.get('session_key')})"
    )

    # 先说再答:重发可能很慢,而且这条提示即使重发失败也依然属实(会话确实已经重置)。
    await send_text_with_media(
        bot=reply_bot,
        target=reply_target,
        text=_SESSION_RESET_NOTICE,
        media_urls=[],
        at_user_id=None,
        adapter_name=adapter_name,
    )

    session_manager.clear_session(adapter_name, is_private, user_id, group_id)
    chat_kwargs["session_key"] = session_manager.get_session_key(adapter_name, is_private, user_id, group_id)

    retried = await _chat_with_persistence_retry(label, **chat_kwargs)
    if retried.is_context_overflow:
        logger.error(
            f"[HERMES {label}] 重置后仍然溢出,放弃本轮(group={group_id})—— 病因不在历史而在单轮本身:"
            "system prompt 加历史窗口就已经超了,请缩小窗口或换更大上下文的模型"
        )
    return retried


async def _run_passive_turn(
    *,
    bot: Bot,
    target,
    adapter_name: str,
    user_id: str,
    group_id: str | None,
    text: str,
    image_urls: list[str],
    is_private: bool,
    now_ms: int,
):
    """跑一发 passive turn,返回 ChatResult 或 None(被 submit_decision 静默兜底等情况)。"""
    session_key = session_manager.get_session_key(
        adapter_name=adapter_name,
        is_private=is_private,
        user_id=user_id,
        group_id=group_id,
    )
    memory_key = session_manager.get_memory_key(
        adapter_name=adapter_name,
        is_private=is_private,
        user_id=user_id,
        group_id=group_id,
    )
    hermes_target = resolve_target(adapter_name, is_private, group_id)

    # 群聊 + 默认配置(active_session=false)+ perception_enabled:补回 0.1.6
    # 「@bot 时让 LLM 看到群里旁观历史」。before_ts=now_ms 排除 perception 在
    # 同一事件 priority=1 时刚写入的当前消息,避免历史里出现重复。
    # 私聊不注入(0.1.6 起 perception 在私聊就是 no-op,Hermes session 已覆盖)。
    # 历史从 0.2.x 起放进 user content 而非 system,以维持 system 字节稳定。
    recent: list[BufferedMessage] = []
    if not is_private and group_id and plugin_config.hermes_perception_enabled and _mcp.message_buffer is not None:
        recent = list(
            _mcp.message_buffer.get_recent(
                adapter=adapter_name,
                group_id=group_id,
                limit=plugin_config.hermes_perception_buffer,
                before_ts=now_ms,
            )
        )

    system_prompt = build_passive_system_prompt(
        adapter=adapter_name,
        is_private=is_private,
        user_id=user_id,
        group_id=group_id,
    )
    # 图片内联成 data: URL 再出站:上游对 http(s) URL 是纯透传,交出去就把可达性
    # 赌在 provider 出网与 URL 时效上,且部分 provider 形态会静默丢图。
    inlined = await inline_image_urls(image_urls)
    user_content = build_passive_user_content(
        recent_messages=recent,
        current_text=text or " ",
        current_image_urls=inlined,
    )

    result = await _chat_with_overflow_recovery(
        "passive",
        reply_bot=bot,
        reply_target=target,
        text="",
        image_urls=[],
        session_key=session_key,
        memory_key=memory_key,
        user_id=user_id,
        group_id=group_id,
        adapter_name=adapter_name,
        is_private=is_private,
        mode="passive",
        expect_structured=False,
        system_prompt=system_prompt,
        user_content_override=user_content,
        target=hermes_target,
    )

    # 上游 transport_error 同款保护(见 _run_reactive_turn 同名分支注释)。
    # passive 路径下私聊总是显式对话,群聊已通过触发判断进得来,两边都该有可见反馈;
    # 配空 fallback_text 时静默,保留逃生口。
    if result.is_transport_error:
        fallback_text = plugin_config.hermes_transport_error_fallback_text
        logger.warning(
            f"[HERMES passive] transport error fallback "
            f"(group={group_id}, is_private={is_private}, "
            f"fallback={'silent' if not fallback_text else 'friendly_text'}); "
            f"upstream raw_text suppressed (len={len(result.raw_text or '')})"
        )
        if fallback_text:
            await send_text_with_media(
                bot=bot,
                target=target,
                text=fallback_text,
                media_urls=[],
                at_user_id=None if is_private else user_id,
                adapter_name=adapter_name,
            )
        return result

    # 防御:同一 Hermes session 之前跑过 reactive 时学到 submit_decision 契约,
    # 切回 passive 后仍可能吐 JSON。检测并抠 reply_text;不命中则用原 raw_text。
    reply_text = result.raw_text
    extracted = maybe_extract_decision_reply_text(reply_text)
    if extracted is not None:
        if extracted == "":
            logger.info(f"[HERMES passive] LLM 返回 should_reply=false 结构,静默(group={group_id})")
            return result
        logger.warning(f"[HERMES passive] 检测到 submit_decision 形 JSON 残留,抠 reply_text 后发送(group={group_id})")
        reply_text = extracted

    cleaned_text, extracted_media_urls = extract_response_media(reply_text)
    media_urls = list(result.media_urls) + [u for u in extracted_media_urls if u not in result.media_urls]

    if not cleaned_text and not media_urls:
        return result
    await send_text_with_media(
        bot=bot,
        target=target,
        text=cleaned_text,
        media_urls=media_urls,
        at_user_id=None if is_private else user_id,
        adapter_name=adapter_name,
    )
    return result


async def _run_reactive_turn(
    *,
    bot: Bot,
    target,
    adapter_name: str,
    user_id: str,
    group_id: str,
    text: str,
    image_urls: list[str],
    is_explicit_trigger: bool,
    addressed_to_bot: bool = False,
    now_ms: int,
    nickname: str | None = None,
):
    """跑一发 reactive turn,返回 hermes_client.chat() 的 ChatResult,或 None 表示提前 return。

    外壳 _handle_reactive_path 负责 inflight + 图片门控,这里只管:
    拉 recent → 组 prompt → 调 chat → 解析 decision → 发出向 → 回写 buffer。
    """
    assert _mcp.message_buffer is not None and _mcp.active_sessions is not None

    # 用 get_if_active 而非 get():get() 是 debug-only 裸访问,可能返回已过期 session;
    # get_if_active 与 is_active(handle_message 入口处用过)同口径。
    session = _mcp.active_sessions.get_if_active(adapter_name, group_id, now_ms)
    if session is None:
        # turn 生命期内的 TTL 过期已由 begin_turn/end_turn 租约挡住。剩下两条来路:
        # 上一发 turn 返回了 should_exit_active(唯一会 end() 窗口的地方),或者窗口
        # 恰好在「turn 结束 → refire 接力」的交接瞬间过期且被 cron sweep 扫掉。
        # 两种都意味着有人的触发被吞了 —— 必须留痕,不能静默 return。
        logger.warning(
            f"[HERMES reactive] drop: no active window at turn start "
            f"(group={group_id} user={user_id} explicit={is_explicit_trigger} now_ms={now_ms})"
        )
        return None

    # B.3: 快照本 turn 入口时的 last_bot_reply_at, 供 chat() 返回后判定 agent loop
    # 期间是否有外部(MCP push_message)推过 bot 自己的回复。 若发生, 即使 LLM 返
    # should_reply=True 也必须抑制本路 send, 否则同 turn 内双答。
    last_bot_reply_at_at_entry = session.last_bot_reply_at

    recent_full = _mcp.message_buffer.get_recent(
        adapter=adapter_name,
        group_id=group_id,
        limit=plugin_config.hermes_perception_buffer,
    )
    # 续发轮裁剪:归属判定主要依赖窗口尾部与 bot 自己的上一条发言,
    # 全量窗口只在 explicit 触发轮保留(裁剪函数内含回退开关语义)。
    recent = (
        recent_full
        if is_explicit_trigger
        else select_followup_window(recent_full, plugin_config.hermes_reactive_followup_window)
    )

    system_prompt = build_reactive_system_prompt()
    # 平台账号名 + self_id 一起进 prompt:模型判归属只能靠这两样把消息里回填的
    # 裸 `@<id>` 和 [bot] 历史行绑回自己。取值走进程内缓存,首轮一次 RPC。
    self_nickname = await get_bot_nickname(bot)
    # 见 _run_passive_turn 同名调用注释:出站一律内联,不发平台 URL。
    inlined = await inline_image_urls(image_urls)
    user_content = build_reactive_user_content(
        adapter=adapter_name,
        group_id=group_id,
        self_id=str(bot.self_id),
        self_nickname=self_nickname,
        addressed_to_bot=addressed_to_bot,
        triggered_by=session.triggered_by,
        triggered_by_nickname=None,
        topic_hint=session.topic_hint,
        recent_messages=recent,
        current_user_id=user_id,
        current_nickname=nickname or user_id,
        current_text=text or "[图片]",
        current_image_urls=inlined,
    )

    # 出站 prompt 体量:模型在 explicit @ 下静默时,第一件要排除的就是「上下文被
    # 某条历史撑爆」。只记尺寸不记内容(历史可能几十万字符,也含用户隐私)。
    # oversized_history 记的是 sanitize **之前** 的原始长度 —— 渲染端已经会截断,
    # 但一条超长旧行说明 DB 里躺着脏数据,值得单独告警。
    prompt_chars = len(user_content) if isinstance(user_content, str) else sum(len(str(p)) for p in user_content)
    oversized = [(m.id, len(m.content)) for m in recent_full if len(m.content) > 2000]
    logger.debug(
        f"[HERMES reactive] prompt built group={group_id} user_content_chars={prompt_chars} "
        f"window={len(recent)}/{len(recent_full)} explicit={is_explicit_trigger} "
        f"imgs={len(inlined)}/{len(image_urls)}"
    )
    if oversized:
        logger.warning(
            f"[HERMES reactive] <recent_messages> 含超长历史行(已按渲染上限截断,但 DB 里是脏数据): "
            f"group={group_id} rows={oversized}"
        )

    session_key = session_manager.get_session_key(
        adapter_name=adapter_name,
        is_private=False,
        user_id=user_id,
        group_id=group_id,
    )
    memory_key = session_manager.get_memory_key(
        adapter_name=adapter_name,
        is_private=False,
        user_id=user_id,
        group_id=group_id,
    )
    hermes_target = resolve_target(adapter_name, False, group_id)
    # 注:user_content_override 已携带 user message 的全部内容(text + 多模态);
    # text/image_urls 在 chat() 中会被忽略(见 hermes_client.chat 文档),此处显式传 ""
    # /[] 让契约清晰,避免被读者误以为 image_urls 也参与了构造。
    result = await _chat_with_overflow_recovery(
        "reactive",
        reply_bot=bot,
        reply_target=target,
        text="",
        image_urls=[],
        session_key=session_key,
        memory_key=memory_key,
        user_id=user_id,
        group_id=group_id,
        adapter_name=adapter_name,
        is_private=False,
        mode="reactive",
        expect_structured=True,
        structured_tool_name="submit_decision",
        system_prompt=system_prompt,
        user_content_override=user_content,
        target=hermes_target,
    )

    if result.parse_failed or result.structured is None:
        # 上游 transport_error(5xx / 网络断 / 流被掐):raw_text 是服务端错误信息原文
        # (如 "Model generated invalid tool call: ..."),原文转发会把内部错误丢到群里
        # 既泄密又难看。换成 config 里的友好兜底文本;空串则静默。
        # parse_failed 但非 transport(LLM 真的回了点啥但结构错):原样转发 raw_text,
        # 仍可能是用户想要的回答。
        if result.is_transport_error and is_explicit_trigger:
            fallback_text = plugin_config.hermes_transport_error_fallback_text
            logger.warning(
                f"[HERMES reactive] transport error fallback "
                f"(group={group_id}, fallback={'silent' if not fallback_text else 'friendly_text'}); "
                f"upstream raw_text suppressed (len={len(result.raw_text or '')})"
            )
            if fallback_text:
                await send_text_with_media(
                    bot=bot,
                    target=target,
                    text=fallback_text,
                    media_urls=[],
                    at_user_id=user_id,
                    adapter_name=adapter_name,
                )
            return result

        # raw_text 预览:用来分类失败模式(裸文本 / fenced ```json / 多 JSON 块 /
        # nested object 等)。换行折叠成 \n 字面量,300 字够覆盖单 turn submit_decision。
        raw = result.raw_text or ""
        # 纯散文兜底:模型完全没进 submit_decision 信封(连 "should_reply" 键都没出现),
        # 说明它抛开协议直接自然作答 —— 这段散文本身就是它想说的话,非显式 turn 也照发。
        # 关键区分:信封在场但破了(截断 / 语法脏)可能正带着「这条不归我」的
        # should_reply=false 决定,而那信号只以 JSON 形式存在,拿一段可能不该说的话去撞群
        # 比丢一条更糟,所以那种破法非显式 turn 仍静默。transport / persistence 错误的 raw
        # 是服务端报文,绝不当散文转发,故排除。
        is_pure_prose = bool(raw) and not result.is_transport_error and '"should_reply"' not in raw
        forward_raw = bool(raw) and (is_explicit_trigger or is_pure_prose)
        raw_preview = preview_raw(raw)
        logger.warning(
            f"[HERMES reactive] structured parse failed (group={group_id}, "
            f"transport_error={result.is_transport_error}); fallback="
            f"{('raw_text' if is_explicit_trigger else 'prose') if forward_raw else 'silent'}; "
            f"raw_len={len(raw)} raw_preview={raw_preview!r}"
        )
        # 显式触发降级发 raw_text;非显式触发仅在纯散文时发,其余静默
        if forward_raw:
            fallback_text = raw
            extracted = maybe_extract_decision_reply_text(fallback_text)
            if extracted is not None:
                fallback_text = extracted
            cleaned_text, extracted_media_urls = extract_response_media(fallback_text)
            media_urls = list(result.media_urls) + [u for u in extracted_media_urls if u not in result.media_urls]
            # 群里明确说话给某人 → at;主动插话(散文兜底)→ 不 at,与成功回复路径同口径
            await send_text_with_media(
                bot=bot,
                target=target,
                text=cleaned_text,
                media_urls=media_urls,
                at_user_id=user_id if is_explicit_trigger else None,
                adapter_name=adapter_name,
            )
        return result

    logger.info(
        f"[HERMES reactive] decision adapter={adapter_name} group={group_id} user={user_id} "
        f"explicit={is_explicit_trigger} should_reply={result.structured.get('should_reply')} "
        f"should_exit_active={result.structured.get('should_exit_active')} "
        f"reply_text_len={len(str(result.structured.get('reply_text') or ''))} "
        f"topic_hint={result.structured.get('topic_hint')!r} "
        f"salvaged={result.structured.get('_salvaged', False)}"
    )
    # 解析成功时也留一份 raw 预览:decision 摘要看不出模型到底吐了什么形状
    # (是完整 submit_decision,还是只有 {"should_reply": false} 这种退化输出),
    # 而「该回却静默」的排查恰恰全靠这个区分。DEBUG 级,正常运行不刷日志。
    raw_preview = preview_raw(result.raw_text or "")
    logger.debug(f"[HERMES reactive] decision raw (len={len(result.raw_text or '')}): {raw_preview!r}")

    decision = result.structured
    if decision.get("topic_hint"):
        _mcp.active_sessions.update_topic(adapter_name, group_id, str(decision["topic_hint"]))
    if decision.get("should_exit_active") and not _mcp.active_sessions.end_if_current(session):
        # 收窗只对本 turn 谈的那个窗口生效:期间若有别人 @bot 建了新窗口,那条 @ 正排在
        # pending 里等 refire,把它的窗口一起弹掉就等于把它静默吞掉。
        logger.info(
            f"[HERMES reactive] should_exit_active not applied: window was re-triggered "
            f"during this turn (group={group_id})"
        )

    # 同一事实两处要用:静默分支判「是不是 push 已答」、send 分支做同 turn 去重。
    # chat() 返回后到这里没有 await,快照一次即可。
    mid_turn_push = session.last_bot_reply_at > last_bot_reply_at_at_entry

    if not decision.get("should_reply"):
        # 显式触发 + LLM 选择沉默是「看起来该回但没回」最常见的来源,
        # 提到 info 让群主能扫日志直接看见「不是插件 bug,是 LLM 自己判定的」。
        # 被点名(addressed_to_you: true 已进 prompt)却静默要更响一档:那是模型否掉了
        # 插件给的既定事实,属于要排查的形状。例外是本 turn 已经由 MCP push 答过 ——
        # 那种静默正是防重复该有的样子。
        emit = logger.warning if addressed_to_bot and not mid_turn_push else logger.info
        emit(
            f"[HERMES reactive] silent: LLM decided should_reply=false "
            f"(group={group_id} user={user_id} explicit={is_explicit_trigger} "
            f"addressed={addressed_to_bot} answered_mid_turn={mid_turn_push} "
            f"topic_hint={result.structured.get('topic_hint')!r})"
        )
        return result

    reply_text = str(decision.get("reply_text") or "").strip()
    cleaned_reply_text, media_urls = extract_response_media(reply_text)
    if not cleaned_reply_text and not media_urls:
        return result

    # B.3: 同 turn 内防重复闸门 — 若 chat() agent loop 期间 last_bot_reply_at
    # 已被推进(即 push_message 在中途答过一次), 抑制本路 submit_decision 的 send。
    # 与入口 cooldown 不同, 这里对显式触发也生效:同 turn 双答属纯重复,与触发性质无关。
    # 注: mid_turn_push 直接读 `session.last_bot_reply_at` 而非重新查 active_sessions ──
    # mark_bot_replied 是对同一 dataclass 实例原地写, session 变量持有的就是那个实例。
    # 不查 active_sessions 也回避了 TTL 边界判定与 ended-and-retriggered 罕见竞态。
    if mid_turn_push and not _is_restatement(session.last_bot_reply_text, cleaned_reply_text):
        # 中途 push 过,但这条带的是新内容(典型:push 一句「在查」、decision 给真答案)。
        # 两条都发 —— 抑制它等于把答案吞掉,而 agent 会以为已经发出去了。
        logger.info(
            f"[HERMES reactive] mid-turn push detected but submit_decision adds new content; "
            f"sending both (group={group_id} push_len={len(session.last_bot_reply_text)} "
            f"reply_text_len={len(cleaned_reply_text)})"
        )
    elif mid_turn_push:
        # 复述。中途那发**没投出任何媒体**、而本发带着能投的图 → 不是纯重复,是
        # 「文本已答、图还没出去」。典型来路:agent 用 push_message 发文本,图给的是
        # Hermes 主机本地路径(投不出去),随后 submit_decision 里带着网关内联好的
        # data URL。整条抑制会把那张图彻底丢掉,所以只补媒体、不重复文本。
        if media_urls and session.last_bot_reply_media == 0:
            media_sent = await send_text_with_media(
                bot=bot,
                target=target,
                text="",
                media_urls=media_urls,
                at_user_id=None,
                adapter_name=adapter_name,
            )
            logger.info(
                f"[HERMES reactive] mid-turn push had no media; delivered submit_decision media only "
                f"(group={group_id} media={len(media_urls)} ok={media_sent})"
            )
            if media_sent:
                _mcp.active_sessions.mark_bot_replied(
                    adapter_name, group_id, now_ms=_now_ms(), media_count=len(media_urls), text=""
                )
            return result
        logger.info(
            f"[HERMES reactive] suppress submit_decision reply: it restates the mid-turn push "
            f"(group={group_id} user={user_id} explicit={is_explicit_trigger} "
            f"reply_text_len={len(reply_text)} mid_turn_media={session.last_bot_reply_media})"
        )
        return result

    # 群里明确说话给某人 → at;主动插话 → 不 at
    at_user = user_id if is_explicit_trigger else None
    sent = await send_text_with_media(
        bot=bot,
        target=target,
        text=cleaned_reply_text,
        media_urls=media_urls,
        at_user_id=at_user,
        adapter_name=adapter_name,
    )
    logger.debug(
        f"[HERMES reactive] sent adapter={adapter_name} group={group_id} "
        f"ok={sent} text_len={len(cleaned_reply_text)} media={len(media_urls)} at_user={at_user}"
    )

    # 用 send 完成时的 wall clock,不复用入参 now_ms。
    # 入参 now_ms 是 _handle_reactive_path 进函数那一刻抓的, chat() + send 可能耗时
    # 任意长(上游重试 / 上下文压缩 / 工具调用累加),复用入参会让 last_bot_reply_at
    # 远早于真实 send 时间, 让下游 cooldown 闸门(_in_post_reply_cooldown)算出来的
    # elapsed 失真,把窗内的 refire 误判成窗外放过去。
    # 调一次 _now_ms() 拿到 reply_now_ms,三个写操作复用这一个快照,既校正 stale
    # 入参,又避免多次读时钟在三个字段间引入毫秒级偏差。
    if sent and _mcp.message_buffer is not None:
        reply_now_ms = _now_ms()
        # 回写清洗后的文本 + [图片] 占位,绝不写 reply_text 原文:媒体在回复里是内联
        # data:image;base64 形态,单张图就是几十万字符,原文入库后会被逐 turn 渲染进
        # <recent_messages>,几轮把上下文顶满,模型开始引用错历史。入站侧本来就只存
        # [图片] 占位(见 handle_perception),出站对齐同一约定。
        buffered_content = cleaned_reply_text
        if media_urls:
            buffered_content = f"{buffered_content} [图片]".strip()
        _mcp.message_buffer.append(
            BufferedMessage(
                ts=reply_now_ms,
                adapter=adapter_name,
                group_id=group_id,
                user_id=str(bot.self_id),
                nickname=self_nickname,
                content=buffered_content,
                image_urls=[],
                is_bot=True,
            )
        )
        # 注:若 should_exit_active=True,session 已在上方 end(),touch / mark_bot_replied
        # 都是安全 no-op(两者文档统一:session 缺失则 no-op)。
        _mcp.active_sessions.touch(adapter_name, group_id, now_ms=reply_now_ms)
        # B.2: 记下「bot 刚回过」时间戳,供 _handle_reactive_path 入口 + _refire 入口的
        # cooldown 闸门判定。
        marked = _mcp.active_sessions.mark_bot_replied(
            adapter_name,
            group_id,
            now_ms=reply_now_ms,
            media_count=len(media_urls),
            text=cleaned_reply_text,
        )
        if not marked and not decision.get("should_exit_active"):
            # 排除 should_exit_active:那是本 turn 自己刚 end() 掉窗口,mark 落空是预期的。
            # 剩下的只能是别人的 turn 在本 turn 期间 end() 掉了这个 key(跨用户场景),
            # 本群 cooldown 闸门这一轮失去依据 —— 日志里 last_bot_reply_at 会一直是 0,
            # 没有这条就查不出为什么。
            logger.warning(
                f"[HERMES reactive] mark_bot_replied landed nowhere: active window gone "
                f"(group={group_id}); post-reply cooldown will not engage for follow-ups"
            )

    return result


async def _handle_passive_path(
    *,
    bot: Bot,
    target,
    adapter_name: str,
    user_id: str,
    group_id: str | None,
    text: str,
    image_urls: list[str],
    is_private: bool,
    now_ms: int,
    nickname: str | None = None,
    event_msg_id: str | int | None = None,
):
    """Passive 外壳:inflight 占位 → _run_passive_turn → 合并重燃。

    与 reactive 同形,key 含 private/group 前缀区分。
    """
    assert _mcp.inflight is not None

    scope_id = user_id if is_private else (group_id or "")
    scope_prefix = "private" if is_private else "group"
    key = (adapter_name, f"{scope_prefix}:{scope_id}")

    current_buffered = BufferedMessage(
        ts=now_ms,
        adapter=adapter_name,
        group_id=group_id,
        user_id=user_id,
        nickname=nickname or user_id,
        content=text,
        image_urls=list(image_urls),
        reply_to_ts=None,
        is_bot=False,
    )

    if (
        _mcp.inflight.try_enter(
            key,
            current_buffered,
            is_explicit_trigger=True,  # passive 路径只在 (private OR active_session=off) 触发, 二者都需要 user 显式说话
            original_msg_id=event_msg_id,
            now_ms=now_ms,
        )
        != "entered"
    ):
        return

    should_refire = False
    try:
        result = await _run_passive_turn(
            bot=bot,
            target=target,
            adapter_name=adapter_name,
            user_id=user_id,
            group_id=group_id,
            text=text,
            image_urls=image_urls,
            is_private=is_private,
            now_ms=now_ms,
        )
        should_refire = not (result is not None and result.is_transport_error)
    except Exception:
        logger.exception(f"[HERMES] passive turn raised; dropping pending for {key}")
        should_refire = False
        raise
    finally:
        if not should_refire:
            _mcp.inflight.exit(key)
        else:
            pending_entry = _mcp.inflight.take_pending(key)
            if pending_entry is None or pending_entry.msg.ts <= current_buffered.ts:
                _mcp.inflight.exit(key)
            else:
                asyncio.create_task(
                    _refire(
                        key=key,
                        trigger_msg=pending_entry.msg,
                        is_explicit_trigger=pending_entry.is_explicit_trigger,
                        addressed_to_bot=pending_entry.addressed_to_bot,
                        original_msg_id=pending_entry.original_msg_id,
                        depth=1,
                        mode="passive",
                        bot=bot,
                        target=target,
                        adapter_name=adapter_name,
                        group_id=group_id,
                    )
                )


def _in_post_reply_cooldown(adapter_name: str, group_id: str, now_ms: int) -> bool:
    """B: 判断 (adapter, group_id) 是否处于 bot 上次回复后的冷却窗内。

    输入路径 (`_handle_reactive_path`) 和重燃路径 (`_refire`) 共用,确保:
      - 通过 reactive submit_decision 发出的回复 (_run_reactive_turn 末段写 mark_bot_replied)
      - 通过 MCP push_message 发出的回复 (push_message_impl 写 mark_bot_replied)
    两条路径都把后续非显式触发的旁观消息压在窗内,避免「同主题二次答复」。

    冷却仅对**非显式触发**生效——显式 @bot 必须立刻进 chat;调用方自行判断
    `is_explicit_trigger` 后再询问本 helper。

    冷却窗禁用 (`hermes_reactive_post_reply_cooldown_sec == 0`) 或 session 不存在
    /已过期、未记录过 last_bot_reply_at → 返回 False。
    """
    assert _mcp.active_sessions is not None

    cooldown_sec = plugin_config.hermes_reactive_post_reply_cooldown_sec
    if cooldown_sec <= 0:
        return False
    sess = _mcp.active_sessions.get_if_active(adapter_name, group_id, now_ms)
    if sess is None or not sess.last_bot_reply_at:
        return False
    elapsed_ms = now_ms - sess.last_bot_reply_at
    return 0 <= elapsed_ms < cooldown_sec * 1000


async def _handle_reactive_path(
    *,
    bot: Bot,
    target,
    adapter_name: str,
    user_id: str,
    group_id: str,
    text: str,
    image_urls: list[str],
    is_explicit_trigger: bool,
    addressed_to_bot: bool = False,
    now_ms: int,
    nickname: str | None = None,
    event_msg_id: str | int | None = None,
):
    """Reactive 外壳:inflight 占位 → 调 _run_reactive_turn → finally 合并重燃。

    coalesce 语义:in-flight 期间到来的新触发不并发跑,只覆盖 pending 单元;
    本发完成后 take_pending,如有则用 create_task 起一个 _refire 接力,
    本 task 立即 return,不阻塞 NoneBot 事件循环。
    """
    assert _mcp.inflight is not None and _mcp.active_sessions is not None

    # 图片门控:active window + 非显式触发 + 纯图无文本 → 跳过 chat()
    # 理由:LLM 自己的 should_reply 决策对图片要先看完才能定,而看图本身慢。
    # 这种「旁观纯图」最大概率是 should_reply=false,跳过它就是省一次多模态调用。
    # 消息已被 priority=1 perception 写入 MessageBuffer,等下次文本触发能看到。
    in_active = _mcp.active_sessions.is_active(adapter_name, group_id, now_ms)
    if in_active and not is_explicit_trigger and image_urls and not text.strip():
        logger.debug(
            f"[HERMES reactive] skip image-only passive in-window msg "
            f"(group={group_id} user={user_id}); buffered for next text trigger"
        )
        return

    # B: post-reply cooldown — bot 刚在本群发出 reactive 回复 N 秒内,非显式触发的
    # 新消息直接静默。压「我刚说完别人接话→我又凑一句」模式 2。
    # 显式 @bot 触发不受影响(is_explicit_trigger=True 直接旁路)。
    # 写在 inflight try_enter 之前,避免占用 slot 又立刻退出造成 pending 抖动。
    if in_active and not is_explicit_trigger:
        # debug 日志保留入口处,便于运维排查;helper 自己不打日志(refire 路径也会用)
        sess = _mcp.active_sessions.get_if_active(adapter_name, group_id, now_ms)
        cooldown_sec = plugin_config.hermes_reactive_post_reply_cooldown_sec
        logger.debug(
            f"[HERMES reactive] cooldown_check group={group_id} user={user_id} "
            f"sess_exists={sess is not None} "
            f"last_bot_reply_at={sess.last_bot_reply_at if sess else 'n/a'} "
            f"now_ms={now_ms} window_ms={cooldown_sec * 1000}"
        )
        if _in_post_reply_cooldown(adapter_name, group_id, now_ms):
            elapsed_ms = now_ms - (sess.last_bot_reply_at if sess else 0)
            logger.debug(
                f"[HERMES reactive] skip: post-reply cooldown "
                f"(group={group_id} elapsed_ms={elapsed_ms} window_ms={cooldown_sec * 1000})"
            )
            return

    key = (adapter_name, f"group:{group_id}")
    current_buffered = BufferedMessage(
        ts=now_ms,
        adapter=adapter_name,
        group_id=group_id,
        user_id=user_id,
        nickname=nickname or user_id,
        content=text,
        image_urls=list(image_urls),
        reply_to_ts=None,
        is_bot=False,
    )

    inflight_result = _mcp.inflight.try_enter(
        key,
        current_buffered,
        is_explicit_trigger=is_explicit_trigger,
        original_msg_id=event_msg_id,
        now_ms=now_ms,
        addressed_to_bot=addressed_to_bot,
    )
    if inflight_result != "entered":
        # 入队是 coalesce 的正常语义,但必须可观测:这条分支早先对 bystander 完全静默,
        # 一串消息集体没反应时日志里只剩 cooldown_check,查不出去向。
        logger.debug(
            f"[HERMES reactive] queued behind in-flight turn "
            f"(group={group_id} user={user_id} explicit={is_explicit_trigger} result={inflight_result})"
        )
        # pending_set + explicit:在用户的原消息上贴 busy emoji,告知 bot 在排队。
        # 之前只有 _refire 深度上限才贴 busy,首次撞 inflight 的 explicit @
        # 拿不到任何提示 —— ack 闪一下被 _ack_scope finally 撤掉就没了。
        # pending_kept(已有 explicit pending,新到 bystander 被挡)不贴 —— 那个
        # 已被保护的 explicit 进 pending 时已贴过 busy,bystander 自己不期待反馈。
        if inflight_result == "pending_set" and is_explicit_trigger:
            await _emit_busy_notice(bot, adapter_name, event_msg_id)
        return

    # should_refire_pending=False 仅在 _run_reactive_turn 抛异常时:那是 plugin
    # 自己的 bug,不该把 pending 回放(可能立刻再炸)。transport_error 是上游
    # 故障(Hermes 超时 / 5xx),pending 里的下一发(特别是 explicit @)仍要
    # refire,不能被静默吞掉。
    should_refire_pending = False
    # 租约:本 turn 跑多久,活跃窗就至少活多久。慢 turn(生图 / 长工具链)跨过 TTL 后,
    # 收尾的 touch / mark_bot_replied 和 pending 的 refire 全都依赖窗口还在。
    lease = _mcp.active_sessions.begin_turn(adapter_name, group_id)
    try:
        await _run_reactive_turn(
            bot=bot,
            target=target,
            adapter_name=adapter_name,
            user_id=user_id,
            nickname=nickname,
            group_id=group_id,
            text=text,
            image_urls=image_urls,
            is_explicit_trigger=is_explicit_trigger,
            addressed_to_bot=addressed_to_bot,
            now_ms=now_ms,
        )
        should_refire_pending = True
    except Exception:
        logger.exception(f"[HERMES] reactive turn raised; dropping pending for {key}")
        raise
    finally:
        # 先还租约(剩余窗口不足时在这里垫到下限),再决定要不要接力 —— 顺序反了
        # 就等于让 refire 去撞一个刚死的窗口。
        _mcp.active_sessions.end_turn(lease, now_ms=_now_ms())
        if not should_refire_pending:
            _mcp.inflight.exit(key)
        else:
            pending_entry = _mcp.inflight.take_pending(key)
            if pending_entry is None or pending_entry.msg.ts <= current_buffered.ts:
                _mcp.inflight.exit(key)
            else:
                asyncio.create_task(
                    _refire(
                        key=key,
                        trigger_msg=pending_entry.msg,
                        is_explicit_trigger=pending_entry.is_explicit_trigger,
                        original_msg_id=pending_entry.original_msg_id,
                        depth=1,
                        mode="reactive",
                        bot=bot,
                        target=target,
                        adapter_name=adapter_name,
                        group_id=group_id,
                    )
                )


async def _refire(
    *,
    key,
    trigger_msg: BufferedMessage,
    is_explicit_trigger: bool,
    original_msg_id: str | int | None,
    depth: int,
    mode: str,
    addressed_to_bot: bool = False,
    bot: Bot,
    target,
    adapter_name: str,
    group_id,
):
    """链式重燃。fire-and-forget,深度上限 MAX_REFIRE_DEPTH。"""
    from ..core.inflight import MAX_REFIRE_DEPTH

    assert _mcp.inflight is not None

    if depth > MAX_REFIRE_DEPTH:
        _mcp.inflight.exit(key)  # release slot first to avoid race with concurrent arrivals
        if is_explicit_trigger:
            logger.warning(
                f"[HERMES] refire depth cap reached for explicit @ "
                f"(key={key} depth={depth} msg_id={original_msg_id}); emitting busy notice"
            )
            await _emit_busy_notice(bot, adapter_name, original_msg_id)
        else:
            logger.warning(f"[HERMES] refire depth exceeded ({depth}); dropping pending {key}")
        return

    # now_ms 用 wall-clock 而不是 trigger_msg.ts:_run_*_turn 内部用它做
    # get_if_active 的 TTL 校验、active_sessions.touch 的滑动续期、以及 bot
    # 自己回复的 BufferedMessage.ts。如果用 trigger 时间会导致 touch 后窗口
    # 比预期早 N 秒过期、bot 回复时间戳倒退。trigger_msg.ts 只在 finally 的
    # pending.ts 比对里用,那是消息到达时序而非「当前是几点」。
    refire_now_ms = _now_ms()

    # B: refire 路径同款 post-reply cooldown 闸门。
    # 仅对非显式触发生效——explicit pending(如 @bot)须穿透 cooldown 直达 chat()。
    # 关键场景:初发 turn 自己没回(submit_decision=silent)但期间 MCP push_message
    # 把 last_bot_reply_at 写了 → 仅靠入口处的闸门挡不住,因为 pending 是上一次
    # 入口处放进来的(进 pending 时还没写 mark)。在这里再判一次,把这条路径补严。
    if (
        mode == "reactive"
        and group_id is not None
        and not is_explicit_trigger
        and _in_post_reply_cooldown(adapter_name, str(group_id), refire_now_ms)
    ):
        logger.debug(f"[HERMES reactive] refire skipped by post-reply cooldown (key={key} depth={depth})")
        _mcp.inflight.exit(key)
        return

    lease = None
    if mode == "reactive" and group_id is not None and _mcp.active_sessions is not None:
        lease = _mcp.active_sessions.begin_turn(adapter_name, str(group_id))

    should_refire = False
    try:
        if mode == "reactive":
            assert group_id is not None
            result = await _run_reactive_turn(
                bot=bot,
                target=target,
                adapter_name=adapter_name,
                user_id=trigger_msg.user_id,
                nickname=trigger_msg.nickname,
                group_id=group_id,
                text=trigger_msg.content,
                image_urls=list(trigger_msg.image_urls),
                is_explicit_trigger=is_explicit_trigger,
                addressed_to_bot=addressed_to_bot,
                now_ms=refire_now_ms,
            )
        else:
            result = await _run_passive_turn(
                bot=bot,
                target=target,
                adapter_name=adapter_name,
                user_id=trigger_msg.user_id,
                group_id=trigger_msg.group_id,
                text=trigger_msg.content,
                image_urls=list(trigger_msg.image_urls),
                is_private=trigger_msg.group_id is None,
                now_ms=refire_now_ms,
            )
        should_refire = not (result is not None and result.is_transport_error)
        if result is None and is_explicit_trigger:
            # 排队的显式触发一路走到这里却没跑成 turn = 用户的 @ 被吞了。
            # 具体原因由 _run_*_turn 内部那条 drop 日志给出,这条负责点出「被吞的是
            # 一个排过队的 explicit @」,两条合起来才够定位。
            logger.warning(
                f"[HERMES] queued explicit trigger produced no turn (key={key} depth={depth} msg_id={original_msg_id})"
            )
    except Exception:
        logger.exception(f"[HERMES] refire raised at depth {depth}; dropping pending for {key}")
        should_refire = False
    finally:
        if lease is not None:
            _mcp.active_sessions.end_turn(lease, now_ms=_now_ms())
        if not should_refire:
            _mcp.inflight.exit(key)
            # B012 豁免: try 侧异常已被上方 except 捕获记录,不存在被吞的活异常;
            # refire 是独立 create_task 的旁路接力,CancelledError 窗口仅停机时。
            return  # noqa: B012
        pending_entry = _mcp.inflight.take_pending(key)
        if pending_entry and pending_entry.msg.ts > trigger_msg.ts:
            asyncio.create_task(
                _refire(
                    key=key,
                    trigger_msg=pending_entry.msg,
                    is_explicit_trigger=pending_entry.is_explicit_trigger,
                    addressed_to_bot=pending_entry.addressed_to_bot,
                    original_msg_id=pending_entry.original_msg_id,
                    depth=depth + 1,
                    mode=mode,
                    bot=bot,
                    target=target,
                    adapter_name=adapter_name,
                    group_id=group_id,
                )
            )
        else:
            _mcp.inflight.exit(key)


async def route_synthesized_input(
    *,
    bot: Bot,
    target,
    adapter_name: str,
    user_id: str,
    group_id: str | None,
    nickname: str | None,
    text: str,
    allow_passive: bool,
    addressed_to_bot: bool,
    now_ms: int,
):
    """合成消息的统一入口,供 notice handler 复用既有 message routing。

    派发规则:
      - private (target.private=True) → 仅 allow_passive=True 才走 passive,否则跳过
      - group + active_session 开 → 触发 active session 并走 reactive
        (synth 始终算 is_explicit_trigger=True)

    `addressed_to_bot` 由合成方按事件语义给:平台确认事件指向 bot 本体(如戳 bot)
    才是 True;群级事件(如入群)虽然 explicit 触发,但不是「冲 bot 说的」,
    断言了就是对模型撒谎,还会让「被点名却静默」的 WARNING 变成噪音。
      - group + active_session 关 → 仅 allow_passive=True 才走 passive

    `allow_passive` 控制无 active session 时的兜底:
      - 戳一戳: True (任何 mode 都开口)
      - 入群: False (仅 active 开时通过 reactive 让 Hermes 自决,否则不打扰)
    """
    if target.private:
        if not allow_passive:
            return
        await _handle_passive_path(
            bot=bot,
            target=target,
            adapter_name=adapter_name,
            user_id=user_id,
            nickname=nickname,
            group_id=None,
            text=text,
            image_urls=[],
            is_private=True,
            now_ms=now_ms,
        )
        return

    # 群聊
    if not plugin_config.hermes_active_session_enabled:
        if not allow_passive:
            return
        await _handle_passive_path(
            bot=bot,
            target=target,
            adapter_name=adapter_name,
            user_id=user_id,
            nickname=nickname,
            group_id=group_id,
            text=text,
            image_urls=[],
            is_private=False,
            now_ms=now_ms,
        )
        return

    # 群 + active_session 开 → 显式触发 + reactive
    # (与 handle_message 显式触发同语义: 先 trigger,再 _handle_reactive_path)
    assert _mcp.active_sessions is not None
    _mcp.active_sessions.trigger(adapter_name, group_id or "", user_id, now_ms=now_ms)
    logger.info(f"[HERMES notice] synthesized reactive trigger: {adapter_name}/{group_id} by {user_id}")
    await _handle_reactive_path(
        bot=bot,
        target=target,
        adapter_name=adapter_name,
        user_id=user_id,
        nickname=nickname,
        group_id=group_id,
        text=text,
        image_urls=[],
        is_explicit_trigger=True,
        addressed_to_bot=addressed_to_bot,
        now_ms=now_ms,
    )
