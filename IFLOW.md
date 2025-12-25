# IFLOW 项目文档

## 项目概述

这是一个基于 **NoneBot 2** 框架开发的 QQ 机器人项目，提供了丰富的功能插件，包括 AI 对话、图片搜索、游戏王卡牌查询、宝可梦信息查询、Pixiv 图片获取等功能。

### 核心技术栈

- **Python**: ^3.7.3
- **NoneBot 2**: ^2.0.0.a1
- **驱动**: FastAPI (nonebot.drivers.fastapi)
- **适配器**: OneBot v11 (通过 go-cqhttp 连接 QQ)
- **依赖管理**: Poetry

### 架构说明

项目采用插件化架构，所有功能模块都位于 `plugins/` 目录下。每个插件都是一个独立的模块，可以单独加载和卸载。

## 项目结构

```
D:\nb2\my_nonebot2\
├── bot.py                 # 主程序入口，负责加载插件和启动机器人
├── config.yml             # go-cqhttp 配置文件（QQ 协议连接配置）
├── pyproject.toml         # Poetry 项目配置和依赖管理
├── .env.dev               # 开发环境配置文件
├── .env.prod              # 生产环境配置文件
├── data/                  # 数据存储目录
│   ├── pokemon/           # 宝可梦数据
│   │   ├── data/          # 宝可梦 JSON 数据
│   │   ├── img/           # 宝可梦图片
│   │   └── texing/        # 宝可梦特性数据
│   ├── setu_config.json   # 色图配置
│   └── notebook/          # 笔记本数据
└── plugins/               # 插件目录
    ├── chat_gpt/          # ChatGPT 对话插件
    ├── deepseek_gpt/      # DeepSeek AI 对话插件
    ├── gemini_gpt/        # Google Gemini AI 对话插件
    ├── jm/                # JM 漫画下载插件
    ├── love/              # 基础功能插件（菜单、love等）
    ├── nonebot_plugin_biliav/      # B站视频信息查询
    ├── nonebot_plugin_masterduel/   # 游戏王大师决斗卡牌查询
    ├── nonebot_plugin_pixiv/        # Pixiv 图片获取
    ├── nonebot_plugin_picsearcher/  # 图片搜索
    ├── nonebot_plugin_pokemon/      # 宝可梦信息查询
    ├── nonebot_plugin_xuanran/      # 网页渲染
    └── nonebot_plugin_yulu/         # 语录功能
```

## 构建和运行

### 环境准备

1. **安装依赖**:
   ```bash
   poetry install
   ```

2. **配置环境**:
   - 复制 `.env.dev` 或 `.env.prod` 并根据需要修改配置
   - 确保 go-cqhttp 已正确配置并运行（监听地址：`0.0.0.0:5700`）

### 启动命令

**开发环境**:
```bash
# 使用 Poetry 运行
poetry run python bot.py

# 或使用 nb-cli
nb run
```

**生产环境**:
```bash
# 使用 Poetry 运行
poetry run python bot.py
```

### 关键配置说明

**环境变量 (.env.dev)**:
- `HOST`: NoneBot 监听 IP（默认：0.0.0.0）
- `PORT`: NoneBot 监听端口（默认：8899）
- `SUPERUSERS`: 超级用户 QQ 号列表
- `NICKNAME`: 机器人昵称
- `COMMAND_START`: 命令起始字符

**go-cqhttp 配置 (config.yml)**:
- `account.uin`: QQ 账号
- `account.password`: QQ 密码（或扫码登录）
- `account.sign-server`: 签名服务器地址
- `servers.http.address`: HTTP 监听地址（默认：0.0.0.0:5700）

## 插件功能说明

### 1. AI 对话插件

**chat_gpt**: ChatGPT 对话（GPT-3.5/GPT-4）
- 命令：`gpt3 <问题>`, `gpt4 <问题>`, `chat <问题>`, `chat4 <问题>`
- 清除上下文：`clear`

**deepseek_gpt**: DeepSeek AI 对话
- 命令：`ds3 <问题>` (v3 模型), `dsr <问题>` (R1 推理模型)
- 翻译：`翻译 <内容>` 或 `fy <内容>`
- 清除上下文：`dsclear`

**gemini_gpt**: Google Gemini AI 对话
- 命令：`gm <问题>` (pro 模型), `gmt <问题>` (推理模型)
- 图片分析：`gmi`（需要上传图片）
- 清除上下文：`gmclear`

### 2. 娱乐功能插件

**love**: 基础功能
- 命令：`ll` 或 `love` - 回复"我也爱你"
- 命令：`菜单` - 显示功能菜单（需要 @ 机器人）

**jm**: JM 漫画下载
- 命令：`jm <ID>` - 下载指定 ID 的漫画并生成 PDF 上传到群
- 权限命令：`关闭jm功能`, `开启jm功能`（仅超级用户）

### 3. 查询功能插件

**nonebot_plugin_masterduel**: 游戏王卡牌查询
- 命令：`ygo <卡名>` - 查询游戏王卡牌信息
- 命令：`ck <卡名>` - 查询卡牌详情

**nonebot_plugin_pokemon**: 宝可梦信息查询
- 根据配置的路径读取宝可梦数据

**nonebot_plugin_biliav**: B站视频信息
- 输入 AV 号或 BV 号查询视频信息

**nonebot_plugin_picsearcher**: 图片搜索
- 命令：`搜图` - 搜索图片

**nonebot_plugin_pixiv**: Pixiv 图片获取
- 命令：`pixiv <PID>` - 获取 Pixiv 插画

### 4. 其他功能插件

**nonebot_plugin_xuanran**: 网页渲染
- 命令：`xr <URL>` - 将网页渲染为图片

**nonebot_plugin_yulu**: 语录功能
- 命令：`yl` - 发送语录

## 开发约定

### 插件开发规范

1. **插件结构**:
   - 每个插件应包含 `__init__.py` 作为主入口
   - 配置文件应命名为 `config.py`
   - 数据源文件应命名为 `data_source.py`

2. **事件处理**:
   - 使用 `on_command`, `on_regex`, `on_keyword` 等装饰器注册事件处理器
   - 群聊消息使用 `GroupMessageEvent`，私聊消息使用 `PrivateMessageEvent`
   - 合并转发消息使用 `send_forward_msg_group` 函数

3. **数据库使用**:
   - 对话历史使用 SQLite 存储
   - 初始化数据库时使用 `init_db()` 函数
   - 使用 `get_conversation_history()` 和 `update_conversation_history()` 管理历史记录

4. **错误处理**:
   - 捕获 `ActionFailed` 异常处理风控情况
   - 添加适当的错误提示和重试机制

### 代码风格

- 使用 Python 类型注解
- 异步函数使用 `async/await`
- 导入顺序：标准库 → 第三方库 → 本地模块
- 函数命名使用蛇形命名法（snake_case）

### 测试

- 使用 `nonebot-plugin-test` 进行插件测试
- TODO: 添加具体的测试命令

## 数据库

项目使用 SQLite 数据库存储对话历史：

- `chat_history.db`: ChatGPT 对话历史
- `chat_gemini_history.db`: Gemini 对话历史

数据库表结构：
```sql
CREATE TABLE conversations (
    user_id TEXT PRIMARY KEY,
    messages TEXT
)
```

## 外部依赖服务

- **go-cqhttp**: QQ 协议连接服务（本地运行）
- **Meilisearch**: 搜索引擎服务（本地运行，端口 7700）
- **签名服务器**: go-cqhttp 签名服务（http://127.0.0.1:7701）
- **代理服务**: HTTP 代理（http://127.0.0.1:7890）

## 常见问题

### 机器人无法启动

1. 检查 go-cqhttp 是否正常运行
2. 检查端口配置（NoneBot: 8899, go-cqhttp: 5700）
3. 检查环境变量配置是否正确

### 插件无法加载

1. 检查 `bot.py` 中是否正确加载了插件
2. 检查插件目录结构是否正确
3. 查看错误日志获取详细信息

### AI 对话失败

1. 检查 API Key 配置
2. 检查网络连接（可能需要代理）
3. 检查 API 服务是否可用

## 贡献指南

1. Fork 项目
2. 创建功能分支
3. 提交更改
4. 推送到分支
5. 创建 Pull Request

## 许可证

本项目遵循相关开源许可证。详见 LICENSE 文件。

## 联系方式

- 项目地址：https://github.com/anlen123/my_nonebot2
- 问题反馈：通过 GitHub Issues