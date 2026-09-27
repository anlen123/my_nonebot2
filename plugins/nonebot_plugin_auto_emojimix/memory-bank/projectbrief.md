# Project Brief - nonebot-plugin-auto-emojimix

## 项目概述
nonebot-plugin-auto-emojimix 是一个 NoneBot2 插件，用于自动合成两个 emoji 表情为一张图片。基于 Google Emoji Kitchen 服务，使用本地 JSON 数据文件查找 emoji 组合映射，然后从 Google 服务器下载合成图片。

## 核心功能
- **emoji 合成**: 用户发送 `emoji1+emoji2` 格式的消息，插件自动识别并合成为图片
- **自动识别**: 通过正则表达式匹配消息中的 emoji 对，无需指令前缀
- **本地数据查找**: 使用预编译的 `emojimix_data_compact.json` 快速查找支持的组合
- **远程图片获取**: 从 Google Emoji Kitchen API 下载合成后的 PNG 图片

## 技术栈
- Python 3.11+
- NoneBot2 框架
- OneBot V11 适配器（原生 `MessageSegment`）
- emoji (Python emoji 数据库，构建匹配正则)
- httpx (异步 HTTP 客户端)
- pytest + nonebug (测试框架)

## 项目状态
项目处于**功能完成阶段**，核心功能已开发完成并通过测试。

## 目标
1. ✅ 完成核心功能的开发和正式化
2. ✅ 编写完善的单元测试
3. ✅ 支持 HTTP 代理配置
4. ⬜ 发布到 NoneBot 商店
