# my_nonebot2

一个常驻在 Windows Server 2019 上的 QQ 机器人：**nonebot2 + OneBot v11**，通过 NapCat 接入 QQ。
插件以「功能小而独立」为原则，全部代码在本仓库内，不依赖任何私有服务。

## 运行

```bash
python bot.py
```

插件清单写在 `bot.py` 的 `PLUGINS` 元组里，**加载顺序会影响同优先级匹配器的裁决**，
调整顺序前请先确认没有重叠的触发词。配置读 `.env` → `.env.<environment>`（当前 `environment=dev`）。

依赖：Python 3.13+、nonebot2 2.4.x、nonebot-adapter-onebot、nonebot-plugin-apscheduler，
以及 pill、httpx、aiohttp 等各插件自用库。本机不再需要 Playwright —— 所有网页截图都走云端渲染。

## 目录结构

```
bot.py                  启动入口：初始化 nonebot、挂 OneBot v11 适配器、按清单加载插件
.env / .env.dev         配置（.env 只放 environment，其余放 .env.dev）
plugins/common/         跨插件复用的工具包（不是 nonebot 插件，不会被自动加载）
plugins/<插件名>/        各功能插件
data/                   运行期数据（备忘录、早报历史等）
```

## 插件一览

| 插件 | 触发方式 | 说明 |
| --- | --- | --- |
| `bazaardb` | `巴扎 <关键词>` / `巴扎别名 …` | 查 The Bazaar 卡牌，数据取自 [bazaar-cards.com](https://bazaar-cards.com/)，附卡片页截图 |
| `nonebot_plugin_xuanran` | `xr <网址>` | 网页截图，渲染在云端完成，本机不需要浏览器 |
| `note` | `note add/rm/list` | QQ 备忘录，按间隔定时推送 |
| `jev_judge` | `判断 <描述>` | 调 typesafe.ai 判断描述是否成立 |
| `deepseek_balance` | `/ai余额` 等 | 各站外 AI 供应商余额查询与定时播报 |
| `seed_analyzer` | `验车` / `种子分析` / `种子信息` / `种子详情` | 种子信息分析 |
| `nonebot_plugin_masterduel` | `游戏王功能` / `查卡` / `ck` 等 | 游戏王卡查 |
| `nonebot_plugin_pixiv` | `pixiv …` / Pixiv 链接 | Pixiv 相关 |
| `nonebot_plugin_picsearcher` | `搜图` | 以图搜图 |
| `nonebot_plugin_auto_emojimix` | 表情组合 | Emoji 合成 |
| `nonebot_plugin_yulu` | `语录` / `yulu` / `来点语录` | 语录收录与抽取 |
| `nonebot_plugin_biliav` | 发 BV/av 号 | 自动解析 B 站视频 |
| `nonebot_plugin_sbbot` | 触发式 | 复读机式互动 |
| `repeater`、`welcome`、`love`、`auto_message` | 消息 / 定时 | 复读、入群欢迎、定时群发 |
| `ai_news`、`bilibili_live`、`bilibili_video` | 定时 / 自动 | AI 资讯早报、直播开播提醒、UP 主更新提醒 |

## 配置要点

配置项全部放在 `.env.dev`，文件内按插件分段并有注释。几个容易踩坑的：

- `CLOUDSHOT_PROXY`：调用云端渲染服务时走的本地代理，留空或填 `direct` 表示直连。
- `COMMAND_START`：影响命令前缀识别；用 `on_regex` 的插件不受它影响，用 `on_command` 的会受影响。
- 巴扎的卡牌库缓存落在 `plugins/bazaardb/cache/`，过期自动重拉，已加入 `.gitignore`。

## 安全提醒

`.env.dev`、`.env.prod` 目前**已被 git 跟踪**，其中有真实密钥（API key、QQ 密码等）。
推送到公开远端前务必先 `git rm --cached .env.dev .env.prod`，并轮换其中所有密钥。
