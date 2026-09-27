# System Patterns - nonebot-plugin-auto-emojimix

## 目录
- [架构概览](#架构概览)
- [源码分层](#源码分层与-peekjmdownloader-一致)
- [一、数据加载阶段 (service.py)](#一数据加载阶段-servicepy)
  - [1.1 SQLite 数据库结构](#11-sqlite-数据库结构-emojimixdb)
  - [1.2 启动加载过程](#12-启动加载过程-_load_emoji_map-方法)
  - [1.3 数据来源关系图](#13-数据来源关系图)
  - [1.4 数据结构示例](#14-数据结构示例)
  - [1.5 数据规模](#15-数据规模-实测)
- [二、正则构建阶段 (handler.py)](#二正则构建阶段-handlerpy-模块级)
  - [2.1 为什么需要 emoji 库？](#21-为什么需要-emoji-库)
  - [2.2 过滤逻辑](#22-过滤逻辑-handlerpy-第-18-24-行)
  - [2.3 降序排序原因](#23-为什么按长度降序排序)
  - [2.4 两种正则模式](#24-两种正则模式)
- [三、消息匹配阶段 (handler.py Rule)](#三消息匹配阶段-handlerpy-rule-函数)
  - [3.1 显式模式](#31-显式模式-check_emojis)
  - [3.2 自动模式](#32-自动模式-check_auto_emojis)
  - [3.3 性能分析](#33-性能分析每条消息的-rule-检查开销)
- [四、组合查找与图片获取 (service.py)](#四组合查找与图片获取阶段-servicepy)
  - [4.1 字符到编码转换](#41-字符到编码的转换-_char_to_code)
  - [4.2 组合查找](#42-组合查找-get_combo_url)
  - [4.3 图片下载](#43-图片下载-mix_emoji)
- [五、完整链路示例](#五完整链路示例)
- [六、错误处理](#六错误处理)
- [七、关键技术决策](#七关键技术决策)

---

## 架构概览
```
用户消息 → NoneBot2 事件系统 → on_message 匹配器
    → check_emojis (正则检查) → handle_emojimix
    → EmojiMixService.mix_emoji() → 返回图片/错误信息
    → UniMessage.image() 跨平台发送图片
```

## 源码分层（与 peek/jmdownloader 一致）
| 文件          | 职责                                                                  |
| ------------- | --------------------------------------------------------------------- |
| `__init__.py` | require(alconna) + 元数据声明 + `from . import handler`（注册命令）   |
| `handler.py`  | 消息匹配（正则构建、check_emojis）+ 处理函数（UniMessage 跨平台发送） |
| `service.py`  | 核心业务逻辑（EmojiMixService 单例）                                  |
| `config.py`   | Pydantic 配置模型（emojimix_explicit, emojimix_auto, emojimix_cd）    |

---

## 一、数据加载阶段 (service.py)

### 1.1 SQLite 数据库结构 (emojimix.db)
文件大小约 7.55MB，单表结构：
```sql
CREATE TABLE combos (
    code1 TEXT NOT NULL,   -- 例如 "u1f602"
    code2 TEXT NOT NULL,   -- 例如 "u1f97a"
    date  TEXT NOT NULL,   -- 例如 "20210521"
    PRIMARY KEY (code1, code2)
);
```
- **`code1`/`code2`**: emoji 编码字符串，格式为 `"u{hex}"` 或 `"u{hex}-ufe0f"`
- **`date`**: 组合图片对应的日期字符串
- 合成图片 URL: `{BASE_URL}{date}/{code1}/{code1}_{code2}.png`

### 1.2 启动加载过程 (`_load_emoji_map` 方法)

> **关键理解**: 数据库中**没有** emoji 列表，只有组合数据。`_emoji_map` 是从组合的 code1/code2 字段中**反向提取、去重**后构建出来的。

```python
# ── 步骤 1: 打开数据库（只读模式） ──
self._db = sqlite3.connect(f"file:{_DB_FILE}?mode=ro", uri=True)

# ── 步骤 2: 从所有组合中提取去重的 emoji 编码 ──
rows = self._db.execute(
    "SELECT DISTINCT code FROM ("
    "  SELECT code1 AS code FROM combos"
    "  UNION"
    "  SELECT code2 AS code FROM combos"
    ")"
).fetchall()                                   # 返回 613 行

# ── 步骤 3: 从编码字符串中提取基础码点，构建 _emoji_map ──
# _emoji_map 的作用: 让 _char_to_code 方法能把用户输入的 emoji 字符转换为数据库中的编码
for (code,) in rows:
    # code 例如 "u2639-ufe0f"
    hex_parts = code.split("-")                 # ["u2639", "ufe0f"]
    base_cp = int(hex_parts[0][1:], 16)         # "2639" → 十进制 9785 (即 0x2639)
    self._emoji_map[base_cp] = code             # 9785 → "u2639-ufe0f"
    # 注意: 只取第一段作为 Key (忽略 -ufe0f 后缀)，这样查找时自动兼容带/不带 FE0F 的输入
```

### 1.3 数据来源关系图
```
emojimix.db (SQLite)
    │
    └── combos 表 (143,274 行)
         │
         ├── 运行时按需查询 ────→  get_combo_url()     (SQL SELECT, 不占内存)
         │
         └── 启动时 DISTINCT ──→  613 个独立编码       (临时结果)
              "u2639-ufe0f"          │
              "u2615"                │
                                     └── 提取码点 ──→  self._emoji_map  (常驻内存, 613 条)
                                          9785 → "u2639-ufe0f"
                                          9749 → "u2615"
```

### 1.4 数据结构示例
```
_emoji_map = {                                   # 613 条 (从 combos 表 DISTINCT 提取)
    0x2615: "u2615",                             # ☕
    0x2639: "u2639-ufe0f",                       # ☹️ (注意带 -ufe0f 后缀)
    0x1F602: "u1f602",                           # 😂
    0x1F97A: "u1f97a",                           # 🥺
    ...
}

# _db 连接 (sqlite3.Connection, 只读) — 组合数据在磁盘，按需查询
```

### 1.5 数据规模 (实测)
| 数据             | 数量       | 存储位置 | 内存占用 |
| ---------------- | ---------- | -------- | -------- |
| `_emoji_map`     | 613 条     | 内存     | ~几十 KB |
| combos 表        | 143,274 行 | 磁盘     | 0        |
| emojimix.db 文件 | -          | 磁盘     | 7.55 MB  |

---

## 二、正则构建阶段 (handler.py 模块级)

### 2.1 为什么需要 emoji 库？
`_emoji_map` 中存储的是**编码字符串**（如 `"u1f602"`），但正则需要匹配的是**字符本身**（如 `😂`）。
- **我们的 JSON 数据**: 提供"哪些 emoji 支持合成"（613 个码点）
- **emoji 库 (`emoji.EMOJI_DATA`)**: 提供"emoji 字符长什么样"（~4000+ 个字符）
- 两者取交集，得到正则所需的字符列表

> **注意**: emoji 库 2.0+ 已移除 `get_emoji_regexp()` 方法（因为性能差且无法正确识别复杂 emoji）。
> 我们不使用它的正则功能，只使用 `EMOJI_DATA` 字典作为数据源。

### 2.2 过滤逻辑 (handler.py 第 18-24 行)
```python
# 1. 获取所有支持合成的基础码点 (来自 JSON 数据，613 个)
supported = emoji_mix_service.supported_codepoints   # set[int]

# 2. 从 emoji 库的 ~4000+ 个 emoji 中，过滤出符合条件的
emojis_list = sorted(
    (e for e in emoji.EMOJI_DATA       # 遍历 emoji 库所有条目
     if len(e) <= 2                    # 条件1: 只要 1-2 字符的简单 emoji
     and ord(e[0]) in supported),      # 条件2: 基础码点必须在我们支持的集合中
    key=len,
    reverse=True,                      # 按长度降序排序！
)

# 3. 拼接为正则 "或" 组
emoji_pattern = "(" + "|".join(re.escape(e) for e in emojis_list) + ")"
```

### 2.3 为什么按长度降序排序？
同一个 emoji 可能在 `EMOJI_DATA` 中有两种写法：
- `☹` — 1 字符 (U+2639)
- `☹️` — 2 字符 (U+2639 + U+FE0F)

正则引擎按从左到右的顺序尝试匹配 `|` 分隔的各个选项。
如果短的在前，正则会先匹配到 `☹`，剩余的 FE0F 字符会变成"多余的"导致匹配失败。
**降序排序确保 `☹️`（2字符）优先被尝试匹配**，匹配失败才回退到 `☹`（1字符）。

### 2.4 两种正则模式
```python
# 显式模式: 严格匹配 "emoji + emoji" 格式（整行匹配）
explicit_pattern = re.compile(
    rf"^\s*(?P<code1>{emoji_pattern})\s*\+\s*(?P<code2>{emoji_pattern})\s*$"
)

# 自动模式: 在文本中搜索任意两个相邻的 emoji（部分匹配）
auto_pattern = re.compile(
    rf"(?P<code1>{emoji_pattern})\s*(?P<code2>{emoji_pattern})"
)
```

---

## 三、消息匹配阶段 (handler.py Rule 函数)

### 3.1 显式模式 (`check_emojis`)
```python
async def check_emojis(state: T_State, text: str = EventPlainText()) -> bool:
    text = text.strip()
    if not text or "+" not in text:       # ← 快速短路！99.9% 的消息在这里结束
        return False
    if matched := re.match(explicit_pattern, text):
        state["code1"] = matched.group("code1")   # 提取第一个 emoji 字符
        state["code2"] = matched.group("code2")   # 提取第二个 emoji 字符
        return True
    return False
```
- **注册**: `emojimix = on_message(check_emojis, block=True)`
- **block=True**: 匹配成功后阻止后续 matcher 处理

### 3.2 自动模式 (`check_auto_emojis`)
```python
async def check_auto_emojis(state: T_State, text: str = EventPlainText()) -> bool:
    if not plugin_config.auto_emojimix:   # 配置未启用则跳过
        return False
    text = text.strip()
    if not text or "+" in text:           # 包含 "+" 则跳过，避免与显式模式冲突
        return False
    if matched := re.search(auto_pattern, text):   # 全文搜索
        state["code1"] = matched.group("code1")
        state["code2"] = matched.group("code2")
        return True
    return False
```
- **注册**: `auto_emojimix_matcher = on_message(check_auto_emojis, block=False, priority=20)`
- **block=False**: 不阻止其他 matcher
- **priority=20**: 低优先级，让其他 matcher 先处理

### 3.3 性能分析（每条消息的 Rule 检查开销）
```
用户发送普通消息 "今天天气真好"
    │
    ▼
check_emojis:
  ① text.strip()          → 纳秒级
  ② "+" not in text       → 纳秒级，99.9%的消息在这里短路返回 False ✅
  ③ re.match(pattern)     → 不会执行（被②短路）

check_auto_emojis (若启用):
  ① auto_emojimix 配置检查 → 纳秒级
  ② "+" in text           → 纳秒级
  ③ re.search(pattern)    → 对普通文本几微秒到几十微秒
```
- **显式模式**: 绝大多数消息在 `"+" not in text` 短路，几乎零开销
- **自动模式**: 需要执行 `re.search` 全文搜索，但仍在微秒级
- **结论**: Rule 检查性能消耗可忽略不计，不是瓶颈

---

## 四、组合查找与图片获取阶段 (service.py)

### 4.1 字符到编码的转换 (`_char_to_code`)
```python
def _char_to_code(self, emoji_char: str) -> Optional[str]:
    base_cp = ord(emoji_char[0])           # 😂 → 0x1F602 (取第一个字符的码点)
    return self._emoji_map.get(base_cp)    # 0x1F602 → "u1f602"
```
- 只取 `emoji_char[0]` 的码点，**自动忽略尾部的 FE0F**
- 所以 `☹` 和 `☹️` 都会映射到同一个编码 `"u2639-ufe0f"`

### 4.2 组合查找 (`get_combo_url`)
```python
def get_combo_url(self, emoji1: str, emoji2: str) -> Optional[str]:
    code1 = self._char_to_code(emoji1)     # "😂" → "u1f602"
    code2 = self._char_to_code(emoji2)     # "🥺" → "u1f97a"

    # SQL 查询同时尝试两种排列顺序
    row = self._db.execute(
        "SELECT date, code1, code2 FROM combos "
        "WHERE (code1=? AND code2=?) OR (code1=? AND code2=?) LIMIT 1",
        (code1, code2, code2, code1),
    ).fetchone()

    if row:
        date, c1, c2 = row
        return f"{_BASE_URL}{date}/{c1}/{c1}_{c2}.png"
    return None
```
生成的 URL 示例:
```
https://www.gstatic.com/android/keyboard/emojikitchen/20210521/u1f602/u1f602_u1f97a.png
```

### 4.3 图片下载 (`mix_emoji`)
```python
async def mix_emoji(self, emoji1: str, emoji2: str) -> Union[str, bytes]:
    # 1. 转换编码
    # 2. 查找 URL
    # 3. 使用 httpx 异步下载（支持 http_proxy 配置）
    async with httpx.AsyncClient(
        proxy=plugin_config.http_proxy, timeout=20
    ) as client:
        resp = await client.get(url)
        if resp.status_code == 200:
            return resp.content        # 返回 bytes (图片数据)
        return "出错了，可能不支持该emoji组合"   # 返回 str (错误信息)
```
- 返回类型 `Union[str, bytes]`: `str` 表示错误消息，`bytes` 表示图片数据
- handler 通过 `isinstance(result, str/bytes)` 区分处理

---

## 五、完整链路示例

用户发送 `😂+🥺`：
```
[handler.py] check_emojis:
  ① text = "😂+🥺"
  ② "+" in text → 不短路
  ③ re.match(explicit_pattern, "😂+🥺") → 匹配成功
  ④ state["code1"] = "😂", state["code2"] = "🥺"
  ⑤ return True → 触发 handle_emojimix

[handler.py] handle_emojimix:
  调用 emoji_mix_service.mix_emoji("😂", "🥺")

[service.py] mix_emoji:
  ① _char_to_code("😂") → ord("😂") = 0x1F602 → _emoji_map[0x1F602] → "u1f602"
  ② _char_to_code("🥺") → ord("🥺") = 0x1F97A → _emoji_map[0x1F97A] → "u1f97a"
  ③ get_combo_url: SQL 查询 code1/code2 两种排列
  ④ 找到! date="20210521", code1="u1f602", code2="u1f97a"
  ⑤ URL = "https://www.gstatic.com/android/keyboard/emojikitchen/20210521/u1f602/u1f602_u1f97a.png"
  ⑥ httpx.get(url) → 200 OK → 返回 bytes

[handler.py] handle_emojimix:
  isinstance(result, bytes) → True
  await UniMessage.image(raw=result).finish()  # 跨平台发送图片
```

---

## 六、错误处理
| 场景                       | 返回值                            | 用户看到 |
| -------------------------- | --------------------------------- | -------- |
| emoji 不在 `_emoji_map` 中 | `"不支持的emoji：{emoji}"`        | 错误文字 |
| 组合不在数据库中           | `"出错了，可能不支持该emoji组合"` | 错误文字 |
| HTTP 请求失败              | `"下载出错，请稍后再试"`          | 错误文字 |
| 自动模式下合成失败         | 不发送任何消息                    | 无感知   |

## 七、关键技术决策
1. **本地 SQLite 数据库**: 组合数据存磁盘按需查询，启动时仅加载 613 条 emoji 映射（<100KB 内存）
2. **sqlite3 标准库**: 本地主键查找 5-50μs，不阻塞事件循环，零额外依赖
3. **基础码点映射**: 统一处理带/不带 FE0F 的 emoji 变体
4. **双向组合查找**: SQL 查询同时尝试两种排列，对用户透明
5. **代理支持**: 使用标准环境变量 `HTTP_PROXY` 配置代理（httpx 自动支持，无需专用配置项）
6. **UniMessage 跨平台消息**: 使用 `nonebot-plugin-alconna` 的 `UniMessage.image()` 发送图片，自动适配各平台
7. **保留 on_message + Rule**: emoji 匹配不是传统命令，不使用 `on_alconna`，保持高效正则短路逻辑
8. **emoji 库角色**: 仅作为"字符数据源"提供 emoji 字符形式，不使用其正则功能（2.0+ 已移除）
