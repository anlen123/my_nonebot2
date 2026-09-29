"""Gateway 图片链路:结构化输出解析/救补、data URL 出向投递、bot 回复回写 buffer。"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from nonebot_plugin_hermes import mcp as _mcp
from nonebot_plugin_hermes.core.active_session import ActiveSessionManager
from nonebot_plugin_hermes.core.bot_registry import BotRegistry
from nonebot_plugin_hermes.core.hermes_client import (
    ChatResult,
    _try_parse_first_json_block,
    extract_response_media,
    maybe_extract_decision_reply_text,
)
from nonebot_plugin_hermes.core.inflight import InflightRegistry
from nonebot_plugin_hermes.core.message_buffer import MessageBuffer
from nonebot_plugin_hermes.core.outbound import _parse_image_data_url
from nonebot_plugin_hermes.core.storage.image_cache import ImageCache
from nonebot_plugin_hermes.core.storage.image_fetcher import ImageFetcher
from nonebot_plugin_hermes.core.storage.message_store import MessageStore

_PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4096).decode("ascii")
_PNG_DATA_URL = f"data:image/png;base64,{_PNG_B64}"


def test_parse_image_data_url_accepts_whitespace_and_missing_padding():
    """无损的宽容:payload 内联进 JSON 后可能带折行,尾部 `=` 也可能被省。"""
    valid_b64 = base64.b64encode(b"hello image bytes").decode("ascii")

    res = _parse_image_data_url(f"data:image/jpeg;base64,{valid_b64}")
    assert res is not None
    raw, mime, _payload = res
    assert raw == b"hello image bytes"
    assert mime == "image/jpeg"

    unpadded = f"data:image/png;base64,  {valid_b64.rstrip('=')} \n"
    res2 = _parse_image_data_url(unpadded)
    assert res2 is not None
    assert res2[0] == b"hello image bytes", "补 padding 必须还原出同一批字节"
    assert res2[1] == "image/png"


def test_parse_image_data_url_rejects_corrupt_payload():
    """有损修补是反功能:半截 base64 强解出来是坏图,平台要么拒收要么显示破图,
    还把"上游把回复截断了"伪装成"发图成功"。宁可跳过 + 留日志。"""
    b64 = base64.b64encode(bytes(range(256)) * 8).decode("ascii")
    truncated = b64[:1025]  # 1025 % 4 == 1 —— base64 不可能有的长度
    assert _parse_image_data_url(f"data:image/png;base64,{truncated}") is None
    assert _parse_image_data_url("data:image/png;base64,!!!!") is None
    assert _parse_image_data_url("data:image/png;base64,   ") is None


def test_extract_response_media_with_base64_data_uri():
    text = f"画好啦! ![image]({_PNG_DATA_URL}) 喜欢吗?"
    cleaned, urls = extract_response_media(text)
    assert cleaned == "画好啦!  喜欢吗?"
    assert urls == [_PNG_DATA_URL]


def test_extract_response_media_truncated_data_uri_becomes_placeholder():
    """上游回复被截断 → markdown 图片没闭合 `)`。半截 data URL 不能当图投递,
    留占位让用户知道"有图但没传全",而不是既没图也没解释。"""
    text = f"画好啦! ![image]({_PNG_DATA_URL[:-20]}"
    cleaned, urls = extract_response_media(text)
    assert urls == []
    assert "data:image" not in cleaned
    assert "画好啦!" in cleaned
    assert "[图片" in cleaned


def test_extract_response_media_host_path_markdown_image_placeholder():
    """api_server 拒绝内联时(>5MB / 非图片后缀 / 路径校验失败)markdown 里留的是
    主机本地路径 —— bot 侧抓不到,但不能静默删掉,否则用户只看到"画好啦"没有图。"""
    cleaned, urls = extract_response_media("画好啦 ![image](/root/hermes/media/out_1.png) 看看")
    assert urls == []
    assert cleaned == "画好啦 [生成了文件: out_1.png] 看看"

    cleaned2, urls2 = extract_response_media("![image](file:///root/hermes/media/out_2.png)")
    assert urls2 == []
    assert cleaned2 == "[生成了文件: out_2.png]"


def test_structured_parse_of_inline_image_stays_fast():
    """内联大图后 submit_decision JSON 是 MB 级。json5 是纯 Python 解析器,
    实测 6.6MB ≈ 55s CPU,而解析跑在 event loop 里 —— 一次发图能把整个 bot 冻住。
    合法 JSON 必须走 stdlib json 快路径。"""
    b64 = base64.b64encode(b"\x89PNG" + b"\x00" * (400 * 1024)).decode("ascii")
    raw = json.dumps(
        {"should_reply": True, "reply_text": f"给你 ![image](data:image/png;base64,{b64})"},
        ensure_ascii=False,
    )
    t0 = time.perf_counter()
    parsed = _try_parse_first_json_block(raw)
    elapsed = time.perf_counter() - t0

    assert parsed is not None
    assert parsed["should_reply"] is True
    assert elapsed < 1.0, f"解析 {len(raw) // 1024}KB 结构化回复耗时 {elapsed:.1f}s,快路径没生效"


def test_structured_parse_with_raw_newline_and_inline_image_stays_fast():
    """模型在 reply_text 里嵌真换行(JSON5 不允)+ 内联大图:转义后仍走 stdlib json。"""
    b64 = base64.b64encode(b"\x89PNG" + b"\x00" * (400 * 1024)).decode("ascii")
    raw = f'{{"should_reply": true, "reply_text": "第一段\n第二段 ![image](data:image/png;base64,{b64})"}}'
    t0 = time.perf_counter()
    parsed = _try_parse_first_json_block(raw)
    elapsed = time.perf_counter() - t0

    assert parsed is not None
    assert "第二段" in parsed["reply_text"]
    assert elapsed < 2.0, f"带裸换行的 {len(raw) // 1024}KB 回复耗时 {elapsed:.1f}s,快路径没生效"


def test_try_parse_first_json_block_salvages_truncated_json():
    raw_truncated = (
        '{"should_reply": true, "reply_text": "画好啦主人喵~! ![image](data:image/jpeg;base64,/9j/4AAQSkZJRg...'
    )
    parsed = _try_parse_first_json_block(raw_truncated)
    assert parsed is not None
    assert parsed.get("should_reply") is True
    assert "画好啦主人喵" in parsed.get("reply_text", "")
    assert parsed.get("_salvaged") is True


def test_maybe_extract_decision_reply_text_salvages_truncated_json():
    raw_truncated = '{"should_reply": true, "reply_text": "画好啦! ![image](data:image/jpeg;base64,/9j/4AAQSkZJRg...'
    extracted = maybe_extract_decision_reply_text(raw_truncated)
    assert extracted is not None
    assert "画好啦" in extracted


def test_salvage_preserves_should_reply_false():
    """救补不能把模型"这条不归我"的决定翻成插话 —— 抢答别人的对话比丢一条回复更糟。"""
    broken = (
        '{"should_reply": false, "reply_text": "", "topic_hint": "别人在聊自己的事", '
        '"note": "user said "算了" so I stay quiet' + " padding" * 80 + "}"
    )
    parsed = _try_parse_first_json_block(broken)
    assert parsed is not None
    assert parsed.get("should_reply") is False
    assert parsed.get("_salvaged") is True
    assert maybe_extract_decision_reply_text(broken) == "", "should_reply=false 应静默"


# ---------------------------------------------------------------- handler 层


@dataclass
class _FakeTarget:
    id: str
    private: bool = False
    adapter: str = "ob11"


@pytest.fixture
def _runtime(tmp_path):
    store = MessageStore(db_path=tmp_path / "messages.db")
    cache = ImageCache(cache_dir=tmp_path / "imgs", quota_bytes=1024 * 1024)
    fetcher = ImageFetcher(store=store, cache=cache)
    _mcp.message_buffer = MessageBuffer(store=store, fetcher=fetcher)
    _mcp.active_sessions = ActiveSessionManager(default_ttl_sec=300)
    _mcp.bot_registry = BotRegistry()
    _mcp.inflight = InflightRegistry()
    yield
    _mcp.message_buffer = None
    _mcp.active_sessions = None
    _mcp.bot_registry = None
    _mcp.inflight = None
    store.close()


@pytest.mark.asyncio
async def test_reactive_reply_buffers_placeholder_not_base64(monkeypatch, _runtime):
    """回写 buffer 必须用清洗后的文本 + [图片] 占位。

    api_server 把 MEDIA: 图片内联成 data:image;base64 —— 一张 300KB 图就是 40 万字符。
    原文入库后会被逐 turn 渲染进 <recent_messages>,几轮就把上下文顶满,
    模型开始引用错历史。入站侧本来就只存 [图片] 占位,出站要对齐。
    """
    from nonebot_plugin_hermes.handlers import message as handler_mod

    now = 5_000_000
    _mcp.active_sessions.trigger("ob11", "g1", "u1", now_ms=now)

    reply_text = f"画好啦! ![image]({_PNG_DATA_URL}) 喜欢吗?"
    monkeypatch.setattr(
        handler_mod.hermes_client,
        "chat",
        AsyncMock(
            return_value=ChatResult(raw_text=reply_text, structured={"should_reply": True, "reply_text": reply_text})
        ),
    )
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(handler_mod, "send_text_with_media", send_mock)

    bot = MagicMock()
    bot.self_id = "999"
    await handler_mod._run_reactive_turn(
        bot=bot,
        target=_FakeTarget(id="g1"),
        adapter_name="ob11",
        user_id="u1",
        group_id="g1",
        text="画张图",
        image_urls=[],
        is_explicit_trigger=True,
        now_ms=now,
    )

    # 出向:文本清洗掉 markdown,图片走 media_urls
    assert send_mock.call_args.kwargs["text"] == "画好啦!  喜欢吗?"
    assert send_mock.call_args.kwargs["media_urls"] == [_PNG_DATA_URL]

    # 回写:不能带 base64
    recent = _mcp.message_buffer.get_recent(adapter="ob11", group_id="g1", limit=5)
    bot_lines = [m for m in recent if m.is_bot]
    assert len(bot_lines) == 1
    content = bot_lines[0].content
    assert "data:image" not in content, f"base64 进了历史(len={len(content)})"
    assert "[图片]" in content
    assert "画好啦!" in content
    assert len(content) < 200


def _decision_with_image(b64: str, *, suffix: str) -> str:
    """构造一条 api_server 内联大图后的 submit_decision 回复。"""
    return (
        f'{{"should_reply": true, "reply_text": "画好啦喵! ![image](data:image/jpeg;base64,{b64})' + '"' + suffix + "}"
    )


def test_inline_image_decision_keeps_all_fields():
    """带图回复必须走完整解析,不能退到救补。

    救补只认 should_reply + reply_text,topic_hint / should_exit_active 会被丢掉 ——
    活跃态的话题跟踪和退场判断在每个带图 turn 上静默失效。data URL 是生成之后由
    api_server 内联的,JSON 结构本身只有几百字节,先摘掉 payload 再解析即可。
    """
    b64 = base64.b64encode(b"\xff\xd8\xff" + b"\x00" * (400 * 1024)).decode("ascii")
    raw = _decision_with_image(b64, suffix=', "topic_hint": "黑长直自画像", "should_exit_active": false')

    parsed = _try_parse_first_json_block(raw)
    assert parsed is not None
    assert parsed.get("_salvaged") is None, "完整 JSON 不该落到救补"
    assert parsed["topic_hint"] == "黑长直自画像"
    assert parsed["should_exit_active"] is False
    # 图必须原样回填,能一路解出字节
    _cleaned, urls = extract_response_media(parsed["reply_text"])
    assert len(urls) == 1
    assert _parse_image_data_url(urls[0]) is not None


def test_inline_image_decision_with_json5_only_syntax():
    """尾随逗号这类只有 json5 认的脏语法 + 大图:摘掉 payload 后候选串回到 KB 级,
    json5 仍然跑得起来(此前会被尺寸阈值挡掉,直接退救补)。"""
    b64 = base64.b64encode(b"\xff\xd8\xff" + b"\x00" * (400 * 1024)).decode("ascii")
    raw = _decision_with_image(b64, suffix=', "topic_hint": "画图",')

    parsed = _try_parse_first_json_block(raw)
    assert parsed is not None
    assert parsed.get("_salvaged") is None
    assert parsed["topic_hint"] == "画图"
    assert "data:image/jpeg;base64," in parsed["reply_text"]


def test_inline_image_reattach_is_byte_exact():
    """回填后的 reply_text 必须与原文逐字节一致,不能因为摘/填丢字符。"""
    b64 = base64.b64encode(b"\xff\xd8\xff" + b"\x00" * 300_000).decode("ascii")
    inner = f"两张图 ![a](data:image/png;base64,{b64}) 和 ![b](data:image/jpeg;base64,{b64})"
    raw = json.dumps({"should_reply": True, "reply_text": inner}, ensure_ascii=False)

    parsed = _try_parse_first_json_block(raw)
    assert parsed is not None
    assert parsed["reply_text"] == inner


def test_truncated_inline_image_still_salvages():
    """真被截断时(无闭合引号/括号)仍走救补,且截断的 data URL 不当图投递。"""
    b64 = base64.b64encode(b"\xff\xd8\xff" + b"\x00" * 300_000).decode("ascii")
    raw = '{"should_reply": true, "reply_text": "画好啦 ![image](data:image/jpeg;base64,' + b64[: len(b64) // 2]

    parsed = _try_parse_first_json_block(raw)
    assert parsed is not None
    assert parsed.get("_salvaged") is True
    cleaned, urls = extract_response_media(parsed["reply_text"])
    assert urls == []
    assert "[图片" in cleaned


# 线上实测的破法:reply_text 以 ![image](…) 结尾时,模型漏掉闭合 `"`,
# 于是 `)` 后面直接跟 `,"topic_hint"`。19:28 与 19:30 两轮一模一样。
def _malformed_decision_missing_quote(b64: str) -> str:
    return (
        '{"should_reply":true,"reply_text":"好的主人,这就重新投递刚才那张漫画风图喵~ '
        f"![image](data:image/jpeg;base64,{b64})"
        ',"topic_hint":"重新投递漫画图","should_exit_active":false}'
    )


def test_salvage_recovers_topic_hint_and_exit_flag():
    """信封破了也要把 topic_hint / should_exit_active 捞回来。

    带图 turn 几乎每次都走救补,而救补以前只认 should_reply + reply_text ——
    等于活跃态的话题跟踪与退场判断在最常见的场景里静默失效。
    """
    b64 = base64.b64encode(b"\xff\xd8\xff" + b"\x00" * 300_000).decode("ascii")
    parsed = _try_parse_first_json_block(_malformed_decision_missing_quote(b64))

    assert parsed is not None
    assert parsed.get("_salvaged") is True
    assert parsed["should_reply"] is True
    assert parsed["topic_hint"] == "重新投递漫画图"
    assert parsed["should_exit_active"] is False


def test_salvage_drops_envelope_comma_artifact():
    """漏引号会让 reply_text 一路吃到 `,"topic_hint"` 前,尾部多个逗号 ——
    那是信封残渣,不能当正文发到群里。图必须完好可投。"""
    b64 = base64.b64encode(b"\xff\xd8\xff" + b"\x00" * 300_000).decode("ascii")
    parsed = _try_parse_first_json_block(_malformed_decision_missing_quote(b64))

    assert parsed is not None
    cleaned, urls = extract_response_media(parsed["reply_text"])
    assert not cleaned.endswith(","), f"尾部逗号没清掉: {cleaned[-20:]!r}"
    assert cleaned.endswith("喵~")
    assert len(urls) == 1
    assert _parse_image_data_url(urls[0]) is not None, "图必须仍然可解"
