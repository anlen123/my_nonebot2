"""
Hermes API Server HTTP 客户端

通过 /v1/chat/completions 与 Hermes Agent 通信。
M1-mem 路径 B(P0-spike 决策):tools/tool_choice 被 Hermes 吞掉,改用 system prompt 强约束 + JSON5 容错解析。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
import json5  # type: ignore[import-untyped]
from nonebot import logger

from .routing import HermesTarget, default_target

# user_content_override 期望形态:纯文本 或 OpenAI 多模态 parts 列表
UserContent = str | list[dict[str, Any]]

_MD_IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_MEDIA_TAG_PATTERN = re.compile(r"MEDIA:(\S+)")

# Hermes apiserver 把 provider 端错误包成 502, body 形如:
#   {"error": {"message": "Error code: 400 - {'error': {'message': '...'}}"}}
# 把外层 502 当真因显示会误导, 因此抠出内层 status 与 reason。
_INNER_STATUS_RE = re.compile(r"Error code:\s*(\d+)")
_VISION_UNSUPPORTED_RE = re.compile(
    r"unknown variant\s+[`'\"]image_url|image_url.*not.*support|does not support.*image|"
    r"input_image.*not.*support|multimodal.*not.*support",
    re.IGNORECASE,
)

# 提取首个 balanced `{...}` 子串:栈式扫描,支持任意嵌套深度,
# 正确穿过字符串字面量内的花括号(`{"x": "}"}` 不会误判提前闭合)。
# 这取代了之前的单层正则——小模型(Flash 系)有时会输出 nested object
# 或在 reply_text 里嵌带 `{}` 的代码段,正则会抠错或抠不全。


def _find_first_balanced_json_object(text: str) -> str | None:
    """扫描 text,返回从首个 `{` 到与之配平的 `}` 的子串。找不到完整平衡块返回 None。

    规则(对齐 JSON5):
      - 双引号 / 单引号字符串都识别
      - 字符串内的 `\\X` 整对透传(`\\"` / `\\\\` / `\\n` 等),不参与 quote/brace 计数
      - 字符串内出现的 `{` / `}` 不计入深度
      - 字符串内允许出现真换行(JSON5 不允,但调用方有 _escape_raw_newlines 二次回退)
    """
    if not text:
        return None
    n = len(text)
    i = 0
    while i < n and text[i] != "{":
        i += 1
    if i >= n:
        return None
    start = i
    depth = 0
    in_string = False
    quote = ""
    while i < n:
        c = text[i]
        if in_string:
            if c == "\\" and i + 1 < n:
                i += 2
                continue
            if c == quote:
                in_string = False
                quote = ""
            i += 1
            continue
        if c == '"' or c == "'":
            in_string = True
            quote = c
            i += 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    return None


_DECISION_HINT = (
    "\n\n=== STRUCTURED OUTPUT ===\n"
    "Your reply MUST be a single JSON object with these keys:\n"
    "  should_reply (boolean, required)\n"
    "  reply_text (string, optional, required when should_reply=true)\n"
    "  topic_hint (string, optional)\n"
    "  should_exit_active (boolean, optional)\n"
    "Output ONLY the JSON object, no preamble, no postscript, no markdown fences.\n"
    "All string values MUST be single-line; escape line breaks inside strings as \\n (no raw newlines)."
)


def _escape_raw_newlines_in_strings(s: str) -> str:
    """把 JSON 字符串字面量内部的裸 \\n/\\r/\\t 转义掉,让 json5 能解析。

    LLM 经常在 reply_text 里嵌真换行(段落分隔),JSON5 字符串不允许;首发 json5
    抛 `Unexpected "\\n"` 后我们走这一遍状态机重试一次。状态:跟踪 " / ' 进出
    string、`\\X` 整对透传(不参与 quote 计数),quoted 区里把裸控制字符替换。
    """
    out: list[str] = []
    in_string = False
    quote = ""
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if not in_string:
            if c == '"' or c == "'":
                in_string = True
                quote = c
            out.append(c)
            i += 1
            continue
        # 在 string 内
        if c == "\\" and i + 1 < n:
            # 转义序列整对透传(含 \" / \' / \\ / \n 等),不参与 quote 计数
            out.append(s[i : i + 2])
            i += 2
            continue
        if c == quote:
            in_string = False
            quote = ""
            out.append(c)
            i += 1
            continue
        if c == "\n":
            out.append("\\n")
        elif c == "\r":
            out.append("\\r")
        elif c == "\t":
            out.append("\\t")
        else:
            out.append(c)
        i += 1
    return "".join(out)


# 中文模型常把 JSON 结构位(字段分隔、键值冒号)吐成全角标点,让 json / json5 双双拒解析。
# 只归一化**字符串外**的这些 —— reply_text 正文里的全角逗号/冒号是合法中文,动一个字节
# 都会污染发给用户的文本。用与 _escape_raw_newlines_in_strings 同款状态机圈定 string 边界。
_FULLWIDTH_JSON_PUNCT = {
    "，": ",",  # U+FF0C 全角逗号
    "：": ":",  # U+FF1A 全角冒号
}


def _normalize_fullwidth_json_punct(s: str) -> str:
    """把字符串**外**的全角结构标点(，/：)换成 ASCII;字符串内部原样保留。

    合法 JSON 里字符串外只可能出现结构 token,所以字符串外的全角 ，/： 必是错吐的
    分隔符/冒号,归一是安全的;合规输入下本函数是 no-op。
    """
    out: list[str] = []
    in_string = False
    quote = ""
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if not in_string:
            if c == '"' or c == "'":
                in_string = True
                quote = c
                out.append(c)
            else:
                out.append(_FULLWIDTH_JSON_PUNCT.get(c, c))
            i += 1
            continue
        # 在 string 内:转义对整体透传(不参与 quote 计数),内容一律不动
        if c == "\\" and i + 1 < n:
            out.append(s[i : i + 2])
            i += 2
            continue
        if c == quote:
            in_string = False
            quote = ""
        out.append(c)
        i += 1
    return "".join(out)


def _summarize_error_body(body: str) -> tuple[str, int | None]:
    """从 Hermes 错误响应体里抠出可读 reason 与内层 status。

    Hermes apiserver 习惯把 provider 端错误外包成 502, body 是 JSON, `error.message`
    形如 `"Error code: 400 - {...}"`。直接把 200 字符 body 倒进日志噪音大、
    用户看到只剩外层 502 完全不知道发生了什么, 所以这里先剥一层。

    返回 (reason_snippet, inner_status_or_None);body 不可解析时退化到 body 截断。
    """
    if not body:
        return "(empty body)", None
    raw = body.strip()
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw[:200], None
    err = parsed.get("error") if isinstance(parsed, dict) else None
    if isinstance(err, dict):
        msg = str(err.get("message") or err).strip()
    elif isinstance(err, str):
        msg = err.strip()
    else:
        msg = raw[:200]
    inner_status: int | None = None
    m = _INNER_STATUS_RE.search(msg)
    if m:
        try:
            inner_status = int(m.group(1))
        except ValueError:
            inner_status = None
    return msg[:300], inner_status


def preview_raw(text: str, *, head: int = 220, tail: int = 220) -> str:
    """给日志用的回复预览:**头尾都取**,中间省略。

    只取头部对诊断结构化解析失败没用 —— 破在末尾的情况(缺闭合括号、多余字段、
    尾随逗号)恰好都在被截掉的那一半里。先切片再转义:内联大图时 text 是 MB 级,
    反过来做会白拷一遍整串。
    """
    if not text:
        return ""

    def _esc(s: str) -> str:
        return s.replace("\n", "\\n").replace("\r", "\\r")

    if len(text) <= head + tail:
        return _esc(text)
    return f"{_esc(text[:head])}…[省略 {len(text) - head - tail} 字符]…{_esc(text[-tail:])}"


def _user_facing_error(reason: str) -> str:
    """把 _summarize_error_body 的 reason 翻译成给群里发的简短提示。

    命中已知模式(目前: 图片输入被非 vision 模型拒)就给精准提示;否则带 reason 片段
    让用户能直接看到真因, 不再只露一个误导性的 502。
    """
    if _VISION_UNSUPPORTED_RE.search(reason):
        return "⚠️ 当前主模型不支持图片识别,请改用文字提问或换用 vision 模型"
    snippet = reason.strip().splitlines()[0][:140] if reason.strip() else "未知错误"
    return f"⚠️ AI 服务异常: {snippet}"


# 输出被截断时 markdown 图片可能停在 base64 中途,没有闭合 `)`。半截 base64
# 解出来是坏图,平台要么拒收要么显示破图,还会把「上游截断了回复」伪装成「发图成功」,
# 所以只留占位、不投递(投递前的合法性最终判定在 outbound._parse_image_data_url)。
_TRUNCATED_MD_IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\(data:image/[\w.+-]+;base64,[A-Za-z0-9+/=\s]*$")
_TRUNCATED_IMAGE_PLACEHOLDER = "[图片传输不完整]"


def _media_basename(ref: str) -> str:
    """只留 basename,不泄完整主机路径。"""
    return ref.rsplit("/", 1)[-1] or ref


def extract_response_media(text: str) -> tuple[str, list[str]]:
    """从 Hermes 回复中提取 markdown 图片 / MEDIA: 标签 URL,返回 (清洗后文本, URL 列表)。

    两种标签同一套分流规则:http(s) 与 data:image/…;base64 进媒体列表;
    其余是 Hermes 主机上的本地文件路径(音频/视频/超限图片/路径校验失败,
    api_server 不做内联改写),bot 侧无法抓取,原位替换为 [生成了文件: 文件名]
    占位——静默删掉会让用户只看到「画好啦」却没有图。
    """
    media_urls: list[str] = []

    def _collect(ref: str) -> str | None:
        """进媒体列表返回 None;否则返回该原位替换成的占位文本。"""
        # data:image/…;base64 是 api_server 对本地生成图片(≤5MB)的内联形态,
        # 与 http(s) 同样要进 media 列表;解码与合法性校验在 outbound 侧做。
        if ref.startswith(("http://", "https://", "data:image/")):
            media_urls.append(ref)
            return None
        if ref.startswith("data:"):
            # 非图片 data URL(text/plain 等):既不是可投递媒体也不是文件,直接去掉,
            # 不能按路径切 basename——那会把 payload 本身当文件名贴到群里。
            return ""
        return f"[生成了文件: {_media_basename(ref)}]"

    def _md_image_repl(m: re.Match) -> str:
        return _collect(m.group(2)) or ""

    def _media_tag_repl(m: re.Match) -> str:
        return _collect(m.group(1)) or ""

    cleaned = _MD_IMAGE_PATTERN.sub(_md_image_repl, text)
    cleaned = _MEDIA_TAG_PATTERN.sub(_media_tag_repl, cleaned)
    cleaned = _TRUNCATED_MD_IMAGE_PATTERN.sub(_TRUNCATED_IMAGE_PLACEHOLDER, cleaned)

    return cleaned.strip(), media_urls


_SALVAGE_SHOULD_REPLY_RE = re.compile(r'"should_reply"\s*:\s*(true|false)', re.IGNORECASE)
_SALVAGE_REPLY_TEXT_RE = re.compile(r'"reply_text"\s*:\s*"((?:[^"\\]|\\.)*)', re.DOTALL)
_SALVAGE_TOPIC_HINT_RE = re.compile(r'"topic_hint"\s*:\s*"((?:[^"\\]|\\.)*)"')
_SALVAGE_EXIT_ACTIVE_RE = re.compile(r'"should_exit_active"\s*:\s*(true|false)', re.IGNORECASE)


def _salvage_truncated_reply_text(text: str) -> dict[str, Any] | None:
    """当 JSON 块语法破坏 (如输出被截断、未闭合双引号或花括号) 时,
    尝试通过正则从 raw_text 抠出 should_reply / reply_text 两个字段。

    救补的判据只有「信封在场」这一条 —— should_reply 与 reply_text 两个键都能捞到。
    不按响应长度或媒体标记设闸门:信封破损与回复大小无关,一条纯文本短回复里多一个
    引号同样能让平衡块扫不出来,而那时落空的代价是整条回复丢失(窗内跟随)或整段
    JSON 被当散文发进群(显式触发),都比一段可能不完整的正文更糟。

    should_reply 必须照抄原文里的布尔值:硬编码 True 会把模型「这条不归我」的
    决定翻成插话,抢答别人的对话比丢一条回复更糟。读不出布尔值时才默认 True
    (reply_text 存在说明模型本来打算说话)。
    """
    if not text or '"should_reply"' not in text:
        return None
    m = _SALVAGE_REPLY_TEXT_RE.search(text)
    if m is None:
        return None
    raw_val = m.group(1)
    escaped_val = raw_val.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    try:
        val = json.loads(f'"{escaped_val}"')
    except Exception:
        val = raw_val
    # 典型破法:reply_text 以 `![image](…)` 结尾时模型漏掉闭合 `"`,于是 reply_text
    # 正则一路吃到 `,"topic_hint"` 前的那个引号,尾部就多带一个逗号。那是信封的残渣,
    # 不是内容,发到群里只是一个突兀的逗号。
    val = val.rstrip().removesuffix(",").rstrip()

    m_flag = _SALVAGE_SHOULD_REPLY_RE.search(text)
    should_reply = True if m_flag is None else m_flag.group(1).lower() == "true"
    out: dict[str, Any] = {"should_reply": should_reply, "reply_text": val, "_salvaged": True}

    # 另外两个字段也按正则捞回来:它们在原文里通常是完好的,丢掉的话每个带图 turn
    # 都会连带丢掉话题跟踪与退场判断 —— 而带图 turn 恰恰是最常触发救补的那些。
    m_topic = _SALVAGE_TOPIC_HINT_RE.search(text)
    if m_topic:
        out["topic_hint"] = m_topic.group(1)
    m_exit = _SALVAGE_EXIT_ACTIVE_RE.search(text)
    if m_exit:
        out["should_exit_active"] = m_exit.group(1).lower() == "true"
    return out


# json5 是纯 Python 解析器,MB 级 payload 要几十秒 CPU,而解析跑在 event loop 里 ——
# 一次内联大图就能把整个 bot 冻住。超过这个尺寸只用 stdlib json(C 实现,同 payload
# 毫秒级)与正则救补,不再交给 json5 兜。
_JSON5_MAX_CHARS = 262_144


def _load_json_candidate(candidate: str) -> Any:
    """按 stdlib json → 转义裸控制字符 → json5(仅小 payload)顺序解析,全失败返回 None。

    stdlib json 优先不只是快:结构化输出在正常路径下就是合法 JSON,json5 只用来
    兜模型的语法脏(单引号 / 尾随逗号 / 裸 key)。
    """
    try:
        return json.loads(candidate)
    except Exception:
        pass
    # 中文模型常把字段分隔/键值冒号吐成全角 ，/:,让 json / json5 双双拒解析。先把
    # **字符串外**的全角结构标点归一成 ASCII(正文里的全角标点原样保留),后续阶梯都用它。
    # 合规输入下归一是 no-op,与旧行为等价。
    normalized = _normalize_fullwidth_json_punct(candidate)
    try:
        return json.loads(normalized)
    except Exception:
        pass
    # LLM 常在 reply_text 里嵌真换行(JSON 不允),转义后 stdlib json 仍能吃下
    escaped = _escape_raw_newlines_in_strings(normalized)
    try:
        return json.loads(escaped)
    except Exception:
        pass
    if len(candidate) > _JSON5_MAX_CHARS:
        logger.warning(
            f"[HERMES] 结构化回复过大({len(candidate)} 字符)且非合法 JSON,跳过 json5 回退以免阻塞 event loop,转正则救补"
        )
        return None
    try:
        return json5.loads(normalized)
    except Exception:
        pass
    try:
        return json5.loads(escaped)
    except Exception:
        return None


# 内联 data URL 摘出/回填。api_server 把 MEDIA: 图片换成 data URL 是在**生成之后**
# 做的,所以一条带图回复的 JSON 是 MB 级,而里面真正的 JSON 结构只有几百字节。
# 先把 payload 换成短 token 再解析:候选串回到 KB 级,解析器阶梯(含 json5)全程可用,
# 不必再靠尺寸阈值放弃 —— 之前跳过 json5 会让带图回复退到正则救补,连带丢掉
# topic_hint / should_exit_active(救补只认 should_reply + reply_text)。
# 阈值 256:短 data URL 本来就不影响解析,只处理真正撑爆候选串的那些。
_DATA_URL_DETACH_RE = re.compile(r"data:[\w.+-]+/[\w.+-]+;base64,[A-Za-z0-9+/=]{256,}")
_DETACH_TOKEN_RE = re.compile(r"⟦hermes-media-(\d+)⟧")


def _detach_data_urls(text: str) -> tuple[str, list[str]]:
    """把长 data URL 替换成 ⟦hermes-media-N⟧,返回 (缩短后的文本, 原始 URL 列表)。"""
    urls: list[str] = []

    def _repl(m: re.Match) -> str:
        urls.append(m.group(0))
        return f"⟦hermes-media-{len(urls) - 1}⟧"

    return _DATA_URL_DETACH_RE.sub(_repl, text), urls


def _reattach_data_urls(value: str, urls: list[str]) -> str:
    """把 ⟦hermes-media-N⟧ 换回原始 data URL。索引越界时原样留着 token。"""

    def _repl(m: re.Match) -> str:
        idx = int(m.group(1))
        return urls[idx] if 0 <= idx < len(urls) else m.group(0)

    return _DETACH_TOKEN_RE.sub(_repl, value)


def _try_parse_first_json_block(text: str) -> dict[str, Any] | None:
    """从模型回复中提取首个 {...} 块并解析。失败返回 None,调用方记 parse_failed。

    流程:摘掉内联 data URL → 提平衡块 → _load_json_candidate(json → json5)
    → 失败则 _salvage_truncated_reply_text 正则抠取 → 把 data URL 回填进字符串字段。
    """
    if not text:
        return None
    shrunk, detached = _detach_data_urls(text)
    parsed: Any = None
    candidate = _find_first_balanced_json_object(shrunk)
    if candidate is not None:
        parsed = _load_json_candidate(candidate)
    if not isinstance(parsed, dict):
        parsed = _salvage_truncated_reply_text(shrunk)
    if parsed is None:
        return None
    if detached:
        parsed = {k: (_reattach_data_urls(v, detached) if isinstance(v, str) else v) for k, v in parsed.items()}
    return parsed


def maybe_extract_decision_reply_text(text: str) -> str | None:
    """passive 路径的防御:如果 raw_text 是 submit_decision 形 JSON,抠出 reply_text。

    场景:同一 Hermes session 之前跑过 reactive 模式,LLM 上下文学到了
    submit_decision 契约;之后切回 passive(active_session 关掉)时,session
    依旧吐 JSON,导致整段 JSON 被当作回复发给用户。

    返回值:
      - 字符串:JSON 是 submit_decision 形且 should_reply=true,返回 reply_text
      - 空串 "":JSON 是 submit_decision 形但 should_reply=false(显式静默)
      - None:不是 submit_decision 形,调用方应继续用原始 raw_text
    """
    parsed = _try_parse_first_json_block(text)
    if parsed is None or "should_reply" not in parsed:
        return None
    if not parsed.get("should_reply"):
        return ""
    rt = parsed.get("reply_text")
    if isinstance(rt, str):
        return rt
    return None


_PERSISTENCE_ERROR_PATTERNS = (
    "session storage could not be written",
    "the turn was stopped because session storage",
    "session_persistence_failed",
)

# 上游把 cause 只写在散文里(结构化的 failure_reason 没有透到 /v1/chat/completions
# 的响应体),所以按各 cause 的特征词分类。三类的可恢复性完全不同:
#   locked  — 别的 Hermes 进程正在写 state.db,上游明说「消息已存下,过一会再发一次」
#   disk    — 磁盘满 / state.db 权限,重发也写不进去
#   unknown — 需要人去跑 hermes doctor
PersistenceCause = Literal["locked", "disk", "unknown"]

_PERSISTENCE_LOCKED_PATTERNS = ("session storage was busy", "another hermes process was writing")
_PERSISTENCE_DISK_PATTERNS = ("free some space", "full disk", "disk full")


# 上游会把 provider 的失败包成 200 + 报错正文当助手回复返回,而不是一个 HTTP 错误码。
# 这段报文于是走到「模型确实说了点什么」的分支,被当成散文发进群 —— 既把内部报错摊给
# 用户,也把报文里那条带时效凭据的媒体 URL 一起泄出去。与持久化中断同属一类:200 的
# 外壳,错误的内容。
_PROVIDER_ERROR_PREFIX_RE = re.compile(r"^\s*HTTP\s+\d{3}\s*:", re.IGNORECASE)
_PROVIDER_ERROR_MARKERS = (
    "error from provider",
    "upstream request failed",
    "invalid_request_error",
)


def is_provider_error_text(text: str) -> bool:
    """检测回复内容是否为上游透传的 provider 报错(200 外壳 + 错误正文)。

    前缀与特征词必须同时命中:只认前缀会把正经聊到状态码的回复误伤,只认特征词会把
    讨论报错本身的对话误伤 —— 而误伤的代价是把用户想要的回答换成一句兜底。
    """
    if not text:
        return False
    if not _PROVIDER_ERROR_PREFIX_RE.match(text):
        return False
    lower = text.lower()
    return any(marker in lower for marker in _PROVIDER_ERROR_MARKERS)


# 上游在 `compression.enabled: false` 下撞到上下文溢出时会**放弃**:agent/turn_recovery.py
# 禁掉包括会话轮换在内的全部自动恢复路径,只把一句英文提示当作 final_response 返回,而
# api_server 又把它原样塞进 assistant content 以 200 发回 —— 形态与 provider 报错同类,
# 但可恢复性完全不同:那一段历史窗口本身就是病因,换个 session key 就能继续。
#
# 锚点必须落在**开头**:上游那段是整条回复的全文,而解释性的正经回答不会以它开头。
# 只认特征词会把「群友正在聊这个报错」的回答一起误伤,而误伤的代价是清空整条会话。
_CONTEXT_OVERFLOW_ANCHOR_RE = re.compile(r"^\s*Context overflow\b", re.IGNORECASE)
_CONTEXT_OVERFLOW_MARKER = "auto-compaction is disabled"


def is_context_overflow_text(text: str) -> bool:
    """检测回复是否为上游「上下文溢出且自动压缩已关」的放弃提示。"""
    if not text:
        return False
    if not _CONTEXT_OVERFLOW_ANCHOR_RE.match(text):
        return False
    return _CONTEXT_OVERFLOW_MARKER in text.lower()


def rotated_session_key(header_value: str | None, sent_key: str) -> str | None:
    """响应头里的有效 session id;缺失或与请求发出的 key 相同时返回 None(未轮换)。"""
    value = (header_value or "").strip()
    if not value or value == sent_key:
        return None
    return value


def is_persistence_error_text(text: str) -> bool:
    """检测回复内容是否为 Hermes 上游 SQLite session 持久化失败引发的中断报错文本。"""
    if not text:
        return False
    lower = text.lower()
    return any(pattern in lower for pattern in _PERSISTENCE_ERROR_PATTERNS)


def classify_persistence_error(text: str) -> PersistenceCause:
    """把持久化中断文本分到 locked / disk / unknown。只在 is_persistence_error_text 为真时有意义。"""
    lower = (text or "").lower()
    if any(pattern in lower for pattern in _PERSISTENCE_LOCKED_PATTERNS):
        return "locked"
    if any(pattern in lower for pattern in _PERSISTENCE_DISK_PATTERNS):
        return "disk"
    return "unknown"


@dataclass
class ChatResult:
    raw_text: str
    structured: dict[str, Any] | None = None
    media_urls: list[str] = field(default_factory=list)
    parse_failed: bool = False
    """期望结构化输出但解析失败(JSON 提取不到 / json5 解析报错 / 非 dict 类型)。"""

    is_transport_error: bool = False
    """HTTP 失败(非 200 / timeout / connect error / 其他异常)。

    handler 据此决定:transport_error → 可重试或对用户报错;parse_failed →
    通常静默降级(模型回了不可解析的内容);两者**不互斥**(transport_error
    场景下 parse_failed 也设 True 以阻止 caller 误把 raw_text 当模型有效输出)。
    """

    is_persistence_error: bool = False
    """上游 Hermes session DB 持久化失败(session_persistence_failed)。

    Hermes Gateway 在持久化失败时仍返回 HTTP 200,但 content 是中断解释文本。
    置 True 让 handler 能识别并按 persistence_cause 决定重试还是静默屏蔽,
    而不是把上游报错原文丢进群里。
    """

    is_context_overflow: bool = False
    """上游上下文溢出,且它那边关掉了自动压缩(compression.enabled: false)。

    上游此时禁掉包括会话轮换在内的全部自动恢复,同样以 HTTP 200 回一段英文提示。
    窗口只涨不缩,不换 session key 的话这条会话从此每轮都失败 —— handler 据此
    重置会话再重发一次,见 _chat_with_overflow_recovery。
    """

    effective_session_key: str | None = None
    """上游本轮实际使用的 session id,**仅在与请求发出的 key 不同时**有值。

    Hermes 自动压缩上下文时会轮换会话:旧 id 被 end_reason='compression' 关闭,
    新建 continuation 子会话,新 id 走响应头 X-Hermes-Session-Id 回传。不采纳的话
    下一轮又钉回已关闭的父会话——读还能跟随 tip,写全部失败,且每次压缩再分叉一个
    兄弟会话,直到 live 子会话不止一个,上游血缘恢复判定歧义后永久写不进去。
    """

    persistence_cause: PersistenceCause | None = None
    """持久化失败的类别,仅 is_persistence_error=True 时有值。

    只有 locked(别的进程正在写库)是瞬时可恢复的 —— 上游对这一类明确要求
    「过一会再发一次」;disk / unknown 重发也写不进去,重试只是浪费一次 agent turn。
    """


# 上游 /v1/capabilities 里与长期记忆作用域相关的能力键(见 api_server 的 features 块)。
_MEMORY_CAPABILITY_KEYS = ("session_key_header",)


def missing_memory_capabilities(caps: dict[str, Any]) -> list[str]:
    """返回 caps 中缺失的长期记忆能力键。

    上游把能力放在 {"features": {...}} 下;宽容处理平铺形状,避免因为
    响应外壳变化就误报"上游不支持"。
    """
    features = caps.get("features")
    if not isinstance(features, dict):
        features = caps
    return [key for key in _MEMORY_CAPABILITY_KEYS if not features.get(key)]


class HermesClient:
    def __init__(self) -> None:
        # 无实例状态:接入点按群解析,缓存会让路由与配置漂移。
        pass

    @property
    def api_url(self) -> str:
        """默认接入点的 base URL(日志、启动期能力探测用)。"""
        return default_target().base_url

    @property
    def api_key(self) -> str:
        return default_target().api_key

    @property
    def timeout(self) -> int:
        return default_target().timeout

    def get_headers(
        self,
        session_key: str = "",
        memory_key: str | None = None,
        *,
        api_key: str | None = None,
    ) -> dict[str, str]:
        """拼装出向 header。

        X-Hermes-Session-Id  = 短期 transcript 作用域(每轮必发)
        X-Hermes-Session-Key = 长期记忆作用域(仅在调用方给了值且本轮有 api_key 时发)

        没有 api_key 却发记忆头会被上游 403 整轮打回,所以这里与 Authorization
        绑在同一个条件上。

        api_key=None 表示用默认接入点的 key;按群路由时由调用方传入该群接入点的 key。
        """
        key = self.api_key if api_key is None else api_key
        h = {"Content-Type": "application/json", "X-Hermes-Session-Id": session_key}
        if key:
            h["Authorization"] = f"Bearer {key}"
            if memory_key:
                h["X-Hermes-Session-Key"] = memory_key
        return h

    async def chat(
        self,
        *,
        text: str,
        image_urls: list[str] | None = None,
        session_key: str,
        memory_key: str | None = None,
        user_id: str,
        group_id: str | None,
        adapter_name: str,
        is_private: bool,
        mode: Literal["reactive", "passive"] = "passive",
        expect_structured: bool = False,
        structured_tool_name: str | None = None,
        system_prompt: str | None = None,
        user_content_override: UserContent | None = None,
        target: HermesTarget | None = None,
    ) -> ChatResult:
        """调用 Hermes,返回 ChatResult。

        - mode='passive': 普通文本回复(不强制结构化)
        - mode='reactive' + expect_structured=True + structured_tool_name='submit_decision':
          system prompt 追加 STRUCTURED OUTPUT 段,期望模型回复纯 JSON;解析失败 parse_failed=True
        - system_prompt: 由 prompt_builder 注入;None 走默认 Message Context 拼装。
          **注意**:外部传入 system_prompt 时,Platform/User/Group 上下文须由调用方
          自行包含,本方法不会再额外补。
        - memory_key: 长期记忆作用域(X-Hermes-Session-Key)。None = 不发该头。
          由 SessionManager.get_memory_key() 产出,与 session_key 相互独立:后者会随
          /clear 与上游压缩轮换,前者不会。
        - user_content_override: 由 prompt_builder 直接给出 user message 的 content
          (str 或 OpenAI 多模态 parts 列表;text + image_urls 参数将被忽略)
        - target: 本轮发往的接入点(url + key + timeout)。None = 默认接入点。
          按群路由时由 handler 经 routing.resolve_target() 解析后传入。
        - mode 字段当前为路由元数据,chat() 内部不分支判断;Task 15 handler 据此决定
          如何呈现结果(reactive 走 structured 流,passive 走 raw_text)。
        """
        tgt = target or default_target()
        url = f"{tgt.base_url}/v1/chat/completions"

        if user_content_override is not None:
            content: Any = user_content_override
        else:
            cur_imgs = image_urls or []
            if not cur_imgs:
                content = text
            else:
                parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
                for u in cur_imgs:
                    parts.append({"type": "image_url", "image_url": {"url": u}})
                content = parts

        if system_prompt is None:
            ctx_lines = [f"Platform: {adapter_name or 'unknown'}"]
            ctx_lines.append("Chat Type: " + ("Private" if is_private else "Group"))
            if user_id:
                ctx_lines.append(f"User ID: {user_id}")
            if not is_private and group_id:
                ctx_lines.append(f"Group ID: {group_id}")
            system_prompt = "Message Context:\n" + "\n".join(ctx_lines)

        if expect_structured and structured_tool_name == "submit_decision":
            system_prompt = system_prompt + _DECISION_HINT

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ]

        payload: dict[str, Any] = {
            "model": "hermes-agent",
            "messages": messages,
            "stream": False,
        }
        # 路径 B:不发 tools / tool_choice(Hermes 透传不可靠,P0-spike 已验)

        headers = self.get_headers(session_key, memory_key, api_key=tgt.api_key)
        try:
            async with httpx.AsyncClient(timeout=tgt.timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code != 200:
                    reason, inner_status = _summarize_error_body(resp.text)
                    inner_tag = f" inner={inner_status}" if inner_status else ""
                    logger.error(f"[HERMES] upstream HTTP {resp.status_code}{inner_tag}: {reason}")
                    return ChatResult(
                        raw_text=_user_facing_error(reason),
                        parse_failed=True,
                        is_transport_error=True,
                    )
                data = resp.json()
                rotated = rotated_session_key(resp.headers.get("X-Hermes-Session-Id"), session_key)
        except httpx.TimeoutException:
            logger.error(f"[HERMES] API 请求超时 ({tgt.timeout}s, target={tgt.label})")
            return ChatResult(
                raw_text="⚠️ AI 服务响应超时,请稍后重试",
                parse_failed=True,
                is_transport_error=True,
            )
        except httpx.ConnectError:
            logger.error(f"[HERMES] 无法连接到 {tgt.base_url} (target={tgt.label})")
            return ChatResult(
                raw_text="⚠️ 无法连接到 AI 服务",
                parse_failed=True,
                is_transport_error=True,
            )
        except Exception as exc:
            logger.error(f"[HERMES] API 请求异常: {exc}")
            return ChatResult(
                raw_text=f"⚠️ AI 服务异常: {exc}",
                parse_failed=True,
                is_transport_error=True,
            )

        def _out(result: ChatResult) -> ChatResult:
            """给本轮所有出口统一挂上有效 session id(轮换才有值)。"""
            result.effective_session_key = rotated
            return result

        choices = data.get("choices") or []
        if not choices:
            # 期望结构化但响应空:这是结构性失败而非"模型选择不回复"
            return _out(ChatResult(raw_text="", parse_failed=expect_structured))

        msg = choices[0].get("message") or {}
        raw_text = msg.get("content") or ""

        # 检查是否为上游持久化失败错误(Hermes 返回 200 但 content 为 Session DB 错误提示)
        if is_persistence_error_text(raw_text):
            cause = classify_persistence_error(raw_text)
            preview = preview_raw(raw_text, head=200, tail=0)
            logger.warning(
                f"[HERMES] 捕获到上游 Session 持久化失败中断提示 "
                f"(cause={cause} raw_len={len(raw_text)} preview={preview!r})"
            )
            return _out(
                ChatResult(
                    raw_text=raw_text,
                    parse_failed=True,
                    is_transport_error=True,
                    is_persistence_error=True,
                    persistence_cause=cause,
                )
            )

        if is_context_overflow_text(raw_text):
            preview = preview_raw(raw_text, head=200, tail=0)
            logger.warning(
                f"[HERMES] 上游上下文溢出且自动压缩已关,本轮被放弃 (raw_len={len(raw_text)} preview={preview!r})"
            )
            return _out(
                ChatResult(
                    raw_text=raw_text,
                    parse_failed=True,
                    is_transport_error=True,
                    is_context_overflow=True,
                )
            )

        if is_provider_error_text(raw_text):
            preview = preview_raw(raw_text, head=200, tail=0)
            logger.warning(
                f"[HERMES] 上游以 200 返回了 provider 报错,按传输失败处理 (raw_len={len(raw_text)} preview={preview!r})"
            )
            return _out(ChatResult(raw_text=raw_text, parse_failed=True, is_transport_error=True))

        # 路径 B:从 raw_text 提取首个 {...} JSON5 块
        if expect_structured and structured_tool_name == "submit_decision":
            structured = _try_parse_first_json_block(raw_text)
            if structured is None:
                preview = preview_raw(raw_text)
                logger.warning(
                    f"[HERMES] 路径 B 未能从回复中解析出 JSON 块 (raw_len={len(raw_text)} preview={preview!r})"
                )
                return _out(ChatResult(raw_text=raw_text, parse_failed=True))
            return _out(ChatResult(raw_text=raw_text, structured=structured))

        # 普通文本路径(passive 或未要求结构化)
        cleaned, media_urls = extract_response_media(raw_text)
        return _out(ChatResult(raw_text=cleaned, media_urls=media_urls))

    async def fetch_capabilities(self) -> dict[str, Any]:
        """取 /v1/capabilities;任何失败都返回空 dict —— 探测不能阻塞启动。"""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.api_url}/v1/capabilities", headers=self.get_headers())
                if resp.status_code != 200:
                    return {}
                data = resp.json()
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    async def health_check(self, target: HermesTarget | None = None) -> bool:
        """探活一个接入点。target=None 探默认接入点。

        探 /v1/models 而不是 /health:前者要鉴权,所以 key 配错会如实报 401 ——
        这正是"命名 profile 的 key 填错 / 忘开 multiplex"能被看见的原因。
        """
        tgt = target or default_target()
        try:
            headers = self.get_headers(api_key=tgt.api_key)
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{tgt.base_url}/v1/models", headers=headers)
                return resp.status_code == 200
        except Exception:
            return False


hermes_client = HermesClient()
