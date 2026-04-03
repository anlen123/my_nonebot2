# IFLOW 项目文档

## 项目概述

这是一个基于 **NoneBot 2** 框架开发的 QQ 机器人项目，提供了丰富的功能插件，包括 AI 对话、图片搜索、游戏王卡牌查询、宝可梦信息查询、Pixiv 图片获取、网页渲染、语录管理、农场游戏、泰拉瑞亚服务器互通等功能。

### 核心技术栈

- **Python**: ^3.7.3（推荐 3.11.9）
- **NoneBot 2**: ^2.0.0.a1
- **驱动**: FastAPI (nonebot.drivers.fastapi)
- **适配器**: OneBot v11 (通过 go-cqhttp 连接 QQ)
- **依赖管理**: Poetry
- **浏览器自动化**: Selenium (网页渲染)
- **图像处理**: OpenCV, PIL
- **AI 服务**: DeepSeek, Google Gemini
- **数据库**: SQLite (对话历史、农场数据)

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
├── chat_history.db        # DeepSeek 对话历史数据库
├── chat_gemini_history.db # Gemini 对话历史数据库
├── data/                  # 数据存储目录
│   ├── pokemon/           # 宝可梦数据
│   │   ├── data/          # 宝可梦 JSON 数据
│   │   ├── img/           # 宝可梦图片
│   │   └── texing/        # 宝可梦特性数据
│   ├── setu_config.json   # 色图配置
│   ├── notebook/          # 笔记本数据
│   │   ├── ban.yaml       # 禁用配置
│   │   ├── config.yaml    # 笔记本配置
│   │   └── unfinished_job.yaml # 未完成任务
│   └── custom_emote_data/ # 自定义表情数据
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
    ├── nonebot_plugin_xuanran/      # 网页渲染
    ├── nonebot_plugin_yulu/         # 语录功能
    ├── nonebot_plugin_command/      # 命令执行插件（已禁用）
    ├── nonebot_plugin_sbbot/        # 机器人保护插件
    ├── nonebot_plugin_farm/         # 真寻农场游戏插件
    └── nonebot-plugin-terralink/    # 泰拉瑞亚服务器互通插件
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
   - 配置代理服务（如需要）：`http://127.0.0.1:7890`
   - 确保安装了 FFmpeg（用于 Pixiv 动图转换）

3. **Python 版本**:
   - 推荐使用 Python 3.11.9
   - 最低要求：Python 3.7.3

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

# 或使用 Docker
docker-compose up -d
```

**Docker 部署**:
- 项目包含 `docker-compose.yml` 和 `Dockerfile` 配置
- 支持通过 Docker 容器化部署
- 容器会自动映射端口 8080 并使用 `.env.prod` 配置

### 关键配置说明

**环境变量 (.env.dev)**:
- `DRIVER`: 驱动类型（nonebot.drivers.fastapi）
- `HOST`: NoneBot 监听 IP（默认：0.0.0.0）
- `PORT`: NoneBot 监听端口（默认：8899）
- `fastapi_reload`: 是否启用热重载（默认：true）
- `apscheduler_autostart`: 是否自动启动定时任务（默认：true）
- `SUPERUSERS`: 超级用户 QQ 号列表
- `NICKNAME`: 机器人昵称
- `COMMAND_START`: 命令起始字符
- `IMGROOT`: 图片根目录路径
- `AIOHTTP`: HTTP 代理地址
- `FFMPEG`: FFmpeg 可执行文件路径
- `PIXIV_R18`: 是否启用 Pixiv R18 内容（True/False）
- `PIXIV_COOKIES`: Pixiv Cookies
- `PROXY`: 代理服务地址
- `pokemon_path`: 宝可梦数据路径
- `pokemon_img_path`: 宝可梦图片路径
- `pokemon_texing_path`: 宝可梦特性数据路径
- `PLUS_ONE_PRIORITY`: 加一功能优先级
- `PLUS_ONE_WHITE_LIST`: 加一功能白名单群号
- `FISHES`: 钓鱼功能配置（JSON 格式）
- `fishing_limit`: 钓鱼限制
- `fishing_coin_name`: 钓鱼币名称
- `special_fish_enabled`: 是否启用特殊鱼
- `special_fish_price`: 特殊鱼价格
- `special_fish_probability`: 特殊鱼出现概率
- `nonebot_plugin_masterduel_root_dir`: 游戏王插件根目录
- `nonebot_plugin_masterduel_img_dir`: 游戏王插件图片目录
- `nonebot_plugin_masterduel_img_card_dir`: 游戏王卡牌图片目录

**农场插件配置**:
- `farm_draw_quality`: 农场绘制清晰度（low/medium/high/original）
- `farm_server_url`: 农场服务器地址（默认：http://diuse.work）

**泰拉瑞亚插件配置**:
- `terralink_enabled`: 插件总开关（true/false）
- `terralink_port`: WebSocket 监听端口（默认：7778）
- `terralink_cmd_prefix`: 指令前缀（默认：/）
- `terralink_resource_path`: 游戏资源路径（TModLoader 模组导出）
- `terralink_links`: 多服务器映射列表（JSON 格式，包含 token、group_id、name）

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
- 特性：支持对话历史记录，群聊消息使用合并转发

**gemini_gpt**: Google Gemini AI 对话
- 命令：`gm <问题>` (pro 模型), `gmt <问题>` (推理模型), `gemini <问题>`
- 图片分析：`gmi`（需要上传图片，然后提出问题）
- 清除上下文：`gmclear`
- 特性：支持对话历史记录，群聊消息使用合并转发

### 2. 娱乐功能插件

**love**: 基础功能
- 命令：`ll` 或 `love` - 回复"我也爱你"
- 命令：`菜单` - 显示功能菜单（需要 @ 机器人）

**jm**: JM 漫画下载
- 命令：`jm <ID>` - 下载指定 ID 的漫画并生成 PDF 上传到群
- 权限命令：`关闭jm功能`, `开启jm功能`（仅超级用户）
- 特性：支持任务锁定、进度提示、错误重试

**nonebot_plugin_farm**: 真寻农场游戏（基于真寻小插件移植）
- 首次开通：`@机器人 开通农场`
- 查询功能：`我的农场`, `农场详述`, `我的农场币`, `我的种子`, `我的作物`
- 商店功能：`种子商店 [筛选关键字] [页数]`, `购买种子 [种子名称] [数量]`
- 种植功能：`播种 [种子名称] [数量]`, `收获`, `铲除`, `土地升级 [地块ID]`
- 交易功能：`出售作物 [作物名称] [数量]`, `偷菜 @玩家名`
- 其他功能：`更改农场名 [新名称]`, `农场签到`
- 特性：支持土地升级、签到、在线更新作物信息、图片化农场展示

### 3. 查询功能插件

**nonebot_plugin_masterduel**: 游戏王卡牌查询
- 命令：`ygo <卡名>` - 查询游戏王卡牌信息
- 命令：`ck <卡名>` - 查询卡牌详情

**nonebot_plugin_biliav**: B站视频信息
- 功能：自动识别消息中的 AV 号或 BV 号，查询视频信息

**nonebot_plugin_picsearcher**: 图片搜索
- 命令：`搜图` - 上传图片进行搜索（支持多种搜索引擎）
- 搜索引擎：SauceNAO、IQDB、Ascii2D、Trace、Yandex

**nonebot_plugin_pixiv**: Pixiv 图片获取
- 命令：`pixiv <PID>` - 获取 Pixiv 插画
- 自动识别：支持自动识别 Pixiv URL
- 排行榜：`pixivRank <时间>` - 获取排行榜（1:日榜, 7:周榜, 30:月榜）
- 特性：支持动图、R18 内容过滤、图片压缩、合并转发

### 4. 其他功能插件

**nonebot_plugin_xuanran**: 网页渲染
- 命令：`xr <URL>` - 将网页渲染为图片
- 技术栈：Selenium + Chrome Headless

**nonebot_plugin_yulu**: 语录功能
- 命令：`yl`, `yulu`, `语录`, `来点语录` - 随机发送语录
- 命令：`上传语录` - 上传图片到语录库
- 特性：每个群独立的语录库，支持递归目录

**nonebot_plugin_sbbot**: 机器人保护
- 功能：自动识别并反击侮辱机器人的消息（将侮辱词替换为 @ 发送者）
- 触发词：傻逼、sb、煞笔、傻B、沙比、笨b、笨逼、沙笔
- 触发对象：机器人、qqbot、bot、群主

**nonebot-plugin-terralink**: 泰拉瑞亚 TModLoader 服务器互通
- 管理指令（仅 SuperUser）：
  - `/boss` - 查看世界 Boss 击杀进度
  - `/buff <玩家/all> <Buff名> [秒数]` - 给予 Buff
  - `/butcher` - 清理敌对生物
  - `/give <玩家> <物品名> [数量]` - 给予物品
  - `/kick <玩家名> [原因]` - 踢出玩家
  - `/save` - 保存世界存档
  - `/settle` - 沉降液体
  - `/time [dawn/noon/dusk/midnight]` - 查询/修改时间
- 查询指令（所有用户）：
  - `/help` - 显示帮助菜单
  - `/inv <玩家名>` - 查看玩家背包
  - `/list` - 查看在线玩家列表
  - `/query <物品名或ID>` - 查询物品详情
  - `/recipe <物品名或ID>` - 生成合成树图片
  - `/search <关键词>` - 模糊搜索物品
  - `/tps` - 查看服务器性能状态
- 特性：双向通信、富文本渲染、多服务器支持、Token 认证

### 5. 第三方插件（通过 pip 安装）

**nonebot_plugin_ygo**: 游戏王相关功能
**nonebot_plugin_apscheduler**: 定时任务调度器
**nonebot_plugin_abbrreply**: 缩写回复
**nonebot_plugin_plus_one**: 加一功能
**nonebot_plugin_navicat**: 数据库工具
**nonebot_plugin_fishing**: 钓鱼游戏

## 开发约定

### 插件开发规范

1. **插件结构**:
   - 每个插件应包含 `__init__.py` 作为主入口
   - 配置文件应命名为 `config.py`
   - 数据源文件应命名为 `data_source.py`

2. **事件处理**:
   - 使用 `on_command`, `on_regex`, `on_keyword`, `on_message` 等装饰器注册事件处理器
   - 群聊消息使用 `GroupMessageEvent`，私聊消息使用 `PrivateMessageEvent`
   - 合并转发消息使用 `send_forward_msg_group` 函数

3. **数据库使用**:
   - 对话历史使用 SQLite 存储
   - 初始化数据库时使用 `init_db()` 函数
   - 使用 `get_conversation_history()` 和 `update_conversation_history()` 管理历史记录

4. **错误处理**:
   - 捕获 `ActionFailed` 异常处理风控情况
   - 添加适当的错误提示和重试机制
   - 使用锁文件防止并发冲突（如 JM 插件）

5. **消息处理**:
   - 群聊长消息使用合并转发（Forward Message）避免风控
   - 图片超过 10MB 自动进行压缩
   - 使用代理服务处理网络请求

### 代码风格

- 使用 Python 类型注解
- 异步函数使用 `async/await`
- 导入顺序：标准库 → 第三方库 → 本地模块
- 函数命名使用蛇形命名法（snake_case）
- 使用 `nonebot.get_driver().config` 获取全局配置

### 测试

- 使用 `nonebot-plugin-test` 进行插件测试
- TODO: 添加具体的测试命令

## 数据库

项目使用 SQLite 数据库存储对话历史和游戏数据：

- `chat_history.db`: DeepSeek 对话历史
- `chat_gemini_history.db`: Gemini 对话历史
- `farm_data.db`: 农场插件数据（由插件自动创建）

对话历史数据库表结构：
```sql
CREATE TABLE conversations (
    user_id TEXT PRIMARY KEY,
    messages TEXT
)
```

## 外部依赖服务

- **go-cqhttp**: QQ 协议连接服务（本地运行，端口 5700）
- **签名服务器**: go-cqhttp 签名服务（http://127.0.0.1:7701）
- **代理服务**: HTTP 代理（http://127.0.0.1:7890）
- **Chrome**: 网页渲染所需的浏览器
- **FFmpeg**: 视频处理工具（用于 Pixiv 动图转换）
- **TModLoader 服务器**: 泰拉瑞亚模组服务器（需安装 TerraNoneBridge 模组）
- **农场服务器**: 农场插件数据服务器（http://diuse.work）

## 常见问题

### 机器人无法启动

1. 检查 go-cqhttp 是否正常运行
2. 检查端口配置（NoneBot: 8899, go-cqhttp: 5700）
3. 检查环境变量配置是否正确
4. 检查 FFmpeg 路径是否正确
5. 检查 Python 版本是否符合要求（推荐 3.11.9）

### 插件无法加载

1. 检查 `bot.py` 中是否正确加载了插件
2. 检查插件目录结构是否正确
3. 查看错误日志获取详细信息
4. 检查插件依赖是否已安装

### AI 对话失败

1. 检查 API Key 配置
2. 检查网络连接（可能需要代理）
3. 检查 API 服务是否可用
4. 检查是否触发风控（尝试使用私聊）

### Pixiv 图片获取失败

1. 检查 Cookies 配置是否有效
2. 检查代理服务是否正常
3. 检查 R18 设置是否符合群组权限
4. 检查账号是否被风控

### JM 漫画下载失败

1. 检查 ID 格式是否正确
2. 检查是否有正在进行的下载任务
3. 检查磁盘空间是否充足
4. 检查 JM 配置文件是否正确

### 农场插件问题

1. 无法开通农场：确保使用 `@机器人 开通农场` 格式
2. 种子购买失败：检查农场币是否充足
3. 作物生长异常：检查服务器连接是否正常
4. 图片显示异常：调整 `farm_draw_quality` 配置

### 泰拉瑞亚插件问题

1. 无法连接服务器：检查 `terralink_enabled` 是否启用，端口配置是否正确
2. 资源加载失败：确保 `terralink_resource_path` 指向正确的游戏资源目录
3. 指令无响应：检查 `terralink_cmd_prefix` 配置和服务器 Token 是否匹配
4. 图片显示异常：检查资源文件是否完整导出

### Docker 部署问题

1. 容器无法启动：检查 Docker 服务是否运行
2. 端口冲突：修改 `docker-compose.yml` 中的端口映射
3. 配置文件未加载：确保 `.env.prod` 文件存在且配置正确
4. 权限问题：检查容器文件挂载权限

## 贡献指南

1. Fork 项目
2. 创建功能分支
3. 提交更改
4. 推送到分支
5. 创建 Pull Request

## 许可证

本项目遵循相关开源许可证。详见 LICENSE 文件。

### 插件许可证

- **nonebot_plugin_farm**: GPL-3.0-or-later
- **nonebot-plugin-terralink**: MIT License
- **nonebot_plugin_picsearcher**: 详见插件 README
- 其他插件遵循各自的许可证

## 联系方式

- 项目地址：https://github.com/anlen123/my_nonebot2
- 问题反馈：通过 GitHub Issues

## 更新日志

### 2026-02-04

- 新增 `nonebot_plugin_farm` 插件（真寻农场游戏）
- 新增 `nonebot-plugin-terralink` 插件（泰拉瑞亚服务器互通）
- 更新 Python 版本推荐至 3.11.9
- 添加 Docker 部署支持
- 优化文档结构，更新配置说明
- 添加农场和泰拉瑞亚插件的详细功能说明
- 更新常见问题解答
- 移除宝可梦插件（`nonebot_plugin_pokemon`）

### 2024-12-17

- 更新 Pixiv Cookies 配置
- 优化钓鱼游戏配置
- 添加游戏王卡牌图片目录配置