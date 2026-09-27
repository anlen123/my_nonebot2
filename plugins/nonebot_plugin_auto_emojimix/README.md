<div align="center">
    <a href="https://v2.nonebot.dev/store">
    <img src="https://github.com/Misty02600/nonebot-plugin-template/releases/download/assets/NoneBotPlugin.png" width="310" alt="logo"></a>

## ✨ nonebot-plugin-auto-emojimix ✨
[![LICENSE](https://img.shields.io/github/license/Misty02600/nonebot-plugin-auto-emojimix.svg)](./LICENSE)
[![python](https://img.shields.io/badge/python-3.11+-blue.svg?logo=python&logoColor=white)](https://www.python.org)
[![Adapters](https://img.shields.io/badge/Adapters-Alconna-blue)](#supported-adapters)
<br/>

[![uv](https://img.shields.io/badge/package%20manager-uv-black?logo=uv)](https://github.com/astral-sh/uv)
[![ruff](https://img.shields.io/badge/code%20style-ruff-black?logo=ruff)](https://github.com/astral-sh/ruff)

</div>

## 📖 介绍

更好的emoji合成，长期跟踪上游数据源。支持两种触发方式：
- **显式模式**：发送 `emoji+emoji`（如 `😂+🥺`）触发合成
- **自动模式**：消息内包含两个相邻的 emoji（如 `😂🥺`）自动检测并合成（需配置开启）

数据来源：[xsalazar/emoji-kitchen-backend](https://github.com/xsalazar/emoji-kitchen-backend)，包含 **14 万+** 种 emoji 组合。

## 💿 安装

<details open>
<summary>使用 nb-cli 安装</summary>
在 nonebot2 项目的根目录下打开命令行, 输入以下指令即可安装

    nb plugin install nonebot-plugin-auto-emojimix --upgrade
使用 **pypi** 源安装

    nb plugin install nonebot-plugin-auto-emojimix --upgrade -i "https://pypi.org/simple"
使用**清华源**安装

    nb plugin install nonebot-plugin-auto-emojimix --upgrade -i "https://pypi.tuna.tsinghua.edu.cn/simple"


</details>

<details>
<summary>使用包管理器安装</summary>
在 nonebot2 项目的插件目录下, 打开命令行, 根据你使用的包管理器, 输入相应的安装命令

<details open>
<summary>uv</summary>

    uv add nonebot-plugin-auto-emojimix
安装仓库 main 分支

    uv add git+https://github.com/Misty02600/nonebot-plugin-auto-emojimix@main
</details>

<details>
<summary>pdm</summary>

    pdm add nonebot-plugin-auto-emojimix
安装仓库 main 分支

    pdm add git+https://github.com/Misty02600/nonebot-plugin-auto-emojimix@main
</details>
<details>
<summary>poetry</summary>

    poetry add nonebot-plugin-auto-emojimix
安装仓库 main 分支

    poetry add git+https://github.com/Misty02600/nonebot-plugin-auto-emojimix@main
</details>

打开 nonebot2 项目根目录下的 `pyproject.toml` 文件, 在 `[tool.nonebot]` 部分追加写入

    plugins = ["nonebot_plugin_auto_emojimix"]

</details>

<details>
<summary>使用 nbr 安装(使用 uv 管理依赖可用)</summary>

[nbr](https://github.com/fllesser/nbr) 是一个基于 uv 的 nb-cli，可以方便地管理 nonebot2

    nbr plugin install nonebot-plugin-auto-emojimix
使用 **pypi** 源安装

    nbr plugin install nonebot-plugin-auto-emojimix -i "https://pypi.org/simple"
使用**清华源**安装

    nbr plugin install nonebot-plugin-auto-emojimix -i "https://pypi.tuna.tsinghua.edu.cn/simple"

</details>


## ⚙️ 配置

在 nonebot2 项目的 `.env` 文件中添加以下配置：

| 配置项              | 必填  | 默认值 | 说明                                        |
| :------------------ | :---: | :----: | :------------------------------------------ |
| `emojimix_explicit` |  否   | `True` | 是否启用显式合成                            |
| `emojimix_auto`     |  否   | `True` | 是否启用自动模式（检测相邻 emoji 自动合成） |
| `emojimix_cd`       |  否   |  `60`  | 每个用户的冷却时间（秒），设为 0 则不限制   |

```dotenv
# 启用显式合成
emojimix_explicit=true
# 启用自动合成
emojimix_auto=true
# 设置冷却时间
emojimix_cd=3
```

## 🎉 使用

### 指令表

| 指令          | 权限  | 需要@ |   范围    | 说明                              |
| :------------ | :---: | :---: | :-------: | :-------------------------------- |
| `emoji+emoji` | 所有  |  否   | 私聊/群聊 | 显式合成两个 emoji                |
| *(自动检测)*  | 所有  |  否   | 私聊/群聊 | 消息中包含两个相邻 emoji 自动合成 |

### 更新数据

如果需要更新 emoji 组合数据（跟踪上游新增组合），克隆项目后运行：

```bash
uv run python scripts/update_emoji_data.py
```

脚本会自动检查上游是否有更新，有则下载并重新生成数据库。
