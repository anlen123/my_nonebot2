# Honcho 自托管示例

`HERMES_HONCHO_ENABLED=true` 只是让插件多发一个 `X-Hermes-Session-Key` 请求头。真正把
长期记忆按群存起来的是 **Hermes 那端的 memory provider**,目前只有 Honcho 会消费这个头
(其他 provider 收到也不理)。

这个目录是部署 Honcho 的示例配置,不是插件运行的必需品——不接 memory provider 时,
那个开关是 no-op,插件行为与关掉时完全一致。

| 文件 | 用途 |
|------|------|
| `docker-compose.yml` | 四个服务:api / deriver / postgres(pgvector) / redis |
| `.env.example` | 面向本插件用途裁剪过的配置示例 |

## 前置

- Docker(上游没有发布预构建镜像,api 与 deriver 都从源码构建,首次拉取 + 构建约 2~3G)
- 一个 OpenAI 兼容的 LLM 网关与 key
- 磁盘:镜像落在 docker 的 data-root,pgdata 落在 `HONCHO_DATA`,两处都要预留空间

## 部署

```bash
git clone https://github.com/plastic-labs/honcho.git /opt/honcho

mkdir -p ~/honcho-deploy && cd ~/honcho-deploy
cp <本仓库>/honcho/docker-compose.yml .
cp <本仓库>/honcho/.env.example .env
$EDITOR .env                      # 填 key、改 POSTGRES_PASSWORD

export HONCHO_SRC=/opt/honcho     # 源码位置
export HONCHO_DATA=/data/honcho   # 数据落盘位置(可选,默认 ./data)

docker compose up -d --build
docker compose logs -f api        # 首次启动会自动跑数据库迁移
```

API 默认在 `http://127.0.0.1:8000`。

## 接到 Hermes

Honcho 是 Hermes 的插件,而 **Hermes 的插件是 opt-in 的**——只加载
`~/.hermes/config.yaml` 里 `plugins.enabled` 列出的插件。没启用时 `hermes honcho`
这个子命令根本不存在(报 `invalid choice: 'honcho'`),因为它是插件注册出来的。

```yaml
# ~/.hermes/config.yaml
plugins:
  enabled:
    - honcho
```

装上 `honcho-ai` 依赖后重启 Hermes,再跑初始化向导:

```bash
hermes memory setup       # 从 provider 列表里选 honcho
```

`hermes memory setup` 是框架入口,会扫描 `plugins/memory/` 下已启用的 provider 让你选;
选中 honcho 后进入它自己的向导。插件启用之后,`hermes honcho <子命令>` 也会同时可用:

```bash
hermes honcho status      # 连接状态与完整配置
hermes honcho strategy    # 当前 session 命名策略
```

向导里按本插件的用途填:

| 提问 | 填什么 | 说明 |
|------|--------|------|
| Cloud or local | 按你的部署 | 自托管选 `local`,地址即上面 compose 起的 `http://localhost:8000` |
| Local JWT | 留空 | 对应服务端 `AUTH_USE_AUTH=false`;跨机访问时必须改为开启鉴权 |
| Your name (user peer) | 随便填,**之后要删** | 见下一节,这是决定隔离是否完整的键 |
| AI peer name | `hermes` | bot 在 Honcho 里的身份 |
| Workspace ID | `hermes` | 顶层租户隔离;想和 CLI 数据彻底分开可以用单独的值 |
| gateway users → peers | 默认 `[3]` | **对本插件无效**,只影响原生 gateway 平台,见下一节 |
| Observation mode | `directional` | 默认即可 |
| Write frequency | `async` | 后台线程写入,不阻塞回复 |
| Recall mode | `context` | 见下方配置表 |
| Context tokens | `1200` | 同上 |
| Dialectic cadence | `5` | 同上 |
| Reasoning level | `low` | 同上 |
| Session strategy | `per-session` | 只影响 CLI;bot 走 `X-Hermes-Session-Key`,它优先级最高 |

最后在 bot 的 `.env` 里打开插件侧开关:

```dotenv
HERMES_HONCHO_ENABLED=true
HERMES_GROUP_SESSIONS_PER_USER=false   # 一个群一份记忆,群成员共享
```

需要注意 Hermes 端必须配了 `API_SERVER_KEY`,插件端必须配了 `HERMES_API_KEY`——
上游对 `X-Hermes-Session-Key` 要求鉴权,没有 key 时插件不发这个头并在启动日志 WARN。

## 关键一步:删掉 `peerName`,否则隔离只做一半

`hermes memory setup` 跑完后,**务必打开 `~/.hermes/honcho.json` 把 `peerName` 这个键删掉**。
向导会把它默认成当前用户名(`root` 之类)且不接受留空,而这个值直接决定了群间隔离是否完整。

Honcho 的记忆有两根正交的轴:

| 轴 | 由谁决定 | 承载什么 |
|----|---------|---------|
| **session** | 本插件发的 `X-Hermes-Session-Key` | 对话记录、session summary |
| **peer** | Hermes 端的 `peerName` / runtime user id | representation、peer card、dialectic 回答 |

peer 的解析顺序是「`pinUserPeer` → runtime user id → `peerName` → 从 session key 派生」。
本插件走 api_server,而 **api_server 不会把 user_id 传给 memory provider**,所以它跳过第二档:

- **`peerName` 有值** → 所有群共用同一个 peer。对话记录按群分了,但**画像层跨群共享**——
  A 群沉淀的用户画像会出现在 B 群的回复里,也就是最初要解决的那个问题。
- **`peerName` 不存在** → 落到最后一档,peer 从 session key 派生,**每个群一个**,两根轴同时隔离。

向导里那个「gateway users map to memory peers」的 1/2/3 选项**对本插件无效**——上游代码注释
自己写明了那些键只影响能提供 runtime user id 的原生 gateway 平台(飞书 / Telegram / QQ 等)。
按你那些原生渠道的需要选即可,默认 `[3]` 通常是对的。

**代价**:同一个 Hermes 实例上的 CLI / TUI 会话同样不传 runtime user id,删掉 `peerName` 后
它们的个人画像也会按 session 碎片化。如果这台 Hermes 你自己也开 CLI 用,就给 bot 单开一个
profile(profile 目录就是独立的 `HERMES_HOME`,`honcho.json` 按 profile 分 host block 存),
让两边互不影响。纯 bot 服务器则直接删,没有副作用。

顺带一提,原生渠道(飞书 / QQ / Telegram 等)不受这个键影响——它们会传真实 user id,
每个真人本来就有自己的 peer。

## 建议的 `~/.hermes/honcho.json`

向导跑完后的最终形态。两处与向导输出不同,都是漏了就出问题的:**没有 `peerName` 这一行**
(有它则画像跨群共享),**有 `reasoningLevelCap` 这一行**(没它则 reasoning level 被架空):

```json
{
  "hosts": {
    "hermes": {
      "baseUrl": "http://localhost:8000",
      "workspace": "hermes",
      "aiPeer": "hermes",

      "recallMode": "context",
      "contextTokens": 1200,
      "dialecticCadence": 5,
      "dialecticReasoningLevel": "low",
      "reasoningLevelCap": "low",
      "dialecticMaxChars": 600,

      "writeFrequency": "async",
      "observationMode": "directional",
      "sessionStrategy": "per-session",

      "pinUserPeer": false,
      "userPeerAliases": {},
      "runtimePeerPrefix": ""
    }
  }
}
```

`hosts` 的键是按 Hermes profile 派生的,所以不同 profile 各有一份配置、互不影响。

为什么这么调(向导默认值是按"单人用 CLI"设计的,群 bot 场景有三处会持续烧额度):

| 键 | 向导默认 | 建议 | 理由 |
|----|---------|------|------|
| `recallMode` | `hybrid` | `context` | tools 模式下模型每调一次 `honcho_*` 就是一次完整的 agent round-trip(又一次 chat completion)。群聊里模型爱试探,开销不可控;context 一次注入拿完 |
| `contextTokens` | uncapped | `1200` | 不封顶意味着 system prompt 随记忆增长而变长,既吃输入额度,又因为每轮内容都变而**破坏 prompt cache** |
| `dialecticCadence` | `2` | `5` | 每次 dialectic 是 Honcho 后端的一次 LLM 调用。每两轮一次,在活跃群里是持续的后台开销 |
| `reasoningLevelCap` | `high` | `low` | **漏配是静默的,后果最大** —— 它不填就是 `high`,`dialecticReasoningLevel` 会被自动升档架空。见下方「cap 是承重的」 |
| `writeFrequency` | `async` | 保持 | 后台线程写入,不占用回复路径 |
| `sessionStrategy` | `per-session` | 保持 | 只影响 CLI。bot 走 `X-Hermes-Session-Key`,它在 Honcho 的 session 名解析里优先级最高,策略管不到 |
| `pinUserPeer` / `userPeerAliases` / `runtimePeerPrefix` | 按向导选择 | 保持 | 只影响原生 gateway 平台,对本插件无效 |

其中两项有子命令可改,不必手编 JSON:

```bash
hermes honcho mode context
hermes honcho tokens --context 1200 --dialectic 600
```

`dialecticCadence` 和 `reasoningLevelCap` 没有对应子命令,要么手改 JSON,要么重跑向导时在
对应提问处填。

### cap 是承重的:漏配等于没配 reasoning level

`reasoningLevelCap` 不填不会报错,而是**静默落到 `high`**
(`plugins/memory/honcho/__init__.py` 的 `_reasoning_level_cap` 默认值)。在别的场景这只是
"允许升到高档",在本插件场景它等于**把你设的 reasoning level 整个作废**。

原因在 `_apply_reasoning_heuristic`:

```python
# Char-count heuristic: +1 at >=120 chars, +2 at >=400.
return self._LEVEL_ORDER[min(base_idx + bump, cap_idx)]
# _LEVEL_ORDER = ("minimal", "low", "medium", "high", "max")
```

它按 **query 长度**升档,而 query 就是插件发过去的整条 user message —— reactive 模式下那里面
裹着 `<recent_messages>`,轻松两三千字符,**恒定越过 400 的门槛,恒定 `bump=2`**。于是:

> 实际档位 = `min(base + 2, cap)`。只要 cap 比 base 高出 2 档以上,**base 填什么都没用,
> 你跑的永远是 cap**。

`dialecticReasoningLevel: minimal` + 没有 cap = 每轮实际跑 `medium`。差别不小:`minimal` 是
`MAX_TOOL_ITERATIONS=1`、单次输入三千余 token;升档后是五六次工具迭代、单次输入两万上下,
耗时逼近甚至越过 Hermes 那侧 30 秒的超时线 —— 越过就是白烧,没人接收结果。

所以 **cap 才是你要认真填的那个键**,base 只在两者相差不足 2 档时才参与决定。

改完用 `hermes honcho status` 确认生效值,别只看文件:

```
  Dialectic cad:  every 5 turns
  Reasoning:      base=minimal, cap=minimal, heuristic=on
```

`heuristic=on` 不必关 —— 它照样算出 `bump=2`,但 `min()` 会被 cap 夹住,升不上去等于没升。

顺带一提,`honcho.json` 出现**重复键**时(重跑向导容易叠出来)按 last-wins 解析,不报错。
看到同一个键出现两次,以最后一次为准,并且该清掉。

## 验证链路通了

把 Hermes 日志开到 debug,在群里 @ 一次 bot,应该看到 Honcho 插件打出解析后的 session key:

```
Honcho session key resolved: agent-main-nonebot-onebotv11-group-<群号>
```

换个群再 @ 一次,这个值应该跟着变——这就是群间记忆隔离生效的直接证据。
(冒号会被 Honcho 净化成 `-`,正常。)

## 成本

Honcho 不是装上就完事的组件,它持续烧 LLM 额度:

- **deriver**:每条消息触发一次后台提炼——按频次算是大头
- **dream**:后台"睡眠期整理",两个自主 agent 回头翻已有观察做演绎与归纳。频次低
  (攒够 50 条观察 / 闲置 60 分钟触发,两次至少隔 8 小时),但**单次最贵**——每个 agent
  最多 20 轮工具迭代、每轮读 16k 上下文。**默认开着**,`.env.example` 里建议先关
- **dialectic**:按轮次触发,一轮可能多发几次
- **summary**:会话推进到一定长度跑一次
- **embedding**:每条 observation 写入前一次,检索时每个 query 一次。`EMBED_MESSAGES`
  只管聊天消息那一路,关掉也免不了这些——**embedding 是硬依赖**。
  注意检索那一路才是大头:`session.py` 把**整条 user message** 当 `search_query` 传给
  `peer.context()`,而 reactive 模式的 user message 裹着 `<recent_messages>`,单条两三千字符。
  实测一次(`EMBED_MESSAGES=false`、580 条 observation 均 105 字符):写入侧只占 7%,
  **其余九成都是检索 query**

⚠️ chat 类模块共用全局 `LLM_OPENAI_BASE_URL`,但 **embedding 不吃这个值**,只认自己的
`EMBEDDING_MODEL_CONFIG__OVERRIDES__*`。只提供 chat-completions 的网关(如 opencode)
必须给 embedding 另配一家,否则请求会打到 `api.openai.com` 报 401。

群越活跃烧得越多,而且这些后台调用与 bot 回复用户**共用同一个额度池**。如果你的 LLM
是按额度封顶的订阅制,强烈建议给 Honcho 单配一把便宜的 key,把后台提炼和主对话分开——
否则后台把额度啃完时,表现是 bot 该说话时说不了,排查起来会绕远路。

省钱的旋钮,按性价比排:

1. **`reasoningLevelCap` 压到 `minimal` 或 `low`** —— 单次 dialectic 的输入和它带出的
   embedding query 数都是数倍差距,而且是一个 JSON 键、随时可回退。见上方「cap 是承重的」
2. **`dialecticCadence` 调稀**(`2` → `5`)—— 直接按比例减少 dialectic 次数
3. flash 档模型、`DERIVER_WORKERS=1`、`DREAM_ENABLED=false`

`EMBED_MESSAGES=false` 只省聊天消息那一路的向量,省不掉 observation 的。

如果 embedding provider 对单批条数有上限(阿里云百炼 text-embedding-v4 是 10),要显式设
`EMBEDDING_MODEL_CONFIG__MAX_BATCH_SIZE`,否则 deriver 按自己的默认批量发,服务端 400
拒绝、客户端重试三轮,纯浪费。

## 冷启动期的 dialectic 风暴

刚部署完会在 Hermes 日志里看到:

```
WARNING plugins.memory.honcho.session: Honcho dialectic query failed: Request timed out after 30.0s
```

而 Honcho 侧是一串工具调用:`search_memory` 只返回一两百字符,随后反复 `grep_messages`
/ `get_messages_by_date_range`,每次回来上万字符且 `was_truncated=true`,直到
`Tool execution loop reached max iterations` 护栏兜底。

**这是空库的正常行为,不是配错了。** `_handle_search_memory` 里有段兜底:memory 为空时
自动转去搜消息历史,免得模型误判"这里什么都没有"。于是 dialectic 在空库上把工具预算烧光。

看 `PERFORMANCE dialectic_chat` 那行能量化代价——冷库下 `low` 档很容易跑到十余次工具调用、
数万输入 token、两分钟量级。而 **Hermes 侧 30 秒就超时放弃了,Honcho 侧还在继续跑:
这些 token 白烧,没人接收结果**。按 `dialecticCadence=2` 算,活跃群里每两轮来一发;
prompt cache 也只能命中一半,未缓存的部分正是每次都不同的历史 dump。

冷启动期先压住,`honcho.json` 的 host block:

```json
"dialecticReasoningLevel": "minimal",
"reasoningLevelCap": "minimal",
"dialecticDepth": 1
```

**`reasoningLevelCap` 这行不能省。** 只设 `dialecticReasoningLevel` 而不夹 cap,heuristic 会
把它原样升回去,这段压制根本不生效 —— 见上方「cap 是承重的」。

`minimal` 档 `MAX_TOOL_ITERATIONS=1`,一次工具调用就收工,这串风暴直接消失。反正空库也没什么
可召回的。Honcho 侧 `.env` 顺手给那些 dump 封顶:

```bash
DIALECTIC_MAX_INPUT_TOKENS=32000     # 默认 100000
DIALECTIC_HISTORY_TOKEN_LIMIT=4096   # 默认 8192
```

**怎么判断收敛。** 那段兜底的触发条件是 `total_count == 0` —— 严格的"这个 peer 的
observation 库**完全为空**"(`_handle_search_memory`)。所以它是全有或全无:只要
`search_memory` 有任何一条命中,那段翻消息历史的逻辑整个跳过。你不会看到它渐变,而是某天
起那种 `len` 上万、`was_truncated=true` 的 `grep_messages` / `get_messages_by_date_range`
**突然不再出现**。判据从强到弱:

1. Honcho 日志里不再有上万字符的历史 dump,dialectic 只剩几百字符的 `search_memory` 结果 —— 最硬
2. `PERFORMANCE dialectic_chat` 行的 `tool_calls` 落到个位数低端、`total_duration` 稳定远低于 30 秒
3. 不再出现 `Tool execution loop reached max iterations`

三条要连续几天、覆盖冷群热群都成立再算数。

**怎么拆防护(一次一样,每步盯 1~2 天 `PERFORMANCE` 行)。** 分两类:

- `DIALECTIC_MAX_INPUT_TOKENS` / `DIALECTIC_HISTORY_TOKEN_LIMIT` 是给单次 dump 封顶,库满后
  本就用不到,留着零成本还能防将来某个 peer 冷启动再炸一次 —— **不必拆**。
- `dialecticDepth` 先拆(`1` → 默认),它直接乘工具调用量,收敛后放开最安全。
- `dialecticReasoningLevel` 从 `minimal` 提到 `low` **不急**:多出来的是召回深度和 token,
  而群聊记忆要的是"这个群聊过什么",`minimal` 往往够用。等真觉得 bot 记性差了再提;提完
  `total_duration` 若又逼近 30 秒,退回 `minimal`。

## 停用与重开

停用要动两侧,只关插件侧开关是不够的:

**Hermes 端(这才是真正的开关)**:`~/.hermes/config.yaml` 里把 `memory.provider` 置空
(或删掉该行),重启 gateway:

```yaml
memory:
  provider: ""        # 原来是 honcho;为空即「只用内建记忆」,外挂 provider 整块跳过
```

provider 为空时 memory provider 根本不加载,deriver / dialectic / summary / embedding
整条链路都不再触发。`memory:` 块的其余键(`memory_enabled` / `user_profile_enabled` /
`nudge_interval` / `flush_min_turns` 等)属于**内建文件记忆**(MEMORY.md / USER.md),
与 Honcho 无关,不用动——留着它给 bot 保底连续性(注意它按 profile 一份、跨群共享)。

想清理得更彻底,可以再从 `plugins.enabled` 里移除 `honcho`——`hermes honcho` 子命令随之
消失(报 `invalid choice`),这正是插件未加载的证据,不是坏了。多 profile 部署注意
逐 profile 检查各自的 `config.yaml`。

**插件侧**:bot 的 `.env` 里 `HERMES_HONCHO_ENABLED=false`(或删掉该行)。技术上这个头
发给没有 provider 的 Hermes 是 no-op,但留着会让配置撒谎,排查时误导人。

**Honcho 服务侧(可选)**:`docker compose stop`。没有调用方时留着只是闲置进程;
数据在 pgdata(`HONCHO_DATA`)里,`stop`/`start` 不影响。

验证停干净:Hermes debug 日志里不再出现 `Honcho session key resolved`,Honcho api 容器
日志不再新增 `PERFORMANCE dialectic_chat` 与 deriver 行。

**停用不删数据。** 重开就是反向操作:`plugins.enabled` 加回 `honcho`、重启 gateway、
插件侧开关打开,记忆从上次停下的地方续上。想连数据一起清才需要 `docker compose down`
加删除 pgdata(不可逆,先备份)。

### 什么时候值得重开

本插件走 api_server 路径,而这条路径不把 user_id 传给 memory provider(见「关键一步」
一节)——所有群成员在 Honcho 里折叠成同一个 peer,画像是全群的搅拌物,per-user 记忆
无从谈起。在这个前提改变之前,Honcho 对群聊场景的产出注定有限,停用没有功能损失。
重开的两个前置条件:

1. **身份**:Hermes 端能拿到 per-user 标识(上游支持入站 user 标识头,或本地补丁),
   群成员各自成 peer;
2. **原料**:进入 Honcho 的文本先剥掉 prompt 脚手架(XML 标签、结构化决策 JSON),
   否则 observation 从噪音里提炼,画像质量上不来。

两者都满足再开,才是 Honcho 该有的样子;只满足其一,大概率还是「感觉不到它存在」。

## 预期

记忆需要累积,头一两周体感不明显——这是正常的,不要在第三天下结论。

另外这个开关只隔离**记忆**。`session_search` 工具搜的是整个 state.db、不分群,
要一并堵住需在 Hermes 端的 `platform_toolsets.api_server` 里移除该工具。
