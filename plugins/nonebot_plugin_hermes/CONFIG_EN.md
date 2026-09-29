# Configuration Reference (CONFIG_EN.md)

This document provides a comprehensive reference for all optional configuration parameters and advanced tuning knobs in `nonebot-plugin-hermes`. Extracted from [README_EN.md](README_EN.md) into its own page.

All settings are configured via the `.env` file (or `.env.prod` / `.env.dev`) at the root of your NoneBot project, all prefixed with `HERMES_`. See annotated examples in [.env.example](.env.example).

> [!TIP]
> Just want to get started? Check "Quick Start - 4. Configuration" in [README_EN.md](README_EN.md#4-configuration): usually only your adapter connection, `HERMES_API_URL`, and `HERMES_API_KEY` are required. All other parameters listed here have sensible defaults and only need tuning as needed.

---

## Table of Contents

- [1. Basic Connection & Request Settings](#1-basic-connection--request-settings)
- [2. Message Triggers & Access Control](#2-message-triggers--access-control)
- [3. Session & Reply Behavior](#3-session--reply-behavior)
- [4. Passive Perception & Context Buffering](#4-passive-perception--context-buffering)
- [5. Active Group Sessions (M1 Reactive)](#5-active-group-sessions-m1-reactive)
- [6. Visual Feedback & Error Fallbacks](#6-visual-feedback--error-fallbacks)
- [7. Reverse FastMCP Channel](#7-reverse-fastmcp-channel)
- [8. Persistent Storage & Image Cache](#8-persistent-storage--image-cache)
- [9. Image Inlining (0.5.3+)](#9-image-inlining-053)
- [10. Long-term Memory Scope (0.5.0+)](#10-long-term-memory-scope-050)
- [11. Per-Group Endpoint Routing (0.5.1+)](#11-per-group-endpoint-routing-051)
- [Topic: Image Inlining Mechanism](#topic-image-inlining-mechanism)
- [Topic: Feedback Emoji and Busy Notice](#topic-feedback-emoji-and-busy-notice)

---

## 1. Basic Connection & Request Settings

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `HERMES_API_URL` | str | `http://127.0.0.1:8642` | Hermes API Server URL (corresponding to the `api_server` port in `hermes gateway`). Set to the actual IP/port for multi-host or container deployments |
| `HERMES_API_KEY` | str | `""` | Hermes API authentication key. **Strongly recommended**: must match `API_SERVER_KEY` in `~/.hermes/.env` on the Hermes host. Without this, upstream rejects session continuation, breaking context across turns |
| `HERMES_API_TIMEOUT` | int | `300` | API request timeout in seconds. Agents performing complex multi-step tool calls (code execution, web browsing) can take time; keep this generous |

---

## 2. Message Triggers & Access Control

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `HERMES_GROUP_TRIGGER` | str | `at` | Group chat trigger mode: `at` (@bot only), `all` (all group messages), `keyword` (matches specific keywords) |
| `HERMES_KEYWORDS` | list[str] | `["/ai"]` | Trigger keywords for `keyword` mode, formatted as a JSON array. E.g.: `["/ai", "bot"]` |
| `HERMES_PRIVATE_TRIGGER` | str | `all` | Direct message trigger mode: `all` (responds to all private messages), `allowlist` (only responds to allowed users) |
| `HERMES_ALLOW_USERS` | list[str] | `[]` | Allowed user IDs for private chat (`allowlist` mode), as a JSON array. E.g.: `["12345678"]` |
| `HERMES_ALLOW_GROUPS` | list[str] | `[]` | Allowed group IDs, as a JSON array. Empty list means all groups are allowed. E.g.: `["987654321"]` |
| `HERMES_ADMIN_USERS` | list[str] | `[]` | Admin allowlist in the format `["<adapter>:<user_id>"]` (lowercase adapter name without dots/spaces, e.g. `telegram`, `onebotv11`). **Default empty = deny by default**; sensitive commands such as `/hermes-status` require matching this list |
| `HERMES_IGNORE_PREFIX` | list[str] | `["."]` | Messages starting with these characters will not trigger a reply (prevents conflicts with other command bots) |

---

## 3. Session & Reply Behavior

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `HERMES_SESSION_SHARE_GROUP` | bool | `false` | Share session within group chat: `false` = each member maintains an isolated conversation; `true` = all group members share a single conversation context |
| `HERMES_MAX_LENGTH` | int | `4000` | Maximum text length for a single reply (truncated with a note if exceeded; QQ platform limit is ~4500 chars) |
| `HERMES_LONG_REPLY_FORWARD` | bool | `true` | In OneBot v11 group chats, whether to send long replies as forward nodes to avoid truncation. Set `false` to revert to single-message truncation. DMs and other adapters are unaffected |
| `HERMES_FORWARD_EXTRACT_MAX_NODES` | int | `10` | Maximum number of nodes to extract when a user sends a forwarded chat record. Excess nodes are noted as `...and N more omitted` to avoid prompt bloating |
| `HERMES_FORWARD_EXTRACT_MAX_CHARS` | int | `800` | Maximum character cap across all extracted forward nodes (secondary safety gate). Excess text is truncated with a notice |

---

## 4. Passive Perception & Context Buffering

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `HERMES_PERCEPTION_ENABLED` | bool | `false` | In group chats with active sessions disabled (`active_session=false`), whether to inject recent bystander history into the prompt when @bot is triggered. **Auto-implied when `HERMES_ACTIVE_SESSION_ENABLED=true` (this switch becomes a no-op)**. Never injected in private chats |
| `HERMES_PERCEPTION_BUFFER` | int | `10` | Number of messages buffered for passive perception |
| `HERMES_PERCEPTION_TEXT_LENGTH` | int | `200` | Maximum text length per historical message before truncation |
| `HERMES_PERCEPTION_IMAGE_MODE` | str | `placeholder` | ⚠️ **Deprecated since 0.3**. Historical image recall is handled via the `get_message_images` MCP tool. This switch only controls whether a `[图片]` placeholder appears in history text (`none` = no placeholder; other values = show placeholder) |
| `HERMES_BUFFER_PER_GROUP_CAP` | int | `200` | ⚠️ **No-op since 0.3**. Message buffering is backed by SQLite; no in-memory per-group cap applies. Will be removed in the next major release |
| `HERMES_BUFFER_TOTAL_GROUPS_CAP` | int | `50` | ⚠️ **No-op since 0.3**. Same as above, governed by retention days and row limits in SQLite |

---

## 5. Active Group Sessions (M1 Reactive)

For architecture details, refer to the "Active Sessions + Reverse Channel" section in [README_EN.md](README_EN.md).

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `HERMES_ACTIVE_SESSION_ENABLED` | bool | `false` | Enable active group sessions (M1). When enabled, @bot puts the group into an active window during which Hermes evaluates incoming messages via structured output (`should_reply`) without requiring further @mentions. `false` reverts to classic 1:1 passive Q&A |
| `HERMES_ACTIVE_SESSION_TTL_SEC` | int | `300` | Active-window TTL lease in seconds; sliding renewal on each reply |
| `HERMES_ACTIVE_SWEEP_INTERVAL_SEC` | int | `30` | Cron sweep interval in seconds for cleaning expired active sessions |
| `HERMES_REACTIVE_FOLLOWUP_WINDOW` | int | `4` | In reactive follow-up turns, only send the tail N messages of `<recent_messages>` (plus the bot's own latest reply). Set to `0` to disable trimming and send full history |
| `HERMES_REACTIVE_POST_REPLY_COOLDOWN_SEC` | int | `8` | In reactive mode, suppress non-explicit @ messages for N seconds after the bot replies, dampening over-triggering cascades. Set to `0` to disable cooldown. Explicit @ mentions are never suppressed |
| `HERMES_POKE_TRIGGER_ENABLED` | bool | `false` | OneBot v11: being poked in private or group chats triggers a turn, equivalent to being @-mentioned |
| `HERMES_GREET_ON_JOIN` | bool | `false` | OneBot v11: when a new member joins and active sessions are enabled, fire a reactive turn so Hermes can decide whether to greet them (`noop` is valid) |

---

## 6. Visual Feedback & Error Fallbacks

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `HERMES_ACK_FEEDBACK_ENABLED` | bool | `false` | Show a visual "received" receipt on explicit triggers (@bot or DM). Implemented on OneBot v11 group chats by attaching an emoji face, which is revoked when the reply finishes |
| `HERMES_ACK_EMOJI_ID` | int | `341` | QQ emoji face ID for ack feedback on OneBot v11. Default `341` (/打招呼 hi-wave); recommended alternatives: `373` (/忙 typing animal), `129` (/挥手 classic wave) |
| `HERMES_BUSY_EMOJI_ID` | int | `97` | When too many explicit mentions arrive in a short window and hit depth limit (`MAX_REFIRE_DEPTH=3`), this emoji face is attached to the dropped message. Default `97` (/擦汗 wiping sweat), **never revoked**, acting as an overload notice |
| `HERMES_TRANSPORT_ERROR_FALLBACK_TEXT` | str | `"嗯…我这边遇到点状况,稍后再问一次"` | Friendly fallback text sent when Hermes returns 5xx or connection drops. Set to `""` to stay silent |

---

## 7. Reverse FastMCP Channel

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `HERMES_MCP_ENABLED` | bool | `false` | Start embedded FastMCP server. Exposes tools: `push_message`, `list_active_sessions`, `get_recent_messages`, `get_message_images` |
| `HERMES_MCP_HOST` | str | `127.0.0.1` | FastMCP server bind host. **Security warning**: because the reverse channel can push messages into groups, do NOT expose this to public networks without reverse proxy, TLS, and ACL |
| `HERMES_MCP_PORT` | int | `8643` | FastMCP server bind port |
| `HERMES_MCP_RECENT_LIMIT_MAX` | int | `50` | Maximum items `get_recent_messages` is permitted to return per call |

---

## 8. Persistent Storage & Image Cache

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `HERMES_STORAGE_DB_PATH` | str | `""` | SQLite message log path. Empty string defaults to `nonebot-plugin-localstore`'s plugin_data_dir (typically `~/.local/share/nonebot2/nonebot_plugin_hermes/messages.db`) |
| `HERMES_STORAGE_MESSAGE_RETENTION_DAYS` | int | `30` | Retention days for message logs; vacuum cron removes expired records |
| `HERMES_STORAGE_MESSAGE_MAX_ROWS` | int | `100000` | Hard cap on total message log rows; vacuum cron removes oldest rows by timestamp when exceeded |
| `HERMES_IMAGE_CACHE_DIR` | str | `""` | Image byte cache directory. Empty string defaults to localstore cache dir (`~/.cache/nonebot2/nonebot_plugin_hermes/images/`) |
| `HERMES_IMAGE_CACHE_QUOTA_MB` | int | `200` | Maximum cache size in MB; LRU eviction by access time during vacuum |
| `HERMES_IMAGE_FETCH_TIMEOUT_S` | int | `10` | HTTP fetch timeout per image in seconds |
| `HERMES_IMAGE_FETCH_MAX_ATTEMPTS` | int | `2` | Total attempts per image fetch (1 = no retry, 2 = one retry) |

---

## 9. Image Inlining (0.5.3+)

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `HERMES_IMAGE_INLINE_ENABLED` | bool | `true` | Whether outbound turn images are fetched, downscaled, and encoded as `data:image/jpeg;base64,...` in the request body (see topic below) |
| `HERMES_IMAGE_INLINE_MAX_EDGE` | int | `1280` | Longest-edge pixel target for downscaling before inlining; only shrinks, never upscales |
| `HERMES_IMAGE_INLINE_QUALITY` | int | `82` | Initial JPEG quality used when re-encoding |
| `HERMES_IMAGE_INLINE_MAX_BYTES` | int | `2000000` | Per-image byte cap after re-encoding (2MB). Images exceeding this after the quality ladder are dropped |
| `HERMES_IMAGE_INLINE_TOTAL_MAX_BYTES` | int | `4000000` | Total byte budget for inlined images in a single turn (4MB). Images beyond budget are dropped individually |

---

## 10. Long-term Memory Scope (0.5.0+)

For architectural details, refer to the "Long-term Memory Scope" section in [README_EN.md](README_EN.md) and [`honcho/`](honcho/).

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `HERMES_HONCHO_ENABLED` | bool | `false` | **⚠️ Not recommended yet**. Send `X-Hermes-Session-Key` header to scope long-term memory per group/DM. Requires memory provider upstream (Honcho) and `HERMES_API_KEY` configured |
| `HERMES_GROUP_SESSIONS_PER_USER` | bool | `false` | Group memory granularity: `false` = shared memory for the group; `true` = isolated memory per member |
| `HERMES_GROUP_SESSION_KEY_FORMAT` | str | `agent:main:nonebot-{adapter}:group:{group_id}` | Key template for group-shared memory |
| `HERMES_GROUP_PER_USER_SESSION_KEY_FORMAT` | str | `agent:main:nonebot-{adapter}:group:{group_id}:{user_id}` | Key template for per-member group memory |
| `HERMES_PRIVATE_SESSION_KEY_FORMAT` | str | `agent:main:nonebot-{adapter}:dm:{user_id}` | Key template for direct message memory |

---

## 11. Per-Group Endpoint Routing (0.5.1+)

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `HERMES_GROUP_ENDPOINTS` | dict | `{}` | Per-group routing dictionary keyed `{adapter}:{group_id}` with `{url, key, timeout}` objects. **Full documentation, security boundaries, and Hermes setup are detailed in [PROFILES_EN.md](PROFILES_EN.md)** |

---

## Topic: Image Inlining Mechanism

Prior to version 0.5.3, the plugin sent raw platform image URLs directly to Hermes, leaving the upstream LLM provider to fetch them. This caused several severe issues:
1. **Reachability & Expiry**: Platform URLs often carry time-limited auth tokens, anti-hotlinking restrictions, or are located within intranet/regional CDNs unreachable by the LLM provider. Any failure causes a 400 error or silent omission.
2. **Provider Inconsistencies**: Anthropic/OpenAI fail with explicit errors; Gemini native silently drops non-`data:` image parts; Bedrock degrades to placeholder text. All result in "the model cannot see the picture without informing the user".
3. **Credential Leaks**: Ephemeral access tokens embedded in URLs leak to third-party providers.

**Inlining Guarantees**:
- The plugin fetches images locally before dispatch, downscales proportionally via Pillow (longest edge ≤ `HERMES_IMAGE_INLINE_MAX_EDGE`), re-encodes as JPEG, and strips all EXIF metadata (GPS, camera details).
- **Graceful degradation**: If an image fails to fetch/decode or exceeds the turn budget, **only that specific image is dropped**; the turn continues rather than failing entirely.
- Set `HERMES_IMAGE_INLINE_ENABLED=false` to temporarily revert to sending raw URLs.

---

## Topic: Feedback Emoji and Busy Notice

On OneBot v11 group chats, two distinct visual signals provide feedback:

| Signal Type | Controlled By | Default Emoji | Lifecycle | Semantics |
|-------------|---------------|---------------|-----------|-----------|
| **Ack Feedback** (In Progress) | `HERMES_ACK_FEEDBACK_ENABLED` | `341` (/打招呼 hi-wave) | Attached upon explicit mention, **cleared** once reply completes or errors | "Message received, currently thinking/executing" |
| **Busy Notice** (Overload Dropped) | `HERMES_BUSY_EMOJI_ID` | `97` (/擦汗 wiping sweat) | Attached when queue depth hits limit (`MAX_REFIRE_DEPTH=3`), **never cleared** | "Queue overflowed under burst mentions; this request was dropped" |

> [!NOTE]
> - Busy Notice is independent of Ack Feedback: even if `HERMES_ACK_FEEDBACK_ENABLED` is `false`, Busy Notice will still appear when requests are dropped so users understand what happened.
> - Adapters without native emoji reaction APIs (Telegram, Discord, etc.) degrade to a WARN log to avoid polluting chat with noisy fallback text.
