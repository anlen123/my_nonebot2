<div align="center">

# my_nonebot2

_✨ 基于 [NoneBot2](https://nonebot.dev/) + [OneBot v11](https://github.com/botuniverse/onebot-11) 的 QQ 机器人 ✨_

</div>

## 📖 简介

一个面向游戏群(泰拉瑞亚 / 游戏王)的多功能 QQ 机器人,提供 AI 对话、游戏查询、B 站监控、图片获取、群互动等功能。

- **框架**:NoneBot2 + FastAPI 驱动 + OneBot v11 适配器
- **连接**:通过 go-cqhttp 协议桥接 QQ
- **Python**:3.10+(推荐 3.11)

## ✨ 功能一览

### 当前启用插件

| 插件 | 命令 | 功能 |
|------|------|------|
| `nonebot_plugin_love` | `ll` / `love` / `菜单` | 基础命令 + **个人插件模板**(含定时任务、消息读取示例) |
| `nonebot_plugin_bazaardb` | `巴扎` / `巴扎查分` / `巴扎绑定` / `巴扎排名` | BazaarDB 物品/怪物查询、用户排位分数查询、每日排名推送 |
| `nonebot_plugin_masterduel` | `ck` / `查卡` / `lck` / `卡组码` / `裁定` / `卡包` / `别名` | 游戏王卡牌查询(本地数据库) |
| `nonebot_plugin_pixiv` | `pixiv <PID>` / `pixivRank` | Pixiv 图片获取(需 cookie,支持 R18) |
| `nonebot_plugin_xuanran` | `xr <URL>` | Selenium 网页渲染成图片 |
| `nonebot_plugin_bilibili_live` | — | B 站直播监控:开播/下播通知、弹幕排行、词云、每小时播报 |
| `nonebot_plugin_bilibili_video` | — | B 站新视频投稿推送 |
| `nonebot_plugin_biliav` | 自动识别 | 群内发 av/BV 号自动返回视频信息 |
| `nonebot_plugin_yulu` | `语录` / `上传语录` | 语录收集与发送 |
| `nonebot_plugin_keyword_image` | 关键词触发 | 群消息命中关键词时随机发图 |
| `nonebot_plugin_repeater` | 自动 | 复读机(文字/图片/表情,可配阈值) |
| `nonebot_plugin_sbbot` | 自动 | 被骂"傻逼机器人"时反击 |
| `nonebot_plugin_welcome` | 自动 | 新成员入群欢迎(@+图文) |
| `nonebot_plugin_auto_message` | 自动 | 定时自动发消息 |
| `nonebot_plugin_plus_one` | `+1` | 复读姬+1(白名单群,pip 安装) |
| `nonebot_plugin_ygo` | `ygo` | 游戏王卡查(pip 安装) |
| `nonebot_plugin_abbrreply` | 自动 | 缩写回复(pip 安装) |
| `nonebot_plugin_navicat` | — | 小工具(pip 安装) |

### 停用插件(保留未加载)

| 插件 | 说明 |
|------|------|
| `nonebot_plugin_deepseek_gpt` | `ds3` / `dsr` / `翻译` AI 对话(字节火山引擎,需 API Key) |
| `nonebot_plugin_command` | `cmd` / `内存` 执行系统命令(有风险,Windows 上停用) |
| `nonebot_plugin_farm` | 农场游戏(空目录) |

## 🚀 快速开始

### 1. 环境准备

```bash
# 安装依赖(项目使用 Poetry 管理)
poetry install
# 或直接 pip 安装核心依赖(见 pyproject.toml [tool.poetry.dependencies])
pip install "nonebot2[fastapi]" "nonebot-adapter-onebot>=2.0.0-beta.1" \
    nonebot-plugin-apscheduler pillow opencv-python

# 安装 go-cqhttp 并配置 config.yml(OneBot v11 反向 WebSocket 指向本机 8899 端口)
```

### 2. 配置环境变量

```bash
cp .env.dev .env   # 开发环境(Windows)
# 或
cp .env.prod .env  # 生产环境(Linux + Redis)
```

关键配置项:

| 配置 | 说明 |
|------|------|
| `SUPERUSERS` | 超级用户 QQ 号 |
| `NICKNAME` | 机器人昵称 |
| `COMMAND_START` | 命令起始字符(为空则命令无需前缀) |
| `IMGROOT` | 图片根目录 |
| `PIXIV_COOKIES` | Pixiv Cookie(私聊/群内获取 pixiv 图需要) |
| `BILIBILI_LIVE_UIDS` / `BILIBILI_VIDEO_UIDS` | B 站监控的 uid → 推送群映射 |
| `AUTO_MESSAGE_TASKS` | 定时消息任务列表 |
| `WELCOME_CONFIG` | 入群欢迎语配置(按群号) |
| `KEYWORD_IMAGE_RULES` | 关键词发图规则 |
| `PLUS_ONE_WHITE_LIST` | +1 复读白名单群 |
| `REPEATER_THRESHOLD` | 复读触发次数(默认 3) |

### 3. 启动

```bash
# 开发(前台)
poetry run python bot.py

# 生产(后台,tmux)
bash run.sh

# 停止
bash kill.sh
```

## 📁 项目结构

```
├── bot.py                    # 入口:初始化 + 加载插件
├── config.yml                # go-cqhttp 配置
├── .env.dev / .env.prod      # 开发 / 生产环境配置
├── plugins/                  # 本地插件(全部 nonebot_plugin_ 前缀)
│   ├── nonebot_plugin_love/  # 个人插件模板 ★ 新插件从这里复制
│   ├── nonebot_plugin_bazaardb/
│   ├── nonebot_plugin_bilibili_live/
│   └── ...
├── data/                     # 数据目录(pokemon 图鉴、表情等)
├── run.sh / kill.sh          # 生产启停脚本(tmux)
├── monitor.py                # 进程守护脚本(配合 cron)
└── docker-compose.yml        # Docker 部署(可选)
```

## 🧩 插件开发

`plugins/nonebot_plugin_love/` 是**个人插件模板**,已集成:

- 定时任务(APScheduler):装饰器方式 + 动态注册方式
- 消息读取:解析消息段、按 message_id 读取任意消息(含机器人自己发的)、发送后撤回
- 合并转发消息工具

新插件流程:

```bash
cp -r plugins/nonebot_plugin_love plugins/nonebot_plugin_xxx
# 1. 修改 docstring 和章节内容
# 2. bot.py 中加载:
#    nonebot.load_plugin("plugins.nonebot_plugin_xxx")
```

插件命名规范:`plugins/nonebot_plugin_xxx/__init__.py`。

## 🛠 生产部署

- **进程守护**:`monitor.py` 检测 bot 进程,异常时自动 `kill.sh` + `run.sh` 重启,配合 crontab 每 5 分钟执行
- **Docker**:`docker-compose.yml`(映射 8080 端口,需 `.env.prod`)
- **外部依赖**:go-cqhttp(QQ 桥接)、Redis(生产)、ffmpeg(视频处理)

## ⚠️ 注意事项

- `.env.*` 和 `config.yml` 含敏感信息(cookie / 密码 / QQ 号),**不要提交到公开仓库**;若已泄露请立即轮换
- `nonebot_plugin_pixiv` / `nonebot_plugin_masterduel` 来自上游开源项目,如需同步上游请先还原格式化差异
- 代码风格统一使用 [ruff](https://github.com/astral-sh/ruff)(black 风格 + import 排序):
  ```bash
  ruff format bot.py demo.py plugins
  ruff check --select I --fix bot.py demo.py plugins
  ```

## 📄 License

MIT
