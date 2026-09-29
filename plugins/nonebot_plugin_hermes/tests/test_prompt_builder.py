"""prompt_builder 单元测试。

约定(0.2.x 起):
- reactive system prompt 是纯 decision_protocol(byte-stable);runtime_state
  整体放在 user content 顶端。
- passive system prompt 是纯 Message Context;recent_messages 整体放在
  user content。

system 端字节稳定是为 LLM 前缀缓存让路的核心原则——所有 per-turn 变化字段
必须在 user 这一侧出现,否则缓存就在 system 出现 volatile 字节那一刻断掉。
"""

from __future__ import annotations

from nonebot_plugin_hermes.core.message_buffer import BufferedMessage
from nonebot_plugin_hermes.core.prompt_builder import (
    _format_speaker_tag,
    build_passive_system_prompt,
    build_passive_user_content,
    build_reactive_system_prompt,
    build_reactive_user_content,
)

# --- _format_speaker_tag ---


def test_format_speaker_tag_real_nickname_includes_id():
    """昵称与 user_id 不同 → 同时出 id=,LLM 可按 id 匹配 SOUL.md 身份。"""
    assert _format_speaker_tag("肯尼", "u-test-1") == "[user=肯尼 id=u-test-1]"


def test_format_speaker_tag_nickname_equals_user_id_omits_id():
    """昵称就是裸 user_id(没抽到真实 nickname)→ 省 id= 避免重复。"""
    assert _format_speaker_tag("12345", "12345") == "[user=12345]"


def test_format_speaker_tag_none_nickname_uses_user_id_only():
    """nickname=None / 空串 → 退回 [user=ID],无 id= 字段。"""
    assert _format_speaker_tag(None, "12345") == "[user=12345]"
    assert _format_speaker_tag("", "12345") == "[user=12345]"
    assert _format_speaker_tag("   ", "12345") == "[user=12345]"


# --- helpers ---


def _msg(ts, sender="alice", content="hello", imgs=None, is_bot=False):
    return BufferedMessage(
        ts=ts,
        adapter="ob11",
        group_id="g1",
        user_id=sender,
        nickname=sender,
        content=content,
        image_urls=imgs or [],
        reply_to_ts=None,
        is_bot=is_bot,
    )


def _msg_with_id(ts, sender, content, msg_id, is_bot=False, imgs=None):
    return BufferedMessage(
        ts=ts,
        adapter="ob11",
        group_id="g1",
        user_id=sender,
        nickname=sender,
        content=content,
        image_urls=imgs or [],
        is_bot=is_bot,
        id=msg_id,
    )


def _reactive_user_kwargs(**overrides):
    """build_reactive_user_content 的默认入参,便于按需 override。"""
    defaults = {
        "adapter": "ob11",
        "group_id": "g1",
        "self_id": "bot-self",
        "self_nickname": "Bot昵称",
        "addressed_to_bot": False,
        "triggered_by": "u42",
        "triggered_by_nickname": None,
        "topic_hint": None,
        "recent_messages": [],
        "current_user_id": "charlie",
        "current_nickname": "Charlie",
        "current_text": "hello",
        "current_image_urls": [],
    }
    defaults.update(overrides)
    return defaults


# --- reactive system prompt(纯 decision_protocol,byte-stable)---


def test_reactive_system_prompt_is_only_decision_protocol():
    """runtime_state 已搬到 user content,system 不应再含任何 per-turn 字段。"""
    sp = build_reactive_system_prompt()
    assert "<decision_protocol>" in sp
    assert "submit_decision" in sp
    assert "<runtime_state>" not in sp
    assert "adapter:" not in sp
    assert "group_id:" not in sp
    assert "triggered_by:" not in sp


def test_reactive_system_prompt_is_byte_stable_across_calls():
    """system 无任何动态字段,两次调用结果必须字节完全相同——前缀缓存的前提。"""
    assert build_reactive_system_prompt() == build_reactive_system_prompt()


def test_decision_protocol_includes_recent_bot_self_guard():
    """decision_protocol 必须显式提示「上一条是你自己且无新指向你的内容 → false」,
    用于压制 reactive 模式下的连发凑话(模式 2)。"""
    sp = build_reactive_system_prompt()
    decision = sp.split("<decision_protocol>")[1]
    assert "[bot]" in decision
    assert "recent_messages" in decision


def test_decision_protocol_includes_no_repeat_guard():
    """decision_protocol 必须显式提示「已回过同一问题且无新疑问/反对 → false」,
    用于压制 reactive 模式下的重复凑话。"""
    sp = build_reactive_system_prompt()
    decision = sp.split("<decision_protocol>")[1]
    assert "重复凑话" in decision or "已回复过" in decision or "重复回答" in decision


def test_decision_protocol_uses_topic_hint_field_name():
    """字段名必须是 topic_hint(对齐 hermes_client _DECISION_HINT 与
    ActiveSessionManager.update_topic),不能是 topic_tag 等别名。"""
    sp = build_reactive_system_prompt()
    decision = sp.split("<decision_protocol>")[1]
    assert "topic_hint" in decision
    assert "topic_tag" not in decision


def test_decision_protocol_includes_addressee_check():
    """decision_protocol 必须包含「对象归属」段,在多 bot / 模糊指代场景下
    显式约束 should_reply,避免误答别人收到的批评/追问。"""
    sp = build_reactive_system_prompt()
    decision = sp.split("<decision_protocol>")[1]
    assert "对象归属" in decision
    assert "reply/quote" in decision
    assert "无锚代词" in decision
    assert "另一个 [bot]" in decision


def test_decision_protocol_at_bot_takes_precedence_over_at_others():
    """@ bot + @ 其他人共存时,@ 你必须压过 @ 了别人——否则 LLM 会把
    `@<bot_id> @<other_id>` 这种双 @ 误判为「不归你」直接 should_reply=false,
    导致用户 @ 群里所有相关方时 bot 集体噤声。两条规则必须在 prompt 里
    显式区分「同时 @」与「只 @ 了别人」。"""
    sp = build_reactive_system_prompt()
    decision = sp.split("<decision_protocol>")[1]
    # 正向规则: @ 你即使带其他 @ 也算归你
    assert "即使同时 @ 了别人" in decision
    # 负向规则: 必须强调"只 @ 了别人 + 没同时 @ 你"
    assert "没同时 @ 你" in decision or "且没 @ 你" in decision


def test_decision_protocol_binds_at_mention_to_own_account():
    """归属规则必须指向 runtime_state.you,否则 `@<陌生数字>` 会被判成 @ 别人。"""
    sp = build_reactive_system_prompt()
    assert "runtime_state.you" in sp
    assert "@<你的 id>" in sp


def test_decision_protocol_includes_self_attribution_check():
    """decision_protocol 必须包含「自我归因校验」,要求被评价时先核对
    recent_messages 里 [bot] 自己确实说过对应内容,才能认领。"""
    sp = build_reactive_system_prompt()
    decision = sp.split("<decision_protocol>")[1]
    assert "自我归因校验" in decision
    assert "禁止认错" in decision
    assert "[bot]" in decision


def test_reactive_user_content_includes_runtime_state_block():
    """runtime_state 从 system 搬过来后,所有字段必须出现在 user content 顶端。"""
    content = build_reactive_user_content(
        **_reactive_user_kwargs(
            triggered_by="u42",
            triggered_by_nickname="老张",
            topic_hint="Rust async runtime",
        )
    )
    assert isinstance(content, str)
    assert "<runtime_state>" in content
    assert "mode: reactive" in content
    assert "adapter: ob11" in content
    assert "group_id: g1" in content
    assert "triggered_by: u42 (老张)" in content
    assert "topic_hint: Rust async runtime" in content


def test_reactive_user_content_declares_own_account():
    """runtime_state 必须声明 bot 自己的账号 —— 消息里的 `@<self_id>` 只能靠它绑回「我」。"""
    content = build_reactive_user_content(**_reactive_user_kwargs(self_id="b9", self_nickname="Tenten"))
    runtime_block = content.split("</runtime_state>")[0]
    assert "you: [user=Tenten id=b9]" in runtime_block


def test_reactive_user_content_declares_addressing_when_known():
    """平台已确认被点名 → runtime_state 直接断言,不让模型从裸 `@<id>` 去猜归属。"""
    content = build_reactive_user_content(**_reactive_user_kwargs(addressed_to_bot=True))
    assert "addressed_to_you: true" in content.split("</runtime_state>")[0]


def test_reactive_user_content_omits_addressing_when_unknown():
    """归属未知时整行不出现 —— 缺行是「自己判」,渲染 false 会被读成「确定不是冲你说的」。"""
    content = build_reactive_user_content(**_reactive_user_kwargs(addressed_to_bot=False))
    assert "addressed_to_you" not in content


def test_decision_protocol_short_circuits_ownership_when_addressed():
    """协议必须承认 addressed_to_you,否则插件断言了也没人读。"""
    sp = build_reactive_system_prompt()
    assert "addressed_to_you: true" in sp
    assert "不确定是否针对你" in sp


def test_reactive_user_content_omits_you_line_without_self_id():
    """self_id 缺失(调用方拿不到)时不渲染半截的 you: 行。"""
    content = build_reactive_user_content(**_reactive_user_kwargs(self_id="", self_nickname=None))
    runtime_block = content.split("</runtime_state>")[0]
    assert "you:" not in runtime_block


def test_reactive_user_content_omits_topic_when_none():
    """topic_hint=None 时 runtime_state 段不出现 topic_hint 行。

    注:把检查范围限定在 runtime_state 段内部——decision_protocol 在 system,
    user content 这边查 'topic_hint:' 不会误命中。"""
    content = build_reactive_user_content(**_reactive_user_kwargs(topic_hint=None))
    assert isinstance(content, str)
    runtime_block = content.split("</runtime_state>")[0]
    assert "topic_hint:" not in runtime_block


def test_reactive_user_content_block_order():
    """user content 内部顺序:runtime_state → recent_messages → current_message。
    顺序不该乱,LLM 解析靠这个层次。"""
    content = build_reactive_user_content(**_reactive_user_kwargs(recent_messages=[_msg(100, "alice", "hi")]))
    assert isinstance(content, str)
    rt_idx = content.index("<runtime_state>")
    rm_idx = content.index("<recent_messages>")
    cm_idx = content.index("<current_message>")
    assert rt_idx < rm_idx < cm_idx


def test_user_content_text_only_when_no_images():
    msgs = [_msg(100, "alice", "hi"), _msg(200, "bob", "hello")]
    content = build_reactive_user_content(
        **_reactive_user_kwargs(
            recent_messages=msgs,
            current_user_id="charlie",
            current_nickname="Charlie",
            current_text="how is it going?",
        )
    )
    assert isinstance(content, str)
    assert "<recent_messages>" in content
    assert "[user=alice]: hi" in content
    assert "[user=bob]: hello" in content
    assert "<current_message>" in content
    assert "[user=Charlie id=charlie]: how is it going?" in content


def test_user_content_multimodal_when_images_present():
    msgs = [_msg(100, "alice", "hi", imgs=["http://x/a.png"])]
    content = build_reactive_user_content(
        **_reactive_user_kwargs(
            recent_messages=msgs,
            current_user_id="charlie",
            current_nickname="Charlie",
            current_text="see this",
            current_image_urls=["http://y/b.png"],
        )
    )
    assert isinstance(content, list)
    types = [p.get("type") for p in content]
    assert "text" in types
    assert "image_url" in types
    assert content[0]["type"] == "text"
    assert "<runtime_state>" in content[0]["text"]
    assert "<recent_messages>" in content[0]["text"]
    assert "<current_message>" in content[0]["text"]
    assert "[user=Charlie id=charlie]: see this" in content[0]["text"]
    last_img = next((p for p in reversed(content) if p.get("type") == "image_url"), None)
    assert last_img is not None
    assert last_img["image_url"]["url"] == "http://y/b.png"


def test_user_content_marks_bot_messages():
    msgs = [_msg(100, "alice", "hi"), _msg(200, "bot", "hi alice", is_bot=True)]
    content = build_reactive_user_content(
        **_reactive_user_kwargs(
            recent_messages=msgs,
            current_user_id="charlie",
            current_nickname="Charlie",
            current_text="?",
        )
    )
    assert isinstance(content, str)
    assert "[bot] [user=bot]: hi alice" in content


def test_user_content_empty_history_still_produces_block():
    """recent_messages=[] 时 <recent_messages> 块仍存在(空但格式齐全),
    保持 prompt 结构稳定,LLM 解析层次不被打破。"""
    content = build_reactive_user_content(
        **_reactive_user_kwargs(
            recent_messages=[],
            current_user_id="alice",
            current_nickname="Alice",
            current_text="hi",
        )
    )
    assert isinstance(content, str)
    assert "<recent_messages>" in content
    assert "</recent_messages>" in content
    assert "[user=Alice id=alice]: hi" in content


def test_user_content_empty_current_text_with_image_is_valid():
    """图片消息无文字描述(用户只发图)是常见场景,空 current_text + 图片应正确产出
    多模态 parts。"""
    content = build_reactive_user_content(
        **_reactive_user_kwargs(
            recent_messages=[],
            current_user_id="alice",
            current_nickname="Alice",
            current_text="",
            current_image_urls=["http://x/photo.jpg"],
        )
    )
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert "[user=Alice id=alice]:" in content[0]["text"]
    assert content[-1]["type"] == "image_url"
    assert content[-1]["image_url"]["url"] == "http://x/photo.jpg"


def test_reactive_user_content_prefixes_history_lines_with_msg_id():
    """每条 <recent_messages> 行有 [m:<id>] 前缀,current_message 没有。"""
    msgs = [
        _msg_with_id(300, "龙", "哈哈", msg_id=1235),
        _msg_with_id(200, "小丑鱼", "[图片]", msg_id=1234, imgs=["http://x/a.jpg"]),
    ]
    content = build_reactive_user_content(
        **_reactive_user_kwargs(
            recent_messages=msgs,
            current_user_id="ph",
            current_nickname="ph",
            current_text="评价下上图",
        )
    )
    assert isinstance(content, str)
    assert "[m:1234] [user=小丑鱼]: [图片]" in content
    assert "[m:1235] [user=龙]: 哈哈" in content
    current_block = content.split("<current_message>", 1)[1].split("</current_message>", 1)[0]
    assert "[m:" not in current_block
    assert "[user=ph]: 评价下上图" in current_block


def test_reactive_user_content_bot_messages_keep_bot_marker_and_get_msg_id():
    msgs = [_msg_with_id(100, "Bot", "收到", msg_id=99, is_bot=True)]
    content = build_reactive_user_content(
        **_reactive_user_kwargs(
            recent_messages=msgs,
            current_user_id="alice",
            current_nickname="Alice",
            current_text="hi",
        )
    )
    assert "[m:99] [bot] [user=Bot]: 收到" in content


def test_reactive_user_content_omits_prefix_when_id_is_none():
    """id=None(transient,从未入库)时不应在 prompt 里出现 [m:None]。"""
    msgs = [_msg(100, "alice", "hi")]
    content = build_reactive_user_content(
        **_reactive_user_kwargs(
            recent_messages=msgs,
            current_user_id="bob",
            current_nickname="Bob",
            current_text="hello",
        )
    )
    assert "[m:None]" not in content
    assert "[user=alice]: hi" in content


def test_reactive_user_content_history_carries_user_id_when_nickname_differs():
    """历史行带真实昵称时标签里要同时给出 id=,LLM 能按 id 匹配 SOUL.md 稳定身份。"""
    msgs = [
        BufferedMessage(
            ts=100,
            adapter="ob11",
            group_id="g1",
            user_id="u-test-1",
            nickname="肯尼",
            content="hi",
            image_urls=[],
            reply_to_ts=None,
            is_bot=False,
            id=42,
        )
    ]
    content = build_reactive_user_content(
        **_reactive_user_kwargs(
            recent_messages=msgs,
            current_user_id="u-test-1",
            current_nickname="肯尼",
            current_text="还在吗",
        )
    )
    assert isinstance(content, str)
    assert "[m:42] [user=肯尼 id=u-test-1]: hi" in content
    assert "[user=肯尼 id=u-test-1]: 还在吗" in content


# --- passive system prompt(纯 Message Context)---


def test_passive_system_prompt_only_message_context_for_group():
    """recent_messages 已搬到 user content,passive system 必须 byte-stable per session。"""
    sp = build_passive_system_prompt(
        adapter="ob11",
        is_private=False,
        user_id="charlie",
        group_id="g1",
    )
    assert "Platform: ob11" in sp
    assert "Chat Type: Group" in sp
    assert "User ID: charlie" in sp
    assert "Group ID: g1" in sp
    assert "<recent_messages>" not in sp


def test_passive_system_prompt_private_omits_group_id():
    """私聊场景调用方一般不会传 history,但若误传也别在 prompt 里漏出
    Group ID 字段——和 hermes_client 默认拼装行为对齐。"""
    sp = build_passive_system_prompt(
        adapter="ob11",
        is_private=True,
        user_id="charlie",
        group_id=None,
    )
    assert "Chat Type: Private" in sp
    assert "Group ID" not in sp


def test_passive_system_prompt_matches_hermes_client_default():
    """passive system 必须与 hermes_client.chat 的默认 Message Context 拼装字节一致——
    handler 统一走本函数,等价于 chat() 默认行为(便于把 user content override 接上)。"""
    # 重建一份 chat() 默认会生成的 Message Context(对齐 hermes_client.chat 实现)。
    expected = "Message Context:\nPlatform: ob11\nChat Type: Group\nUser ID: charlie\nGroup ID: g1"
    sp = build_passive_system_prompt(
        adapter="ob11",
        is_private=False,
        user_id="charlie",
        group_id="g1",
    )
    assert sp == expected


# --- passive user content ---


def test_passive_user_content_includes_history_block_when_recent_present():
    """群聊 + 有 buffer 历史:user content 头部追加 <recent_messages> 块,
    历史按旧→新顺序;bot 自己的回复带 [bot] 前缀。"""
    msgs = [_msg(200, "bob", "hi all"), _msg(100, "alice", "hello")]  # 新→旧 入参
    content = build_passive_user_content(
        recent_messages=msgs,
        current_text="who's there?",
        current_image_urls=[],
    )
    assert isinstance(content, str)
    assert "<recent_messages>" in content
    assert "</recent_messages>" in content
    # 旧→新顺序
    alice_idx = content.index("[user=alice]: hello")
    bob_idx = content.index("[user=bob]: hi all")
    assert alice_idx < bob_idx
    assert "who's there?" in content


def test_passive_user_content_marks_bot_messages():
    msgs = [_msg(100, "alice", "hi"), _msg(200, "bot", "hi alice", is_bot=True)]
    content = build_passive_user_content(
        recent_messages=msgs,
        current_text="?",
        current_image_urls=[],
    )
    assert isinstance(content, str)
    assert "[bot] [user=bot]: hi alice" in content


def test_passive_user_content_empty_history_returns_plain_text():
    """recent_messages=[] 时退化为纯当前文本,与 chat() 默认拼装字节一致——
    意味着 handler 总是走 user_content_override 也不会有行为差。"""
    content = build_passive_user_content(
        recent_messages=[],
        current_text="hello",
        current_image_urls=[],
    )
    assert content == "hello"


def test_passive_user_content_empty_history_with_images_returns_multimodal_parts():
    content = build_passive_user_content(
        recent_messages=[],
        current_text="see this",
        current_image_urls=["http://x/a.jpg"],
    )
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "see this"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == "http://x/a.jpg"


def test_passive_user_content_history_with_images_combines_both():
    msgs = [_msg(100, "alice", "hi")]
    content = build_passive_user_content(
        recent_messages=msgs,
        current_text="look",
        current_image_urls=["http://x/a.jpg"],
    )
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    text = content[0]["text"]
    assert "<recent_messages>" in text
    assert "[user=alice]: hi" in text
    assert "look" in text
    assert content[-1]["type"] == "image_url"


def test_passive_user_content_prefixes_history_lines_with_msg_id():
    msgs = [_msg_with_id(100, "A", "hi", msg_id=42)]
    content = build_passive_user_content(
        recent_messages=msgs,
        current_text="?",
        current_image_urls=[],
    )
    assert isinstance(content, str)
    assert "[m:42] [user=A]: hi" in content


def test_passive_user_content_history_carries_user_id_when_nickname_differs():
    msgs = [
        BufferedMessage(
            ts=100,
            adapter="ob11",
            group_id="g1",
            user_id="u-test-1",
            nickname="肯尼",
            content="hi",
            image_urls=[],
            reply_to_ts=None,
            is_bot=False,
            id=42,
        )
    ]
    content = build_passive_user_content(
        recent_messages=msgs,
        current_text="?",
        current_image_urls=[],
    )
    assert isinstance(content, str)
    assert "[m:42] [user=肯尼 id=u-test-1]: hi" in content


def test_passive_user_content_omits_prefix_when_id_is_none():
    msgs = [_msg(100, "alice", "hi")]
    content = build_passive_user_content(
        recent_messages=msgs,
        current_text="?",
        current_image_urls=[],
    )
    assert isinstance(content, str)
    assert "[m:None]" not in content
    assert "[user=alice]: hi" in content


def test_history_line_strips_data_urls_and_caps_length():
    """渲染端是最后一道闸:DB 里存了什么都不能让单行无上限地进 prompt。

    写入端修好只保护新行 —— 修之前落库的 bot 回复(内联 data URL,一条几十万字符)
    会在之后每个 turn 被逐字重放,直到 retention 淘汰。base64 对模型零信息量,
    却能把真实上下文整段挤出窗口。
    """
    poisoned = "画好啦! ![image](data:image/png;base64," + "A" * 500_000 + ")"
    content = build_reactive_user_content(
        **_reactive_user_kwargs(recent_messages=[_msg_with_id(100, "bot", poisoned, 41, is_bot=True)])
    )
    assert isinstance(content, str)
    history = content.split("<recent_messages>", 1)[1].split("</recent_messages>", 1)[0]
    assert "data:image" not in history
    assert "[图片]" in history
    assert "画好啦!" in history, "正文要留住,只摘掉字节"
    assert len(history) < 2000, f"单行没被封顶,渲染出 {len(history)} 字符"


def test_history_line_under_cap_is_untouched():
    """正常长度的历史行不能被动:多数「归你/自我归因」判断都靠原文匹配。"""
    normal = "刚那题我算的是 O(n log n),你说的方案在最坏情况会退化"
    content = build_reactive_user_content(
        **_reactive_user_kwargs(recent_messages=[_msg_with_id(100, "bot", normal, 7, is_bot=True)])
    )
    assert f"[m:7] [bot] [user=bot]: {normal}" in content


def test_history_sanitize_drops_markdown_shell_and_keeps_spacing():
    """占位要连 `![alt](...)` 外壳一起换,别留 `![image]([图片])`;
    payload 后面那个空格也要还回来(字符类含 \\s,base64 可能折行)。"""
    b64 = "A" * 400
    for raw, want in [
        (f"画好啦! ![image](data:image/png;base64,{b64}) 喜欢吗?", "画好啦! [图片] 喜欢吗?"),
        (f"截断的 ![image](data:image/png;base64,{b64}", "截断的 [图片]"),
        (f"裸 MEDIA:data:image/png;base64,{b64} 完", "裸 MEDIA:[图片] 完"),
    ]:
        content = build_reactive_user_content(
            **_reactive_user_kwargs(recent_messages=[_msg_with_id(100, "bot", raw, 7, is_bot=True)])
        )
        assert f"[m:7] [bot] [user=bot]: {want}" in content, f"{raw[:30]}… 渲染成了别的形状"


# ---------------------------------------------------------------------------
# 折叠消息全文渲染展开(_render_recent_messages_block)
# ---------------------------------------------------------------------------


def _fbm(ts, content, forward_content=None, msg_id=None):
    return BufferedMessage(
        ts=ts,
        adapter="ob11",
        group_id="g1",
        user_id="u1",
        nickname="u1",
        content=content,
        forward_content=forward_content,
        id=msg_id,
    )


def test_render_expands_only_newest_forward():
    from nonebot_plugin_hermes.core.prompt_builder import _render_recent_messages_block

    full_old = '<forwarded_messages count="2">\nA: 第一条正文\nB: 第二条正文\n</forwarded_messages>'
    full_new = '<forwarded_messages count="1">\nC: 第三条正文\n</forwarded_messages>'
    msgs = [  # 新→旧
        _fbm(200, 'x <forwarded_messages count="1" preview="C: 第三…"/>', forward_content=full_new, msg_id=2),
        _fbm(100, 'y <forwarded_messages count="2" preview="A: 第一…"/>', forward_content=full_old, msg_id=1),
    ]
    out = _render_recent_messages_block(msgs)
    assert "C: 第三条正文" in out
    assert 'preview="C' not in out
    assert "A: 第一条正文" not in out
    assert 'preview="A' in out


def test_render_expanded_line_exempt_from_default_cap():
    from nonebot_plugin_hermes.core.prompt_builder import (
        _HISTORY_TRUNCATION_MARK,
        _render_recent_messages_block,
    )

    body = "\n".join(f"U{i}: {'甲' * 70}" for i in range(10))
    full = f'<forwarded_messages count="10">\n{body}\n</forwarded_messages>'
    assert len(full) > 800
    msgs = [_fbm(100, '<forwarded_messages count="10" preview="U0…"/>', forward_content=full, msg_id=1)]
    out = _render_recent_messages_block(msgs)
    assert _HISTORY_TRUNCATION_MARK not in out
    assert "</forwarded_messages>" in out


def test_render_expanded_line_still_scrubs_data_urls():
    from nonebot_plugin_hermes.core.prompt_builder import _render_recent_messages_block

    full = '<forwarded_messages count="1">\nA: ![img](data:image/png;base64,AAAA)\n</forwarded_messages>'
    msgs = [_fbm(100, '<forwarded_messages count="1" preview="A…"/>', forward_content=full, msg_id=1)]
    out = _render_recent_messages_block(msgs)
    assert "base64" not in out
    assert "[图片]" in out


def test_render_expansion_appends_when_no_preview_tag():
    from nonebot_plugin_hermes.core.prompt_builder import _render_recent_messages_block

    full = '<forwarded_messages count="1">\nA: hi\n</forwarded_messages>'
    msgs = [_fbm(100, "plain text only", forward_content=full, msg_id=1)]
    out = _render_recent_messages_block(msgs)
    assert "plain text only\n" in out
    assert "A: hi" in out


def test_render_without_forward_content_keeps_preview():
    from nonebot_plugin_hermes.core.prompt_builder import _render_recent_messages_block

    msgs = [_fbm(100, 'x <forwarded_messages count="2" preview="A / B"/>', msg_id=1)]
    out = _render_recent_messages_block(msgs)
    assert 'preview="A / B"' in out
