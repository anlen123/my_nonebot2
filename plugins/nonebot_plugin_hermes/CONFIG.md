# 完整配置项文档 (Configuration Reference)

本文档整理 `nonebot-plugin-hermes` 的所有可选配置参数与高级调优选项。本文从 [README.md](README.md) 抽出，单独成篇。

所有配置项均在 NoneBot 项目根目录的 `.env`（或 `.env.prod` / `.env.dev`）文件中设置，前缀均为 `HERMES_`。示例注释参见 [.env.example](.env.example)。

> [!TIP]
> 仅需快速启动？请查看 [README.md](README.md#4-配置) 中的「快速开始 - 4. 配置」：通常只需配置平台适配器、`HERMES_API_URL` 与 `HERMES_API_KEY` 即可正常运行。本文档列出的其余所有参数均有合理缺省值，按需调整即可。

---

## 目录

- [1. 基础连接与请求设置](#1-基础连接与请求设置)
- [2. 消息触发与权限控制](#2-消息触发与权限控制)
- [3. 会话模式与回复行为](#3-会话模式与回复行为)
- [4. 被动感知与上下文缓冲](#4-被动感知与上下文缓冲)
- [5. 群活跃态 (M1 Reactive)](#5-群活跃态-m1-reactive)
- [6. 视觉反馈与错误兜底](#6-视觉反馈与错误兜底)
- [7. 反向 MCP 通道 (FastMCP)](#7-反向-mcp-通道-fastmcp)
- [8. 持久化存储与图片缓存](#8-持久化存储与图片缓存)
- [9. 图片内联处理 (0.5.3+)](#9-图片内联处理-053)
- [10. 长期记忆作用域 (0.5.0+)](#10-长期记忆作用域-050)
- [11. 按群路由多接入点 (0.5.1+)](#11-按群路由多接入点-051)
- [专题说明：图片内联机制](#专题说明图片内联机制)
- [专题说明：反馈 Emoji 与 Busy Notice](#专题说明反馈-emoji-与-busy-notice)

---

## 1. 基础连接与请求设置

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `HERMES_API_URL` | str | `http://127.0.0.1:8642` | Hermes API Server 接入点地址（对应 `hermes gateway` 的 `api_server` 端口）。跨机部署或容器部署时需指向实际 IP/端口 |
| `HERMES_API_KEY` | str | `""` | Hermes API 鉴权密钥。**强烈建议配置**：必须与 Hermes 宿主机 `~/.hermes/.env` 中的 `API_SERVER_KEY` 一致。若不设置，上游将拒绝会话续接，无法维持多轮对话上下文 |
| `HERMES_API_TIMEOUT` | int | `300` | API 请求超时时间（秒）。由于 Agent 可能执行复杂的工具调用（如跑代码、多次网页搜索），执行耗时较长，建议保持较大超时 |

---

## 2. 消息触发与权限控制

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `HERMES_GROUP_TRIGGER` | str | `at` | 群聊触发方式：`at`（仅 @bot 触发）、`all`（响应群内所有消息）、`keyword`（匹配指定关键词） |
| `HERMES_KEYWORDS` | list[str] | `["/ai"]` | `keyword` 触发模式下的关键词列表，JSON 数组格式。例：`["/ai", "小助手"]` |
| `HERMES_PRIVATE_TRIGGER` | str | `all` | 私聊触发方式：`all`（响应所有私聊用户）、`allowlist`（仅响应白名单内用户） |
| `HERMES_ALLOW_USERS` | list[str] | `[]` | 允许私聊的用户 ID 列表（`allowlist` 模式生效），JSON 数组格式。例：`["12345678"]` |
| `HERMES_ALLOW_GROUPS` | list[str] | `[]` | 允许响应的群组 ID 列表，空列表表示允许所有群组。例：`["987654321"]` |
| `HERMES_ADMIN_USERS` | list[str] | `[]` | 管理员白名单。格式为 `["<adapter>:<user_id>"]`，adapter 为小写无空格点（如 `telegram`, `onebotv11`, `discord` 等）。**默认空集 = deny by default**；敏感管理命令（如 `/hermes-status`）必须命中该列表才被允许执行 |
| `HERMES_IGNORE_PREFIX` | list[str] | `["."]` | 以这些字符开头的消息不触发回复（通常用于避免与其他 Bot 的指令前缀冲突） |

---

## 3. 会话模式与回复行为

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `HERMES_SESSION_SHARE_GROUP` | bool | `false` | 群聊内是否共享同一个会话：`false` 为每人独立维护对话历史；`true` 为群内所有成员共享同一段会话上下文 |
| `HERMES_MAX_LENGTH` | int | `4000` | 单条回复最大文本长度（超出后截断并添加截断提示，QQ 单条上限约 4500 字符） |
| `HERMES_LONG_REPLY_FORWARD` | bool | `true` | 超长回复在群聊（OneBot v11）下是否使用合并转发发送完整内容。设为 `false` 则退回单条截断。私聊及其他平台不受此开关影响 |
| `HERMES_FORWARD_EXTRACT_MAX_NODES` | int | `10` | 用户发送合并转发消息时，最多展开的节点数量。超出部分标注 `...另有 N 条已省略`，防止 system prompt 历史过大 |
| `HERMES_FORWARD_EXTRACT_MAX_CHARS` | int | `800` | 合并转发展开文本的总字符上限（第二道安全闸），超出按字符截断并标注 `...因字符上限截断` |

---

## 4. 被动感知与上下文缓冲

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `HERMES_PERCEPTION_ENABLED` | bool | `false` | 在群聊且未启用活跃态（`active_session=false`）时，是否在 @bot 时向 LLM 注入近期的旁观聊天记录（他人此前的群发言）。**若 `HERMES_ACTIVE_SESSION_ENABLED=true`，此功能自动隐含开启，本开关无效**。私聊永不注入 |
| `HERMES_PERCEPTION_BUFFER` | int | `10` | 被动感知缓存的历史消息数量 |
| `HERMES_PERCEPTION_TEXT_LENGTH` | int | `200` | 被动感知单条历史消息最大截断长度 |
| `HERMES_PERCEPTION_IMAGE_MODE` | str | `placeholder` | ⚠️ **0.3 起弃用**。历史图召回已统一改走 `get_message_images` MCP 工具。本字段当前仅控制历史文本中是否显示 `[图片]` 占位符（`none` 为不加；其他值均为加占位） |
| `HERMES_BUFFER_PER_GROUP_CAP` | int | `200` | ⚠️ **0.3 起空转**。消息缓冲已全量持久化至 SQLite 后端，无内存上限限制。下一主要版本将移除 |
| `HERMES_BUFFER_TOTAL_GROUPS_CAP` | int | `50` | ⚠️ **0.3 起空转**。同上，由 SQLite 的保留天数与行数上限控制 |

---

## 5. 群活跃态 (M1 Reactive)

详细机制说明请参考 [README.md](README.md)「群活跃态 + 反向通道」章节。

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `HERMES_ACTIVE_SESSION_ENABLED` | bool | `false` | 是否开启群活跃态。开启后，bot 在被 @ 后进入活跃窗口，窗口期内群消息无需再 @ 即可由 Hermes 结构化决策自决是否插话。设为 `false` 退化为经典 1:1 被动问答 |
| `HERMES_ACTIVE_SESSION_TTL_SEC` | int | `300` | 活跃窗口 TTL 租约时长（秒）。群内每次产生回复时滑动续期 |
| `HERMES_ACTIVE_SWEEP_INTERVAL_SEC` | int | `30` | 活跃态过期检查 cron 定时任务频率（秒） |
| `HERMES_REACTIVE_FOLLOWUP_WINDOW` | int | `4` | reactive 自决插话轮次中，仅发送 `<recent_messages>` 尾部 N 条消息（另加 bot 自身最近一条发言）。设为 `0` 关闭裁剪发送全量历史 |
| `HERMES_REACTIVE_POST_REPLY_COOLDOWN_SEC` | int | `8` | reactive 模式下，bot 刚回复后 N 秒内，非显式 @ 的新消息直接静默，阻断过触发。设为 `0` 关闭冷却。显式 @ 永远不受冷却影响 |
| `HERMES_POKE_TRIGGER_ENABLED` | bool | `false` | OneBot v11：被群成员或私聊戳一戳（poke）时触发对话，等价于被 @bot |
| `HERMES_GREET_ON_JOIN` | bool | `false` | OneBot v11：有新成员入群且活跃态开启时，触发一次 reactive turn 让 Hermes 自行决定是否迎新（noop 为合法动作） |

---

## 6. 视觉反馈与错误兜底

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `HERMES_ACK_FEEDBACK_ENABLED` | bool | `false` | 显式触发（@bot 或私聊）时是否贴上"已收到"的视觉反馈。目前在 OneBot v11 群聊通过贴表情实现，回复完成后撤销 |
| `HERMES_ACK_EMOJI_ID` | int | `341` | OneBot v11 路径贴的 QQ 表情 ID。默认 `341`（/打招呼）；其他推荐：`373`（/忙，打字小动物）、`129`（/挥手，经典小表情） |
| `HERMES_BUSY_EMOJI_ID` | int | `97` | 当群内短时间内 @ 消息过多触发深度熔断（`depth-cap`）丢单时，贴在原消息上的表情 ID。默认 `97`（/擦汗），**贴上后不撤销**，作为忙不过来的可见信号 |
| `HERMES_TRANSPORT_ERROR_FALLBACK_TEXT` | str | `"嗯…我这边遇到点状况,稍后再问一次"` | Hermes 上游报 5xx 或网络断连时，向群内发送的兜底友好提示。设为空字符串 `""` 则静默不发 |

---

## 7. 反向 MCP 通道 (FastMCP)

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `HERMES_MCP_ENABLED` | bool | `false` | 是否在插件内启动 FastMCP server。开启后提供 `push_message`、`list_active_sessions`、`get_recent_messages`、`get_message_images` 四项工具 |
| `HERMES_MCP_HOST` | str | `127.0.0.1` | FastMCP server 监听地址。**安全警告**：由于反向通道允许向任意群推送消息，切勿在未配合反向代理与 TLS 的情况下直接暴露在公网 |
| `HERMES_MCP_PORT` | int | `8643` | FastMCP server 监听端口 |
| `HERMES_MCP_RECENT_LIMIT_MAX` | int | `50` | `get_recent_messages` MCP 工具单次允许查询的最大消息条数上限 |

---

## 8. 持久化存储与图片缓存

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `HERMES_STORAGE_DB_PATH` | str | `""` | SQLite 消息数据库路径。留空则走 `nonebot-plugin-localstore` 的默认存储路径（通常为 `~/.local/share/nonebot2/nonebot_plugin_hermes/messages.db`） |
| `HERMES_STORAGE_MESSAGE_RETENTION_DAYS` | int | `30` | 消息日志保留天数。定时清理任务会自动删除超龄消息 |
| `HERMES_STORAGE_MESSAGE_MAX_ROWS` | int | `100000` | 消息日志总行数硬上限。超出时自动由老到新淘汰 |
| `HERMES_IMAGE_CACHE_DIR` | str | `""` | 图片字节缓存目录。留空走 localstore 缓存目录（通常为 `~/.cache/nonebot2/nonebot_plugin_hermes/images/`） |
| `HERMES_IMAGE_CACHE_QUOTA_MB` | int | `200` | 图片缓存总体积上限（MB），超出按访问时间 LRU 淘汰 |
| `HERMES_IMAGE_FETCH_TIMEOUT_S` | int | `10` | 抓取平台图片 HTTP 单次超时时间（秒） |
| `HERMES_IMAGE_FETCH_MAX_ATTEMPTS` | int | `2` | 单张图片抓取重试次数（1 为不重试，2 为失败后重试一次） |

---

## 9. 图片内联处理 (0.5.3+)

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `HERMES_IMAGE_INLINE_ENABLED` | bool | `true` | 出站请求前是否将当前轮图片抓取并下采样编码为 `data:image/jpeg;base64,...` 内联至请求体中（详见下方专题说明） |
| `HERMES_IMAGE_INLINE_MAX_EDGE` | int | `1280` | 内联图片的长边像素上限。超出等比缩小，小图不拉伸 |
| `HERMES_IMAGE_INLINE_QUALITY` | int | `82` | 内联重编码时的初始 JPEG 压缩质量 |
| `HERMES_IMAGE_INLINE_MAX_BYTES` | int | `2000000` | 单张图片内联后的最大字节上限（2MB）。若超出将逐级降质，降质到底仍超出的图片将被丢弃 |
| `HERMES_IMAGE_INLINE_TOTAL_MAX_BYTES` | int | `4000000` | 单轮请求中所有内联图片的总字节预算（4MB）。超出预算的图片被逐张跳过，保证请求不被体积打死 |

---

## 10. 长期记忆作用域 (0.5.0+)

详细机制与架构设计请参考 [README.md](README.md)「长期记忆作用域」与 [`honcho/`](honcho/)。

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `HERMES_HONCHO_ENABLED` | bool | `false` | **⚠️ 暂不推荐开启**。是否向 Hermes 发送 `X-Hermes-Session-Key` 请求头，按群/私聊隔离长期记忆。需要 Hermes 配置了 memory provider（如 Honcho）并设置了 `HERMES_API_KEY` |
| `HERMES_GROUP_SESSIONS_PER_USER` | bool | `false` | 群聊长期记忆颗粒度：`false` 为整群共享一份记忆；`true` 为群内每位成员独立一份记忆 |
| `HERMES_GROUP_SESSION_KEY_FORMAT` | str | `agent:main:nonebot-{adapter}:group:{group_id}` | 群共享记忆 Session Key 格式模板 |
| `HERMES_GROUP_PER_USER_SESSION_KEY_FORMAT` | str | `agent:main:nonebot-{adapter}:group:{group_id}:{user_id}` | 群按人记忆 Session Key 格式模板 |
| `HERMES_PRIVATE_SESSION_KEY_FORMAT` | str | `agent:main:nonebot-{adapter}:dm:{user_id}` | 私聊记忆 Session Key 格式模板 |

---

## 11. 按群路由多接入点 (0.5.1+)

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `HERMES_GROUP_ENDPOINTS` | dict | `{}` | 按群路由的接入点字典，键为 `{adapter}:{group_id}`，值为包含 `url`、`key`、`timeout` 的对象。**完整使用方式、安全边界与 Hermes 配置详见 [PROFILES.md](PROFILES.md)** |

---

## 专题说明：图片内联机制

在 0.5.3 之前，插件将聊天平台（如 QQ、Telegram）的原始图片 URL 原样传给 Hermes，再由上游交由 LLM Provider 抓取。这存在致命缺陷：
1. **网络穿透与时效问题**：平台图片 URL 通常带有防盗链或短时签名 token，或者位于对端 LLM Provider 出网无法直达的内网/区域 CDN。任一环节受阻将直接导致 400 报错或图片被静默丢失。
2. **Provider 行为不一致**：Anthropic/OpenAI 在图片无法访问时报错；Gemini native 会静默丢弃非 `data:` 的图片；Bedrock 则降级为纯文本占位，均出现"模型看不见图但用户不知情"的情况。
3. **隐私凭据泄漏**：平台临时认证签名直接泄漏给了第三方服务商。

**内联行为保证**：
- 插件在出向发往 Hermes 前完成抓取，并在本地经 Pillow 等比缩小（长边 ≤ `HERMES_IMAGE_INLINE_MAX_EDGE`）、压缩为 JPEG 并剥离所有 EXIF（包含地理位置、相机型号等隐私信息）。
- **宽容丢弃策略**：单张图片抓取失败、解码失败或超出单轮体积预算时，**仅丢弃该张图片**，绝不中断整轮会话。
- 设为 `HERMES_IMAGE_INLINE_ENABLED=false` 可临时退回直发 URL 行为。

---

## 专题说明：反馈 Emoji 与 Busy Notice

在 OneBot v11 群聊路径下，插件设计了两套语义分明的视觉信号：

| 信号类型 | 控制参数 | 默认表情 | 生命周期 | 表达语义 |
|---------|---------|---------|----------|----------|
| **Ack Feedback**（处理中） | `HERMES_ACK_FEEDBACK_ENABLED` | `341` (/打招呼) | 收到 @ 触发时贴上，回复完成或报错后**主动撤销** | "已收到您的消息，正在处理中" |
| **Busy Notice**（忙碌丢单） | `HERMES_BUSY_EMOJI_ID` | `97` (/擦汗) | 队列深度触顶（`MAX_REFIRE_DEPTH=3`）时贴上，**永不撤销** | "短时间内消息堆积过多，本条已放弃" |

> [!NOTE]
> - Busy Notice 独立于 Ack Feedback：即使关闭 `HERMES_ACK_FEEDBACK_ENABLED`，丢单时仍会贴上 Busy 表情，确保用户知道请求因超载被抛弃。
> - 其他平台（Telegram、Discord 等）因暂无原生类似的消息贴表情接口，若触发丢单会降级打印 WARN 日志，避免以无意义的文本噪声打扰群聊。
