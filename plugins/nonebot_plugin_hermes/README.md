# nonebot-plugin-hermes

中文文档 | [English](https://github.com/gsskk/nonebot-plugin-hermes/blob/main/README_EN.md)

[Hermes Agent](https://github.com/NousResearch/hermes-agent) 的 NoneBot2 插件，通过 Hermes API Server 实现多平台 AI 聊天机器人。

## 支持的平台

通过 NoneBot adapter 机制，本插件自动支持：

- ✅ OneBot v11（NapCatQQ、LLOneBot、go-cqhttp 等）
- ✅ OneBot v12
- ✅ QQ Official Bot
- ✅ Kook（开黑啦）
- ✅ Discord
- ✅ Telegram
- ✅ 飞书
- ✅ 其他 `nonebot-plugin-alconna` 支持的平台

## 工作原理

```
用户消息 → NoneBot Adapter → nonebot-plugin-hermes
  → POST /v1/chat/completions (Hermes API Server)
  → 解析回复 → UniMessage.send() → NoneBot Adapter → 用户
```

## 功能

- ✅ 私聊 / 群聊对话
- ✅ 多轮上下文记忆（基于 Hermes Session）
- ✅ 群聊 @触发 / 关键词触发 / 全部触发
- ✅ **引用消息提取**：自动提取被回复消息中的文本和图片作为 AI 上下文
- ✅ **被动感知 (Chat Awareness)**：在群聊中默默记录最近对话，为下次触发提供完整背景
- ✅ 图片接收（通过 vision 发给 AI）
- ✅ 图片发送（解析 AI 回复中的 markdown 图片）
- ✅ 会话生命周期由 Hermes Agent 管理
- ✅ 白名单（群/用户级别）
- ✅ 内置命令（`/clear` `/ping` `/help` `/hermes-status`）
- 🧪 **群活跃态 (M1, 实验性)**：@bot 后 5 分钟内主动监听群对话，由 Hermes 通过结构化决策判断是否插话
- 🧪 **反向通道 (M1, 实验性)**：内嵌本地 MCP server，让 Hermes 主动 push 消息进群（延迟回复 / 异步通知）
- 🧪 **历史图片召回 (0.3+, 实验性)**：SQLite 持久化消息日志 + 文件系统图字节缓存 + MCP 工具 `get_message_images`，让 Hermes 在用户说"上图"/"刚才那张"时按消息 id 精确取回历史图字节
- 🧪 **OneBot v11 Notice 触发 (0.3.3+, 实验性)**：戳一戳作为第二种 @ 等价触发；有人入群时让 Hermes 自决要不要欢迎（noop 合法，不做模板欢迎语）
- 🧪 **消息段感知扩展 (0.3.4+, 实验性)**：语音/视频/QQ 表情/sticker 占位文本注入 LLM 视野;sticker 自动跳过 vision API。OneBot v11 NapCat 显式 @ 时贴 emoji 回执(`HERMES_ACK_FEEDBACK_ENABLED=true`)
- ✅ **合并转发处理 (0.4.0+)**：群里收到合并转发消息时展开为有限长度摘要；0.5.3 起全文随消息持久化——活跃窗口内最新一条折叠消息在后续对话的历史中以全文呈现，引用折叠消息时全文内联进当前消息；bot 自身长回复在 OneBot v11 群里转为合并转发避免截断

## 快速开始

### 1. 前置条件

- 已安装并运行 Hermes Agent，且 API Server 已启用
- 已安装 NoneBot2 和对应平台的 adapter

### 2. 启用 Hermes API Server

在 `~/.hermes/.env` 中添加配置：

```bash
# 启用 API Server 并指定端口
API_SERVER_ENABLED=true
API_SERVER_PORT=8642
# 如果 NoneBot 和 Hermes 不在同一台机器上，需要监听所有 IP：
# API_SERVER_HOST=0.0.0.0
```

设置 API Key（**必须**，用于会话保持）：

```bash
# 生成密钥
python3 -c "import secrets; print(secrets.token_hex(32))"
# 或 openssl rand -hex 32

# 写入 Hermes 环境配置
echo 'API_SERVER_KEY=your-generated-key' | tee -a ~/.hermes/.env
```

> **Note**: 不设置 `API_SERVER_KEY` 会导致 Session 续接被拒绝，每次对话无法保持上下文。

启动 Hermes Gateway：

```bash
hermes gateway
```

### 3. 安装插件

**方式 A：使用 nb-cli 安装（推荐）**

```bash
nb plugin install nonebot-plugin-hermes
```

**方式 B：使用 pip / uv 安装**

```bash
pip install nonebot-plugin-hermes
# 或 uv add nonebot-plugin-hermes
```

在 `pyproject.toml` 中添加插件（如果是 nb-cli 安装会自动添加）：

```toml
[tool.nonebot]
plugins = ["nonebot_plugin_hermes"]
```

**新建 NoneBot 项目的完整步骤**：

```bash
pip install nb-cli
nb create          # 创建项目，选择 fastapi 驱动器
nb plugin install nonebot-adapter-onebot  # 安装对应平台的适配器，例如 OneBot
nb plugin install nonebot-plugin-hermes   # 安装 Hermes 插件
```

### 4. 配置

复制示例配置：

```bash
cp .env.example .env
```

编辑 `.env`。以下为运行本插件的**最低限度配置项**（即不配置就无法使用的设置）：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `HERMES_API_URL` | `http://127.0.0.1:8642` | Hermes API Server 接入点地址。若 Hermes 部署在其他机器或端口则必须配置 |
| `HERMES_API_KEY` | (空) | **必须配置**（需与 Hermes 宿主机 `~/.hermes/.env` 中的 `API_SERVER_KEY` 一致）。不配置会导致会话续接被拒绝，每次对话无法保持上下文 |

> [!NOTE]
> 此外，机器人还需至少配置一个平台适配器（如 OneBot v11 的 `ONEBOT_WS_URLS`）以建立与聊天平台的连接。

`.env` 最小配置示例（以 OneBot v11 正向 WebSocket 为例）：

```env
# 平台适配器连接（此处以 OneBot 正向 WS 为例）
ONEBOT_WS_URLS=["ws://127.0.0.1:3001"]

# Hermes API 接入与鉴权
HERMES_API_URL=http://127.0.0.1:8642
HERMES_API_KEY=your-api-server-key
```

完整的可选配置项（触发方式、白名单、群活跃态、反向 FastMCP 通道、图片内联、持久化存储、长期记忆等 50+ 项参数）详见独立配置文档：

**→ [完整配置文档（全部可选配置参数详解）](CONFIG.md)**

### 5. 运行

```bash
nb run
```

## 可用的 AI 工具

本插件通过 Hermes 的 `api_server` 平台通信，默认使用 `hermes-api-server` 工具集：

| 工具类别 | 包含的工具 |
|---------|-----------|
| Web 搜索与提取 | `web_search`, `web_extract` |
| 终端与进程 | `terminal`, `process` |
| 文件操作 | `read_file`, `write_file`, `patch`, `search_files` |
| 视觉与图片生成 | `vision_analyze`, `image_generate` |
| 浏览器自动化 | `browser_navigate`, `browser_snapshot` 等 |
| 规划与记忆 | `todo`, `memory`, `session_search` |
| 代码执行与委托 | `execute_code`, `delegate_task` |
| 定时任务 | `cronjob` |
| 智能家居 | `ha_list_entities`, `ha_get_state` 等 |

### 🔒 安全最佳实践：限制 API Server 工具集

默认的 `hermes-api-server` 工具集包含 `terminal`、`execute_code` 等危险工具。**针对不同的部署环境，强烈建议配置不同的受限工具集，特别是在公共群聊中，必须禁止文件读写（`file` 工具）以防敏感信息泄露或被植入后门。**

在 `~/.hermes/config.yaml` 中配置 `platform_toolsets`：

```yaml
platform_toolsets:
  # 其他平台保持默认
  cli: [hermes-cli]
  telegram: [hermes-telegram]

  # API Server 根据部署场景选择工具集 (见下方推荐)
  api_server: [web]
```

推荐的部署安全级别：

| 部署场景 | 推荐配置 | 包含的工具集 | 说明 |
| :--- | :--- | :--- | :--- |
| **🔴 公共群聊 (极简防刷)** | `[web]` | 仅 `web` (联网搜索) | **对外公开机器人的最稳妥配置。** 杜绝文件操作，同时避免画图/识图带来的高昂 API 费用和合规封号风险。 |
| **🟠 公共群聊 (含多媒体)** | `[safe]` | 搜索 + 识图 + 画图 | 等同于 `[web, vision, image_gen]`。增加了视觉能力，但需注意防范 API 被刷或恶意图片封号的风险。 |
| **🟡 内部/信任群聊 (受限读写)** | `[web, vision, image_gen, memory, session_search]` | 搜索 + 多媒体 + 记忆 | 适合公司内部群或好友群。允许发图画图、保留跨会话记忆，但依然严格禁止文件读写。 |
| **🟢 站长私聊 (高级管理)** | `[web, file, vision, image_gen, skills, todo, memory, session_search]` | 包含文件读写、技能管理等 | 适合机器人主人的私聊。有文件读写能力，可通过群白名单机制将其他群屏蔽。 |
| **💀 危险/开发环境 (完全信任)** | `[hermes-api-server]` | 包含终端、代码执行等全部工具 | （默认）仅限开发者自己在安全的隔离环境使用。 |

> [!WARNING]
> **关于 `memory` 和 `session_search` 的跨群隐私泄露风险：**
> Hermes Agent 的底层数据库是全局共享的（无平台/群组隔离）。如果在多群共用的 Agent 上开启这两个工具，**A群的成员可以搜到B群的聊天记录，甚至你的私人终端/私聊记录**。若看重隐私隔离，多群共用时请勿包含 `memory` 和 `session_search`。普通的上下文多轮对话由临时 Session 维护，不受关闭这两个工具的影响。

### 🆔 用户身份与元数据注入

本插件会自动向 Hermes API 注入以下元数据，使后端 LLM 具备环境感知能力：

*   **用户标识** (`user_id`): 用户的平台 ID（如 QQ 号）。
*   **群组标识** (`group_id`): 消息来源群号（私聊则为空）。
*   **适配器名称** (`adapter_name`): 消息来源平台（如 `OneBot V11`, `Discord`, `Telegram` 等）。
*   **私聊状态** (`is_private`): 当前是否为私聊环境。

后端 Prompt 可以通过这些信息实现个性化称呼或针对特定平台的功能逻辑。

### 🔄 会话轮换（0.4.5+）

插件用 `X-Hermes-Session-Id` 请求头维持会话连续性,key 由 `{adapter}+{private|group}+{ids}`
派生(`/clear` 递增 `-gN`)。但这个 id 不是永久不变的:Hermes 自动压缩上下文时会**轮换会话**
—— 旧 id 被置为 `end_reason='compression'` 并关闭,新建一个 continuation 子会话,新 id 通过
**响应头** `X-Hermes-Session-Id` 回传。

插件从 0.4.5 起采纳这个回传值,并把 `internal_id → session key` 映射持久化到消息库同目录的
`session_keys.db`(`/clear` 的 generation 也一并持久化,重启不再复活被清掉的会话)。

0.1.0 ~ 0.4.4 不读这个响应头,后果是每轮都把会话钉回已关闭的父会话:读还能跟随 tip,写全部
失败,而且每压缩一次就再分叉一个兄弟快照。live 子会话超过一个后,Hermes 的
`find_live_compression_child()` 判定歧义并 fail-closed,该会话从此永久写不进去。旧版本的
Hermes 容忍往已关闭会话追加,所以这个问题长期无声;上游 2026-07-23 的
`fix(compression): recover rotated session lineage` 之后变成硬失败,日志会刷
`Session '…' is closed by compression`。存量损坏用 `hermes-repair-sessions` 修。

另一头是**上游把自动压缩关掉**(`compression.enabled: false`)的情形。这时窗口只涨不缩,某一轮
撑过模型上下文后,Hermes 会禁掉包括会话轮换在内的全部自动恢复,只回一段提示文本 —— 而且是
HTTP 200 + 正常的助手回复外壳,所以它会被当成模型发言。下一轮窗口只会更大,该会话从此每轮
都失败。

插件从 0.5.4 起识别这段提示:不转发给用户,改为重置会话(等同 `/clear`,递增 `-gN`)后把本轮
重发一次,并在聊天里说明一句。丢掉的是 Hermes 侧的 transcript —— 群聊每轮仍会注入插件自己的
`<recent_messages>` 窗口,近处的对话照样看得见,接不上的是更早的部分。重置后仍然溢出则放弃本轮
并打 ERROR:那说明病因不在历史,而在单轮本身(system prompt 加历史窗口就已经超了)。

### 🧠 长期记忆作用域（0.5.0+，默认关，暂不推荐）

> **⚠️ 暂不推荐开启。** 当前 api_server 路径不向记忆层传递 user 标识,群成员在记忆侧会
> 折叠成同一个 peer,画像是全群的混合,能带来的收益有限;而 Honcho 的后台提炼、检索与
> embedding 会持续附带额外的 token 成本。建议等上游支持入站 user 标识、且记忆原料清洗
> 落地后再开——重开判据见 [`honcho/`](honcho/) 的「停用与重开」一节。

Hermes 侧的长期记忆(memory provider,目前是 Honcho)默认**不按群区分**。插件不告诉它
"这段对话属于谁"时,它按自己的兜底策略给记忆命名,两种兜底都有问题:

- 全局 / 按目录策略 → **所有群共写一份记忆**,bot 在 A 群知道的事会在 B 群说出来;
- 按会话策略 → 以 Hermes 会话 id 为记忆键,而这个 id **每次自动压缩都会轮换**(见上一节),
  于是记忆每压缩一次换一本,长期什么都攒不下来。

开启本功能后,插件用 `X-Hermes-Session-Key` 请求头显式告诉上游记忆该记在谁名下。这个头与
`X-Hermes-Session-Id` 是两个独立维度:后者管"接哪段对话历史",会随 `/clear` 和自动压缩轮换;
前者管"记忆记在谁名下",跨 `/clear`、跨压缩恒定不变。

```dotenv
HERMES_HONCHO_ENABLED=true
# 群记忆按群还是按人:false = 一个群一份,群成员共享;true = 群内每人一份
HERMES_GROUP_SESSIONS_PER_USER=false
```

部署 Honcho 的 compose 与配置示例见 [`honcho/`](honcho/) 目录(含成本说明与验证方法)。

**前置条件**(缺任一条,功能静默无效):

1. Hermes 端已配好 memory provider(`hermes memory setup`)。Honcho 本身要么用 Honcho Cloud
   (按量计费),要么自托管一套 Postgres + pgvector + FastAPI 服务,不是加个开关就有的东西。
2. 插件配了 `HERMES_API_KEY`。上游对这个头要求鉴权,没 key 时插件不发头并在启动日志 WARN。
3. Hermes 端的 `~/.hermes/honcho.json` **不能有 `peerName` 这个键**。它有值时所有群共用一个
   memory peer,画像层(representation / peer card)会跨群共享,隔离只做到对话记录那一层。
   `hermes memory setup` 向导会把它默认成当前用户名且不接受留空,跑完要手动删掉。
   详见 [`honcho/README.md`](honcho/README.md)。

**切换代价**:开启后记忆作用域改名,此前累积在旧作用域下的记忆不再被读到。数据仍在 Hermes
侧,关掉开关即回原状。另外记忆需要累积,头一两周体感不明显。

记忆 key 默认形如 `agent:main:nonebot-{adapter}:group:{group_id}`,对齐 Hermes 原生 adapter 的
命名格式,`nonebot-` 前缀用于防止与 Hermes 原生 adapter 写进同一个 workspace 时撞名。三个模板
(群共享 / 群按人 / 私聊)都可通过 `HERMES_*_SESSION_KEY_FORMAT` 覆盖,一般不需要。

> [!NOTE]
> 本开关只隔离**记忆**。`session_search` 工具搜的是整个 state.db,不分群(见上文
> "限制 API Server 工具集"的警告),要一并堵住需在 Hermes 端的 `platform_toolsets.api_server`
> 里移除该工具。终端 / 文件类工具的工作区同理不受本开关影响。

## 群活跃态 + 反向通道（M1，实验性）

启用后，bot 在被 @ 之后会进入 5 分钟"活跃窗口"——期间能听到所有群消息（无需再 @），由 Hermes Agent 通过结构化决策（`should_reply` / `should_exit_active`）自行判断是否插话。同时插件起一个本地 MCP server，让 Hermes 可以主动 push 消息进群（延迟回复、异步通知等）。

### 启用

在 `.env` 中：

```env
HERMES_ACTIVE_SESSION_ENABLED=true
HERMES_MCP_ENABLED=true
```

> 启用 `HERMES_ACTIVE_SESSION_ENABLED` 时被动感知会自动开启(消息缓冲是活跃态的依赖),无需再单独设置 `HERMES_PERCEPTION_ENABLED`。后者只在 active=false 的群聊里有意义——给 @bot 那一刻的 LLM 注入旁观历史。

重启后 bot 会：

- 监听 `127.0.0.1:8643` 暴露 MCP 工具:`push_message` / `list_active_sessions` / `get_recent_messages` / `get_message_images`
- 在 @bot 触发后进入 reactive 模式，5 分钟内对群消息做 should_reply 决策（每次插话续期）
- 把每条群消息持久化到 SQLite(默认走 `nonebot-plugin-localstore`,通常 `~/.local/share/nonebot2/nonebot_plugin_hermes/messages.db`)并分配稳定 msg_id;`<recent_messages>` prompt 块的每条历史前缀变成 `[m:<id>]`,Hermes 凭此 id 调 `get_message_images` 取回历史图字节

> ⚠️ **安全注意 ——`HERMES_MCP_HOST` 默认 `127.0.0.1`(loopback)。** 改成监听公网 / 局域网地址在技术上完全可行,但安全后果是:`push_message` 工具能让 bot 往群里发任意内容,而当前防御仅有 Bearer token(明文 HTTP 传输,且与 `HERMES_API_KEY` 同钥匙)。改之前请配套上反向代理(TLS 终结) + 来源 IP ACL,否则任何能 reach 该端口的进程一旦拿到 token 就可以冒名发送。

### 把插件能力告诉 Hermes Agent

插件自带一份 `SKILL.md`（reactive 决策契约 + 反向通道用法），要装到 **Hermes 那台**的
`<HERMES_HOME>/skills/nonebot-bridge/`(默认 `~/.hermes/skills/nonebot-bridge/`)—— 装错机器的话
Hermes 读不到,skill 不生效。安装位置跟随 `HERMES_HOME`(0.5.1+),所以多 profile 部署下按 profile
分别装:`HERMES_HOME=~/.hermes/profiles/<name> hermes-install-skill`。

bot 与 Hermes **同机**时,在 bot 项目目录下任选一种执行：

```bash
# 用 uv 管理依赖
uv run hermes-install-skill

# 或者 bot 项目用普通 venv
.venv/bin/hermes-install-skill

# 或者已激活虚拟环境
hermes-install-skill

# 备用入口（任何能 import nonebot-plugin-hermes 的环境）
python -m hermes_install_skill
```

**分机**部署时,Hermes 那台一般没装插件,不必为此装一遍 —— 脚本只用标准库,克隆仓库直接跑即可
（它按相对路径读同仓库的 `nonebot_plugin_hermes/skill/SKILL.md`,所以要整个仓库目录,不能只拷单文件）：

```bash
git clone https://github.com/gsskk/nonebot-plugin-hermes.git
cd nonebot-plugin-hermes
python3 hermes_install_skill.py
```

（也可以在 bot 那台跑完再把 `~/.hermes/skills/nonebot-bridge/SKILL.md` 拷过去,效果一样。）

然后在 `~/.hermes/config.yaml` 注册插件 MCP server，把 `<HERMES_API_KEY>` 替换为你前面生成的同一把密钥（用于双向鉴权）：

```yaml
mcp_servers:
  nonebot-bridge:
    url: http://127.0.0.1:8643/mcp
    headers: { Authorization: "Bearer <HERMES_API_KEY>" }
```

后续插件 SKILL.md 升级时,用上面同样的入口加 `--force` 重装,例如 `uv run hermes-install-skill --force` 或 `.venv/bin/hermes-install-skill --force`。

## 按群路由到不同 Hermes 接入点 / 多路复用(0.5.1+,默认关)

让指定的群走各自的 Hermes **profile**,拥有该群独占的工具集、模型和文件工作区;反向通道的权限
范围也按接入点自动收敛。只想让 bot 别把 A 群的事在 B 群说出来?不用上这套 —— 见上文
「长期记忆作用域」。

配置、Hermes 侧步骤、反向通道 token 收敛、端到端示例(含**多个群共用同一 profile**)、以及运维
限制,单独成篇:

**→ [按群路由 / 多路复用 / profiles 完整文档](PROFILES.md)**

## 历史图片召回（0.3+，实验性）

在 0.3 起,消息感知 + 反向通道一起开启时,bot 自动启用一条"按消息 id 精确召回历史图"的通路。典型场景:

```
T0    用户 A:  [图片]                    ← 仅文字描述,bot 看到 [图片] 占位
T+5s  用户 B:  @bot 评价下上图
                ↓
                Hermes 看到 prompt 里 [m:1234] A: [图片]
                Hermes 调 get_recent_messages → 知道 m:1234 有图(image_count=1)
                Hermes 调 get_message_images([1234]) → 拿到字节
                下一轮 LLM 真的看到那张图,回复正常
```

技术细节:

- **持久化**:消息进 SQLite,路径由 `nonebot-plugin-localstore` 管理(默认 `~/.local/share/nonebot2/nonebot_plugin_hermes/messages.db`,可被 `LOCALSTORE_*` env vars 整体重定向);自增 id 即 `[m:<id>]` 前缀的 N
- **字节缓存**:perception 看到图后异步抓 URL → 落到 localstore 管理的 cache dir(默认 `~/.cache/nonebot2/nonebot_plugin_hermes/images/<sha256>.<ext>`),LRU 按 atime 淘汰,默认 200MB 上限
- **失败降级**:URL 短效过期 / 缓存被淘汰 / 消息已过 30 天保留期 → MCP 工具返回 `available: false`,Hermes 礼貌告知用户图不可用,不崩
- **保留窗口**:消息 30 天或 10 万条上限(谁先到),整点 :37 后台 vacuum

如果你的 Hermes 后端模型偏弱、识别 `[m:<id>]` 约定不稳,bot 行为退化为今天的"看不到上图"——无 regression。

## 命令

| 命令 | 说明 |
|------|------|
| `/clear` | 重置对话，开始新会话 |
| `/ping` | 检查 Hermes Agent 连接状态 |
| `/help` | 显示帮助信息 |
| `/hermes-status` | 打印 M1 运行时状态（MCP / 活跃 sessions / buffer / registry）。**需在 `HERMES_ADMIN_USERS` 显式授权 `adapter:user_id`**;非管理员调用时静默无响应,且 `/help` 输出里也不出现该命令 |

### 命令行工具

| 命令 | 在哪台跑 | 说明 |
|------|---------|------|
| `hermes-install-skill --force` | **Hermes 那台** | 把 `SKILL.md` 装到 `~/.hermes/skills/nonebot-bridge/`(覆盖已装版本要带 `--force`) |
| `hermes-purge-media` | **bot 那台** | 清理插件消息库(`messages.db`)里内联的 base64 图片字节。默认只报告,`--apply` 写回,`--vacuum` 收缩文件 |
| `hermes-optimize-state` | **Hermes 那台** | 把 Hermes `state.db` 里历史 turn 重复注入的 `<recent_messages>` 窗口去重、摘掉静态协议尾句,再回收空间。默认只报告,`--apply` 备份后改写 |
| `hermes-repair-sessions` | **Hermes 那台** | 解开 Hermes `state.db` 里被 compression 血缘歧义卡死的会话。默认只报告,`--apply` 备份后修复 |

「在哪台跑」取决于工具动的是谁的数据,不是谁装了插件。bot 与 Hermes 同机时四个命令都能直接敲;
**分机部署时 Hermes 那台通常并没有装本插件**,此时不需要为了跑工具去装一遍——四个脚本都是仓库
根目录下的单文件、只用标准库、也不 import 本包:

```bash
git clone https://github.com/gsskk/nonebot-plugin-hermes.git
cd nonebot-plugin-hermes
python3 hermes_repair_sessions.py            # 与 hermes-repair-sessions 完全等价
```

#### `hermes-optimize-state`:清掉 transcript 里重复注入的窗口

reactive 与 passive 两路都把 `<recent_messages>` 窗口裹进 user message。窗口有几十行、每轮只新增
一两行,于是同一条群发言在 `state.db` 里被存了几十遍;Hermes 每轮回放整条 transcript,这份重复既
是磁盘也是账单。本工具把历史窗口回溯成增量形状:每条群发言只保留第一次出现,并摘掉每行都带一份
的静态协议尾句(system 端的 `decision_protocol` 已经写着同一件事)。`<runtime_state>` 与
`<current_message>` 是本 turn 的实况,原样保留。

```bash
hermes-optimize-state                  # 只看:能省多少、会动哪些行
hermes-optimize-state --sample         # 再打印 3 条改写前后的对照,看清改完还能不能读
hermes-optimize-state --sample 5       # 抽 5 条
hermes-optimize-state --apply          # 备份后改写 + 合并 FTS 段 + VACUUM
```

**多 profile:库要逐个跑。** 每个 profile 是一份完整的 `HERMES_HOME`,所以有三种指定方式:

```bash
hermes-optimize-state --profile team          # ~/.hermes/profiles/team/state.db
HERMES_HOME=/opt/data hermes-optimize-state   # 自定义部署
hermes-optimize-state --db /path/to/state.db  # 直接给完整路径,盖过一切推导
```

`--profile default` 指的是根 home(`~/.hermes/state.db`),不是 `profiles/default` —— 与上游
`get_profile_dir` 同规则;profile 名按小写归一;`--profile` 始终锚在**根** home 上,所以身处
team profile 时 `--profile other` 找的是 `~/.hermes/profiles/other`,不是嵌套路径。

命中的来源就是最终答案:**指定的库不存在时直接报错退出,绝不回落到别的 profile**。profile 名打错、
或 profile 还没初始化,都是这个形状 —— 回落会把改写静默落到默认 profile 那份库上,而那份库事后看
起来一切正常。

`--sample` 展示的是**改写后**的整行(模型下一轮实际会读到的东西),被删掉的重复消息折成一行计数
——压缩比回答不了「改完还能不能读」。样本取自改写行最多的那个会话、按位置均匀铺开:去重效果随
位置差别极大(冷启动那条几乎整扇窗口都留着,中段每轮只留一两条),只挑省得最多的行会把效果说得
过好。

跑之前先停 `hermes-gateway`(拿不到写锁会直接退出)。几件事值得先知道:

- **只动 `sessions.source='api_server'` 且 `role='user'` 的行** —— CLI / TUI / cron 会话不是插件流量,
  一行不碰;assistant 侧是模型自己的输出,也不碰。
- **去重单位是消息,不是物理行**。历史行的正文本身可以带换行(bot 发的列表、多段回复),按物理行
  去重会把两条消息共有的某一行从后出现的那条身上摘走 —— 消息被削,而首行还在。
- **键用 `[m:<id>]` 主键、并且按 session 分别算**。每个 Hermes session 是独立 transcript,
  `/clear` 之后的新世代该重新建立上下文,不能跨会话去重。
- **自证不变量**:改写前记下每条消息的主键**与正文摘要**,改写后逐一回查 —— 少一条、或留存的正文
  不是原文里出现过的任一份,就整体回滚。只比对主键不够:正文被削掉几行的消息,首行还在,主键查得到。
- **FTS 索引由触发器跟随 `UPDATE` 维护**,但触发器挂在 rebuild 水位标记上——rebuild 半途时改写会
  绕过它、静默毁掉搜索索引。发现标记在场即拒绝执行,先跑完 `hermes sessions optimize-storage`。
- 副作用:同一条群发言此后在全文检索里只命中一次(而不是每个回显它的 turn 各命中一次),仍然找得到。
- 代价:被改写的会话下一轮是一次完整的 prompt cache miss,之后按新的、更小的前缀重新建缓存。

纯粹的空间回收(不改内容)上游自带:`hermes sessions optimize` 合并 FTS 段 + `VACUUM`,
`hermes sessions prune --older-than N` 按保留期删会话,`hermes sessions optimize-storage` 把旧的内联
FTS 布局迁到紧凑布局。本工具补的是它们都不做的那件事:**同一条内容在 transcript 里的重复副本**。

(只拷单个文件过去跑也行;`hermes-install-skill` 例外——它要读同仓库里的
`nonebot_plugin_hermes/skill/SKILL.md`,得带上仓库目录。)

`hermes-purge-media` 用于清理历史遗留:早期版本会把 agent 回复里 api_server 内联的
`data:image/…;base64,…` 整段存进消息库,单条可达 MB 级。当前版本写入端与渲染端都已挡住,
这个命令只把存量字节清出去。

在 **bot 那台**(插件装在那儿)执行:

```bash
uv run hermes-purge-media                    # 只报告:每群命中数、最大行、可回收字节
uv run hermes-purge-media --apply --vacuum   # 清理并收缩文件
```

bot 项目用普通 venv 就换成 `.venv/bin/hermes-purge-media`;已激活虚拟环境时可直接敲
`hermes-purge-media`。裸命令只在虚拟环境已激活时才在 PATH 上。

不删消息,只把图片 payload 换成 `[图片]` 占位;幂等,可反复运行。`--vacuum` 需要排它锁,
拿不到时停掉 bot 再跑。

`hermes-repair-sessions` 修的是 Hermes 侧 `state.db` 的会话血缘。症状是 Hermes 日志反复刷:

```
Session '…' is closed by compression; adopt its live continuation before appending messages
compression skipped: … no unique live child could be adopted
```

成因见上文「会话轮换」:0.1.0 ~ 0.4.4 的插件不采纳轮换后的 session id,每压缩一次就从同一个
已关闭的父会话再分叉一个快照子会话;live 子会话超过一个后上游判定歧义并 fail-closed,该会话
从此写不进去 —— 对话记录冻结,上下文还会无限膨胀(压缩永远跑不完)。

在 **Hermes 那台**执行。那台一般没装插件,所以下面直接用单文件形式:

```bash
systemctl stop hermes-gateway     # 修复期间要拿写锁,跑着的 agent 也可能持有旧会话状态

git clone https://github.com/gsskk/nonebot-plugin-hermes.git
cd nonebot-plugin-hermes
python3 hermes_repair_sessions.py            # 只报告:哪些会话卡住、会动哪些行
python3 hermes_repair_sessions.py --apply    # 整库备份后:重开父会话 + 退休快照子会话

systemctl start hermes-gateway
```

与 bot 同机、插件已装时,把这两行换成 `uv run hermes-repair-sessions [--apply]` 即可。

不删任何消息行。**先把插件升级到 0.4.5+ 并重启**,再停掉 gateway 跑修复 —— 否则下一次压缩
会把父会话再次关闭,几轮之内又卡回去。某个子会话若是被真正续写过的 continuation
(消息跨度远超一次批量写入),脚本会跳过该会话并报告,交给人判断。


## 限制

由于通过 HTTP API 与 Hermes 通信（而非原生 Gateway Adapter），以下功能不可用：

- ❌ 追问用户（`clarify` 工具）
- ❌ 跨平台发消息（`send_message` 工具）
- ❌ 语音合成发送（`text_to_speech` 工具）
- ❌ 危险命令审批按钮
- ❌ Cron 定时主动推送
- ❌ 中断正在运行的 Agent

## License

MIT
