"""P0-spike: 验证 Hermes 是否透传 tools+tool_choice 给底层 LLM。

运行:
    HERMES_API_URL=http://127.0.0.1:8642 \\
    HERMES_API_KEY=your-key \\
    uv run python scripts/spike_tools_passthrough.py

判定:
    - 输出包含 'TOOL_CALL_OK' → 路径 A (tools+tool_choice) 可用
    - 输出包含 'TOOL_CALL_FAIL' → 路径 B (prompt+JSON5) 兜底
"""

from __future__ import annotations

import json
import os
import sys

import httpx

API_URL = os.environ.get("HERMES_API_URL", "http://127.0.0.1:8642").rstrip("/")
API_KEY = os.environ.get("HERMES_API_KEY", "")

DECISION_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_decision",
        "description": "Submit your decision about whether and how to reply.",
        "parameters": {
            "type": "object",
            "properties": {
                "should_reply": {"type": "boolean"},
                "reply_text": {"type": "string"},
                "topic_hint": {"type": "string"},
                "should_exit_active": {"type": "boolean"},
            },
            "required": ["should_reply"],
        },
    },
}


def main() -> int:
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    headers["X-Hermes-Session-Id"] = "spike-tools-passthrough"

    payload = {
        "model": "hermes-agent",
        "messages": [
            {
                "role": "system",
                "content": ("You must call the submit_decision tool to answer. Do not output free text."),
            },
            {
                "role": "user",
                "content": "Pretend a user said hi. Decide should_reply=true and reply_text='hello'.",
            },
        ],
        "tools": [DECISION_TOOL],
        "tool_choice": {"type": "function", "function": {"name": "submit_decision"}},
        "stream": False,
    }

    resp = httpx.post(f"{API_URL}/v1/chat/completions", json=payload, headers=headers, timeout=120)
    print(f"HTTP {resp.status_code}")
    body = resp.json()
    print(json.dumps(body, indent=2, ensure_ascii=False)[:2000])

    choice = (body.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    tool_calls = msg.get("tool_calls") or []

    if tool_calls and tool_calls[0].get("function", {}).get("name") == "submit_decision":
        try:
            args = json.loads(tool_calls[0]["function"]["arguments"])
            assert "should_reply" in args
            print("TOOL_CALL_OK")
            return 0
        except Exception as exc:
            print(f"TOOL_CALL_FAIL: arguments not valid JSON / missing field: {exc}")
            return 2

    print("TOOL_CALL_FAIL: no tool_calls in choices[0].message")
    return 1


if __name__ == "__main__":
    sys.exit(main())
