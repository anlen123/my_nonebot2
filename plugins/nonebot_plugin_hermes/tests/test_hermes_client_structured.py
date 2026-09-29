"""HermesClient 结构化输出测试(路径 B)。"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pytest import MonkeyPatch

from nonebot_plugin_hermes.core.hermes_client import (
    HermesClient,
    extract_response_media,
)


class _MockResponse:
    def __init__(self, status_code: int, body: dict[str, Any], headers: dict[str, str] | None = None):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)
        # 真实 httpx.Response 一定带 headers;客户端要从 X-Hermes-Session-Id 读
        # 上游本轮实际使用的 session id(compression 轮换后会变)。
        self.headers = headers or {}

    def json(self) -> dict[str, Any]:
        return self._body


class _MockClient:
    def __init__(self, *, response: _MockResponse) -> None:
        self._response = response
        self.last_payload: dict[str, Any] | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def post(self, url, *, json, headers):
        self.last_payload = json
        return self._response


def _patch_httpx(monkeypatch: MonkeyPatch, response: _MockResponse) -> _MockClient:
    client = _MockClient(response=response)

    def factory(*args: Any, **kwargs: Any) -> _MockClient:
        return client

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return client


@pytest.mark.asyncio
async def test_path_b_extracts_first_json5_block(monkeypatch: MonkeyPatch):
    body = {
        "choices": [
            {
                "message": {
                    "content": (
                        "好的,这是我的决策:\n"
                        "{ should_reply: true, reply_text: '收到', topic_hint: 'greeting' }\n"
                        "(说明:简单问候)"
                    )
                }
            }
        ]
    }
    _patch_httpx(monkeypatch, _MockResponse(200, body))

    client = HermesClient()
    r = await client.chat(
        text="hi",
        session_key="s1",
        user_id="u1",
        group_id="g1",
        adapter_name="ob11",
        is_private=False,
        mode="reactive",
        expect_structured=True,
        structured_tool_name="submit_decision",
    )
    assert r.parse_failed is False
    assert r.structured == {"should_reply": True, "reply_text": "收到", "topic_hint": "greeting"}


@pytest.mark.asyncio
async def test_path_b_no_json_block_marks_parse_failed(monkeypatch: MonkeyPatch):
    body = {"choices": [{"message": {"content": "我无法生成 JSON。"}}]}
    _patch_httpx(monkeypatch, _MockResponse(200, body))
    client = HermesClient()
    r = await client.chat(
        text="hi",
        session_key="s1",
        user_id="u1",
        group_id="g1",
        adapter_name="ob11",
        is_private=False,
        mode="reactive",
        expect_structured=True,
        structured_tool_name="submit_decision",
    )
    assert r.parse_failed is True
    # raw_text 必须保留,handler 据此降级展示 / 记日志
    assert r.raw_text == "我无法生成 JSON。"
    assert r.is_transport_error is False


@pytest.mark.asyncio
async def test_path_b_passive_mode_returns_raw_text(monkeypatch: MonkeyPatch):
    """passive 模式:expect_structured=False,直接返 raw_text 不解析。"""
    body = {"choices": [{"message": {"role": "assistant", "content": "raw fallback message"}}]}
    _patch_httpx(monkeypatch, _MockResponse(200, body))

    client = HermesClient()
    result = await client.chat(
        text="hi",
        session_key="s1",
        user_id="u1",
        adapter_name="ob11",
        is_private=True,
        group_id=None,
        mode="passive",
        expect_structured=False,
    )
    assert result.parse_failed is False
    assert result.raw_text == "raw fallback message"
    assert result.structured is None


def test_extract_response_media_strips_md_and_media_tags():
    text = "hello ![a](http://x/y.png) MEDIA:http://x/z.mp4 world"
    cleaned, urls = extract_response_media(text)
    assert "http://x/y.png" in urls
    assert "http://x/z.mp4" in urls
    assert "![a]" not in cleaned
    assert "MEDIA:" not in cleaned


def test_extract_response_media_collects_data_url_images():
    data_url = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="
    text = f"看这张图 ![image]({data_url}) 完毕"
    cleaned, urls = extract_response_media(text)
    assert data_url in urls
    assert "data:" not in cleaned
    assert "![image]" not in cleaned


def test_extract_response_media_ignores_non_image_data_urls():
    data_url = "data:text/plain;base64,aGVsbG8="
    text = f"![f]({data_url}) 结果"
    cleaned, urls = extract_response_media(text)
    assert urls == []
    assert "![f]" not in cleaned


def test_extract_response_media_replaces_host_path_media_with_placeholder():
    # 非图片媒体(音频/视频/>5MB 图)api_server 不改写,以字面 MEDIA:/host/path 返回。
    text = "转换完成 MEDIA:/tmp/hermes/out/report.mp3 请查收"
    cleaned, urls = extract_response_media(text)
    assert urls == []
    assert "[生成了文件: report.mp3]" in cleaned
    assert "MEDIA:" not in cleaned
    assert "/tmp/hermes" not in cleaned


def test_extract_response_media_host_path_without_slash():
    cleaned, urls = extract_response_media("MEDIA:foo.bin")
    assert urls == []
    assert cleaned == "[生成了文件: foo.bin]"


def test_extract_response_media_data_url_media_tag_goes_to_urls():
    tag = "data:image/png;base64,iVBORw0KGgo="
    cleaned, urls = extract_response_media(f"图 MEDIA:{tag} 完")
    assert urls == [tag]
    assert "MEDIA:" not in cleaned


@pytest.mark.asyncio
async def test_path_b_appends_decision_hint_to_system_prompt(monkeypatch: MonkeyPatch):
    """expect_structured=True 时,system prompt 必须含 STRUCTURED OUTPUT 段。"""
    body = {"choices": [{"message": {"content": '{"should_reply": false}'}}]}
    mock = _patch_httpx(monkeypatch, _MockResponse(200, body))

    client = HermesClient()
    await client.chat(
        text="hi",
        session_key="s1",
        user_id="u1",
        group_id="g1",
        adapter_name="ob11",
        is_private=False,
        mode="reactive",
        expect_structured=True,
        structured_tool_name="submit_decision",
    )
    payload = mock.last_payload
    assert payload is not None
    assert "tools" not in payload  # 路径 B 不发 tools
    assert "tool_choice" not in payload
    sys_msg = payload["messages"][0]["content"]
    assert "STRUCTURED OUTPUT" in sys_msg
    assert "should_reply" in sys_msg


@pytest.mark.asyncio
async def test_http_error_marks_transport_and_parse_failed(monkeypatch: MonkeyPatch):
    """HTTP 500 必须同时标 parse_failed=True 与 is_transport_error=True,
    供 Task 15 handler 区分"重试 vs 降级"。用户可见文案带上 reason 片段, 不再只露状态码。"""
    _patch_httpx(monkeypatch, _MockResponse(500, {"error": "server down"}))

    client = HermesClient()
    r = await client.chat(
        text="hi",
        session_key="s1",
        user_id="u1",
        group_id="g1",
        adapter_name="ob11",
        is_private=False,
        mode="reactive",
        expect_structured=True,
        structured_tool_name="submit_decision",
    )
    assert r.parse_failed is True
    assert r.is_transport_error is True
    assert "server down" in r.raw_text


@pytest.mark.asyncio
async def test_wrapped_provider_image_url_error_yields_vision_hint(monkeypatch: MonkeyPatch):
    """Hermes 把 provider 端 400 包成 502, body.error.message 含 `image_url` 不支持时,
    用户看到的是"不支持图片识别"而不是误导性的 502。"""
    inner = (
        "Error code: 400 - {'error': {'message': 'Error from provider (DeepSeek): "
        "Failed to deserialize the JSON body into the target type: messages[1]: "
        "unknown variant `image_url`, expected one of ...'}}"
    )
    _patch_httpx(monkeypatch, _MockResponse(502, {"error": {"message": inner}}))

    client = HermesClient()
    r = await client.chat(
        text="看图",
        image_urls=["https://example.com/x.jpg"],
        session_key="s1",
        user_id="u1",
        group_id="g1",
        adapter_name="ob11",
        is_private=False,
        mode="passive",
    )
    assert r.is_transport_error is True
    assert r.parse_failed is True
    assert "图片" in r.raw_text  # 命中 vision-unsupported 分支
    # 不再把外层 502 直接当真因显示
    assert "502" not in r.raw_text


@pytest.mark.asyncio
async def test_wrapped_error_with_message_surfaces_inner_reason(monkeypatch: MonkeyPatch):
    """非 vision 类的 wrap-502, 用户可见文案带上 error.message 片段。"""
    _patch_httpx(
        monkeypatch,
        _MockResponse(502, {"error": {"message": "rate limit exceeded"}}),
    )

    client = HermesClient()
    r = await client.chat(
        text="hi",
        session_key="s1",
        user_id="u1",
        group_id="g1",
        adapter_name="ob11",
        is_private=False,
        mode="passive",
    )
    assert r.is_transport_error is True
    assert "rate limit exceeded" in r.raw_text


@pytest.mark.asyncio
async def test_empty_choices_with_expect_structured_marks_parse_failed(monkeypatch: MonkeyPatch):
    """期望结构化但 choices=[] 是结构性失败,而非"模型选择不回"。"""
    _patch_httpx(monkeypatch, _MockResponse(200, {"choices": []}))

    client = HermesClient()
    r = await client.chat(
        text="hi",
        session_key="s1",
        user_id="u1",
        group_id="g1",
        adapter_name="ob11",
        is_private=False,
        mode="reactive",
        expect_structured=True,
        structured_tool_name="submit_decision",
    )
    assert r.parse_failed is True
    # 但不是 transport 错(HTTP 200,只是响应 body 为空 choices)
    assert r.is_transport_error is False


@pytest.mark.asyncio
async def test_user_content_override_replaces_text_and_image_urls(monkeypatch: MonkeyPatch):
    """prompt_builder 给 user_content_override 时,text/image_urls 应被忽略。"""
    body = {"choices": [{"message": {"content": "ok"}}]}
    mock = _patch_httpx(monkeypatch, _MockResponse(200, body))

    custom_content = [
        {"type": "text", "text": "<<HEAD>>"},
        {"type": "image_url", "image_url": {"url": "http://x/a.jpg"}},
    ]
    client = HermesClient()
    await client.chat(
        text="ignored",
        image_urls=["http://ignored.png"],
        session_key="s1",
        user_id="u1",
        group_id="g1",
        adapter_name="ob11",
        is_private=False,
        user_content_override=custom_content,
    )
    sent = mock.last_payload["messages"][1]["content"]
    assert sent == custom_content
    assert "ignored" not in str(sent)


def test_decision_hint_field_names_are_canonical():
    """_DECISION_HINT 必须用 canonical 字段名(should_reply/reply_text/topic_hint/
    should_exit_active),与 prompt_builder 的 decision_protocol 同口径。任何字段
    drift(如 topic_tag、shouldReply 驼峰)都应被这个测试拦下。"""
    from nonebot_plugin_hermes.core.hermes_client import _DECISION_HINT

    assert "should_reply" in _DECISION_HINT
    assert "reply_text" in _DECISION_HINT
    assert "topic_hint" in _DECISION_HINT
    assert "should_exit_active" in _DECISION_HINT
    # 反例:防 drift
    assert "topic_tag" not in _DECISION_HINT
    assert "shouldReply" not in _DECISION_HINT  # camelCase 漂移


# --- maybe_extract_decision_reply_text (passive defensive parse) ---


def test_extract_decision_reply_text_basic():
    from nonebot_plugin_hermes.core.hermes_client import maybe_extract_decision_reply_text

    out = maybe_extract_decision_reply_text('{"should_reply": true, "reply_text": "嗯嗯,知道了"}')
    assert out == "嗯嗯,知道了"


def test_extract_decision_reply_text_silent():
    from nonebot_plugin_hermes.core.hermes_client import maybe_extract_decision_reply_text

    # should_reply=false → 显式静默,返回空串(让调用方走静默分支)
    out = maybe_extract_decision_reply_text('{"should_reply": false, "topic_hint": "x"}')
    assert out == ""


def test_extract_decision_reply_text_not_decision():
    from nonebot_plugin_hermes.core.hermes_client import maybe_extract_decision_reply_text

    # 普通文字回复 → None,调用方继续用原文
    assert maybe_extract_decision_reply_text("今天天气真好") is None
    # 是 JSON 但不含 should_reply 字段 → 不算 submit_decision
    assert maybe_extract_decision_reply_text('{"foo": 1, "bar": 2}') is None


def test_extract_decision_reply_text_with_prefix():
    """LLM 输出常见 'sure! {...}' 形式;前缀文字不应阻止抽取。"""
    from nonebot_plugin_hermes.core.hermes_client import maybe_extract_decision_reply_text

    out = maybe_extract_decision_reply_text('noise before {"should_reply": true, "reply_text": "ok"}')
    assert out == "ok"


def test_extract_decision_reply_text_should_reply_no_text():
    """should_reply=true 但缺 reply_text → None(让调用方按未识别处理 / 走原文)。"""
    from nonebot_plugin_hermes.core.hermes_client import maybe_extract_decision_reply_text

    out = maybe_extract_decision_reply_text('{"should_reply": true}')
    assert out is None


# --- raw-newline 容错回退(regression for "@user {raw JSON dumped to chat}" bug) ---


@pytest.mark.asyncio
async def test_path_b_parses_decision_with_raw_newlines_in_reply_text(monkeypatch: MonkeyPatch):
    """LLM 在 reply_text 里嵌真换行是高频 emission 模式(段落分隔),JSON5 规范
    不允许,首发 json5.loads 会抛 `Unexpected "\\n"`。状态机回退必须把整段
    decision 还原出来,reply_text 里的换行原样保留(以 \\n 形式)。

    Regression: 不修这个,reactive 路径会走 parse_failed 兜底,把整段 JSON
    信封 @ 发给用户。
    """
    raw = (
        "{\n"
        '  "should_reply": true,\n'
        '  "reply_text": "第一段内容。\n'
        "\n"
        '第二段内容。",\n'
        '  "topic_hint": "demo",\n'
        '  "should_exit_active": false\n'
        "}"
    )
    body = {"choices": [{"message": {"content": raw}}]}
    _patch_httpx(monkeypatch, _MockResponse(200, body))
    client = HermesClient()
    r = await client.chat(
        text="hi",
        session_key="s1",
        user_id="u1",
        group_id="g1",
        adapter_name="ob11",
        is_private=False,
        mode="reactive",
        expect_structured=True,
        structured_tool_name="submit_decision",
    )
    assert r.parse_failed is False
    assert r.structured is not None
    assert r.structured["should_reply"] is True
    # reply_text 里的换行被还原为字符串内容(state machine 转义后 json5 解码)
    assert r.structured["reply_text"] == "第一段内容。\n\n第二段内容。"
    assert r.structured["topic_hint"] == "demo"
    assert r.structured["should_exit_active"] is False


def test_parse_first_json_block_genuinely_malformed_still_fails():
    """状态机只补换行,不补结构;信封里捞不出内容时必须仍然 parse_failed。"""
    from nonebot_plugin_hermes.core.hermes_client import _try_parse_first_json_block

    # 没有 should_reply:根本不是 submit_decision 信封,交给调用方按散文处理
    assert _try_parse_first_json_block('{"reply_text": "x"') is None
    # 有信封但没有 reply_text 可捞,且块不平衡 → 无从重建
    assert _try_parse_first_json_block('{"should_reply": true, "topic_hint": "x"') is None
    assert _try_parse_first_json_block('{"should_reply": true, "topic_hint": "a\nb"') is None


def test_parse_first_json_block_stray_quote_after_bool():
    """值后面多一个引号(`"should_exit_active": false"}`):多出的引号把收尾 `}` 吞进
    字符串,平衡块扫不出来,json/json5 也都拒解析 —— 必须由救补把整个信封捞回来。

    纯文本短回复既没有媒体标记也不够长,是此前救补闸门唯一够不到的破法。"""
    from nonebot_plugin_hermes.core.hermes_client import _try_parse_first_json_block

    raw = (
        '{"should_reply": true, "reply_text": "第一句，第二句：收尾", '
        '"topic_hint": "demo-topic", "should_exit_active": false"}'
    )
    parsed = _try_parse_first_json_block(raw)
    assert parsed is not None
    assert parsed["should_reply"] is True
    # 正文里的全角标点是合法中文,救补路径同样一个字节都不能动
    assert parsed["reply_text"] == "第一句，第二句：收尾"
    assert parsed["topic_hint"] == "demo-topic"
    assert parsed["should_exit_active"] is False


def test_parse_first_json_block_short_broken_envelope_keeps_should_reply_false():
    """短回复的破信封同样要保住 should_reply=false —— 捞不回来就会退到把整段
    JSON 当散文发进群(显式触发)或直接丢掉(窗内跟随)。"""
    from nonebot_plugin_hermes.core.hermes_client import _try_parse_first_json_block

    parsed = _try_parse_first_json_block('{"should_reply": false, "reply_text": "", "topic_hint": "demo-topic"')
    assert parsed is not None
    assert parsed["should_reply"] is False


def test_parse_first_json_block_preserves_embedded_quotes():
    """状态机必须正确处理 \\" 转义,不能误把它当 quote 退出 string state
    (否则会把后面的真换行漏掉转义)。"""
    from nonebot_plugin_hermes.core.hermes_client import _try_parse_first_json_block

    raw = '{"should_reply": true, "reply_text": "他说\\"你好\\"\n下一段"}'
    parsed = _try_parse_first_json_block(raw)
    assert parsed is not None
    assert parsed["reply_text"] == '他说"你好"\n下一段'


# --- 栈式 brace-matcher 扩展能力(小模型 emission 容错) ---


def test_parse_first_json_block_nested_object():
    """嵌套对象:旧版单层正则会抠到 `{"reply_text": "x", "meta": {"a": 1}` 然后 json5 失败。
    栈式 matcher 必须穿过内层 `{...}` 并拿到完整外层平衡块。"""
    from nonebot_plugin_hermes.core.hermes_client import _try_parse_first_json_block

    raw = '{"should_reply": true, "reply_text": "x", "meta": {"a": 1, "b": {"c": 2}}}'
    parsed = _try_parse_first_json_block(raw)
    assert parsed is not None
    assert parsed["reply_text"] == "x"
    assert parsed["meta"] == {"a": 1, "b": {"c": 2}}


def test_parse_first_json_block_string_contains_braces():
    """字符串字面量内的 `{` / `}` 不能参与深度计数。"""
    from nonebot_plugin_hermes.core.hermes_client import _try_parse_first_json_block

    raw = '{"should_reply": true, "reply_text": "代码: if (x) {return 1;} else {return 2;}"}'
    parsed = _try_parse_first_json_block(raw)
    assert parsed is not None
    assert parsed["reply_text"] == "代码: if (x) {return 1;} else {return 2;}"


def test_parse_first_json_block_markdown_fenced():
    """模型把 JSON 包在 ```json ... ``` 围栏里:matcher 从首个 `{` 开始扫,
    fence 反引号天然不参与计数,块仍能抠出。"""
    from nonebot_plugin_hermes.core.hermes_client import _try_parse_first_json_block

    raw = '```json\n{"should_reply": true, "reply_text": "ok"}\n```'
    parsed = _try_parse_first_json_block(raw)
    assert parsed is not None
    assert parsed["should_reply"] is True
    assert parsed["reply_text"] == "ok"


def test_parse_first_json_block_single_quoted_string():
    """JSON5 允许单引号字符串;matcher 也得在单引号 string state 内忽略 `{` / `}`。"""
    from nonebot_plugin_hermes.core.hermes_client import _try_parse_first_json_block

    raw = "{'should_reply': true, 'reply_text': '里面有 } 字符'}"
    parsed = _try_parse_first_json_block(raw)
    assert parsed is not None
    assert parsed["reply_text"] == "里面有 } 字符"


def test_parse_first_json_block_prose_after_json():
    """JSON 后面还跟一段说明文字 — 抠到的应是首个平衡块,后续 prose 不影响解析。"""
    from nonebot_plugin_hermes.core.hermes_client import _try_parse_first_json_block

    raw = '{"should_reply": false, "topic_hint": "demo"}\n\n以上是我的判断。'
    parsed = _try_parse_first_json_block(raw)
    assert parsed is not None
    assert parsed["should_reply"] is False
    assert parsed["topic_hint"] == "demo"


# --- 全角结构标点容错(中文模型 emission) ---


def test_parse_first_json_block_fullwidth_comma_separator():
    """中文模型常在字段分隔符处吐全角逗号 ，(U+FF0C):字符串**外**的要归一成 ASCII,
    而 reply_text 正文里的全角逗号是合法中文,必须原样保留。

    复刻真实事故:整串 JSON 因这一个全角逗号被拒解析,随后作为 raw_text 原样发进群。"""
    from nonebot_plugin_hermes.core.hermes_client import _try_parse_first_json_block

    raw = '{"should_reply": true, "reply_text": "第一句，第二句"，"should_exit_active": false}'
    parsed = _try_parse_first_json_block(raw)
    assert parsed is not None
    assert parsed["should_reply"] is True
    assert parsed["should_exit_active"] is False
    # 正文里的全角逗号原样保留,一个字节都不能动
    assert parsed["reply_text"] == "第一句，第二句"


def test_parse_first_json_block_fullwidth_colon_separator():
    """键值冒号处的全角 :(U+FF1A)同样归一;正文里的全角冒号保留。"""
    from nonebot_plugin_hermes.core.hermes_client import _try_parse_first_json_block

    raw = '{"should_reply"：true, "reply_text": "标题：正文"}'
    parsed = _try_parse_first_json_block(raw)
    assert parsed is not None
    assert parsed["should_reply"] is True
    assert parsed["reply_text"] == "标题：正文"


def test_parse_first_json_block_fullwidth_inside_string_untouched():
    """全 ASCII 结构 + 正文含全角标点:归一化是 no-op,字符串内部不得改动(回归护栏)。"""
    from nonebot_plugin_hermes.core.hermes_client import _try_parse_first_json_block

    raw = '{"should_reply": true, "reply_text": "喵，你好：世界，再见"}'
    parsed = _try_parse_first_json_block(raw)
    assert parsed is not None
    assert parsed["reply_text"] == "喵，你好：世界，再见"
