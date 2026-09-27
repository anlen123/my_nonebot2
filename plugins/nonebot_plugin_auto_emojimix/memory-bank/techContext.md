# Tech Context - nonebot-plugin-auto-emojimix

## 技术栈
- **Python**: 3.11+
- **框架**: NoneBot2 (>=2.4.2)
- **消息层**: nonebot-plugin-alconna (>=0.59.0) — 提供 `UniMessage` 跨平台消息发送
- **HTTP 客户端**: httpx (>=0.27.0)
- **emoji 库**: emoji (>=2.0.0) — 提供 `emoji.EMOJI_DATA` 字典，包含所有 Unicode emoji 字符数据
- **数据存储**: sqlite3 (标准库) — 只读查询，零额外依赖
- **冷却限制**: cachetools (>=5.0.0) — TTLCache 实现用户级冷却

## 关键架构决策：Alconna 的使用方式
- **不使用 `on_alconna`**: 本插件的 emoji 匹配不是传统命令（无前缀、子命令、选项），保留 `on_message + Rule` 正则匹配
- **仅使用 `UniMessage`**: 通过 `nonebot-plugin-alconna` 的通用消息组件实现跨平台消息发送
- **不限制适配器**: `supported_adapters=None`，由 UniMessage 自动适配各平台
- **require 机制**: 通过 `nonebot.require("nonebot_plugin_alconna")` 确保插件加载顺序

## 开发工具
- **包管理器**: uv
- **代码检查**: ruff (行长度88, LF行尾)
- **类型检查**: basedpyright (standard 模式)
- **测试**: pytest + nonebug + pytest-asyncio
- **任务运行器**: just (justfile)
- **版本管理**: commitizen + git-cliff
- **Pre-commit**: prek

## 项目结构
```
nonebot-plugin-auto-emojimix/
├── src/nonebot_plugin_auto_emojimix/
│   ├── __init__.py                 # 插件入口（require + 元数据 + handler 导入）
│   ├── config.py                   # 配置模型（emojimix_explicit, emojimix_auto, emojimix_cd）
│   ├── handler.py                  # 消息匹配 + 处理函数（UniMessage 发送）
│   ├── service.py                  # 核心业务逻辑（EmojiMixService + sqlite3）
│   └── emojimix.db                 # emoji 组合数据库（SQLite, 7.55MB, 14 万组合）
├── scripts/                        # 开发辅助脚本（不随包发布）
│   ├── update_emoji_data.py        # 数据更新脚本（输出 .db 文件）
│   └── metadata_hash.txt           # 上游数据哈希（增量更新用）
├── tests/
│   ├── conftest.py                 # NoneBot 初始化 + 加载适配器 + 插件
│   ├── fake.py                     # 虚拟事件工厂
│   ├── plugin_test.py              # 插件集成测试（元数据、handler、config、service）
│   └── units/
│       ├── conftest.py             # importlib 直接加载，mock nonebot 依赖
│       └── test_service.py         # service.py 纯逻辑单元测试（23个）
├── memory-bank/                    # 记忆库文档
├── pyproject.toml                  # 项目配置
├── justfile                        # 任务定义
└── .env.test                       # 测试环境变量
```

## 关键依赖
| 依赖                   | 版本                | 用途                  |
| ---------------------- | ------------------- | --------------------- |
| nonebot2               | >=2.4.2,<3          | 机器人框架            |
| nonebot-plugin-alconna | >=0.59.0            | UniMessage 跨平台消息 |
| httpx                  | >=0.27.0,<1         | HTTP 异步客户端       |
| emoji                  | >=2.0.0             | emoji 字符数据库      |
| cachetools             | >=5.0.0,<6          | TTLCache 用户冷却     |
| sqlite3                | 标准库              | 组合数据查询          |
| nonebug                | >=0.4.3 (test)      | NoneBot 测试工具      |
| pytest-asyncio         | >=1.3.0,<1.4 (test) | 异步测试支持          |

## 测试架构
采用与 peek / jmdownloader 一致的双层测试模式：
- **集成测试** (`tests/plugin_test.py`): 通过 NoneBot 完整初始化后测试插件加载
- **单元测试** (`tests/units/`): 使用 importlib 直接加载模块，mock nonebot 依赖

### 测试配置
- `asyncio_mode = "auto"`: 自动检测异步测试
- `asyncio_default_fixture_loop_scope = "session"`: session 级事件循环
- `--import-mode=importlib`: 使用 importlib 导入

## Unicode FE0F 变体选择符
**U+FE0F (Variation Selector-16)** 是一个不可见的 Unicode 字符，用于请求 emoji 样式呈现：
- `☹` (U+2639) → 可能显示为文本样式
- `☹️` (U+2639 U+FE0F) → 显示为彩色 emoji 样式

本插件通过 `_char_to_code` 方法只取 `ord(emoji_char[0])` 基础码点，忽略尾部 FE0F，
确保 `☹` 和 `☹️` 都能正确映射到同一个组合编码 (`u2639-ufe0f`)。

## 数据更新工具 (scripts/update_emoji_data.py)

本地运行的脚本，用于检查和更新 emoji 组合数据。

### 数据来源
上游: [xsalazar/emoji-kitchen-backend](https://github.com/xsalazar/emoji-kitchen-backend) 的 `metadata.json`，
包含 Google Emoji Kitchen 所有组合的 gStaticUrl 列表（约 30 万个 URL）。

### 运行方式
```bash
uv run python scripts/update_emoji_data.py
```

### 行为流程
1. **下载** 上游 `metadata.json`（使用 httpx 同步客户端）
2. **比较哈希** — 计算 SHA-256，与 `scripts/metadata_hash.txt` 中的旧值对比
   - 相同 → 打印"无更新" → 退出
   - 不同 → 更新哈希文件 → 继续
3. **提取 URL** — 递归查找所有 `gStaticUrl` 字段
4. **处理数据** — 按组合名去重（同名保留最新日期）、提取 (code1, code2, date) 元组
5. **写入数据库** — 构建 SQLite .db 文件，写入 `src/nonebot_plugin_auto_emojimix/emojimix.db`

### 文件作用
- `scripts/metadata_hash.txt`: 存储上次处理的上游数据 SHA-256 哈希，用于增量更新判断
- 脚本在 `scripts/` 目录中，不在包路径内，**不会被打包到 PyPI**
- 使用 httpx（项目已有依赖），无需额外安装
