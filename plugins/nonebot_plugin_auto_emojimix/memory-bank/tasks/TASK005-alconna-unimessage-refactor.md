# [TASK005] - 引入 nonebot-plugin-alconna UniMessage 跨平台消息层

**Status:** Completed
**Added:** 2026-02-18
**Updated:** 2026-02-18

## Original Request
通过 nonebot-plugin-alconna 重构项目，解锁跨平台适配能力。

## Thought Process
- 分析了 Alconna 的 `on_alconna` 和 `UniMessage` 两大能力
- `on_alconna` 适合结构化命令解析，但本插件的匹配逻辑是"正则匹配 emoji 对"，不是传统命令
- 自动模式 (`check_auto_emojis`) 需要在所有消息中搜索相邻 emoji，与命令解析理念冲突
- **结论**: 采用"方案 A — 轻度重构"，仅替换消息发送层为 `UniMessage`，保留高效的 `on_message + Rule` 匹配逻辑
- 这样获取 `UniMessage` 跨平台发送能力的同时，不破坏现有匹配性能

### 为什么不用 `on_alconna` 替换 `on_message`？
1. emoji 合成的触发方式不是命令（无前缀、无子命令、无选项）
2. 现有正则 Rule 已高效实现短路（99.9% 消息在 `"+" not in text` 短路）
3. 自动模式的 `block=False, priority=20` 行为难以用 `on_alconna` 表达
4. 强行套 Extension 只会增加复杂度，无实质收益

## Implementation Plan
- [x] 分析重构可行性，确定方案 A
- [x] 更新记忆库文档
- [x] handler.py: 替换 `MessageSegment.image()` 为 `UniMessage.image()`
- [x] handler.py: 将 `MessageEvent` 替换为通用 `Event`
- [x] __init__.py: 添加 `require("nonebot_plugin_alconna")`，移除适配器限制
- [x] pyproject.toml: 添加 `nonebot-plugin-alconna` 依赖，移除 `nonebot-adapter-onebot` 运行时依赖
- [x] 更新测试以适配新的元数据断言
- [x] 运行测试验证 — 27 个测试全部通过

## Progress Tracking

**Overall Status:** Completed - 100%

### Subtasks
| ID  | Description                | Status   | Updated    | Notes                             |
| --- | -------------------------- | -------- | ---------- | --------------------------------- |
| 5.1 | 分析重构可行性             | Complete | 2026-02-18 | 确定方案 A                        |
| 5.2 | 更新记忆库                 | Complete | 2026-02-18 |                                   |
| 5.3 | handler.py 替换消息发送层  | Complete | 2026-02-18 | UniMessage 替代 MessageSegment    |
| 5.4 | __init__.py 更新适配器声明 | Complete | 2026-02-18 | require + supported_adapters=None |
| 5.5 | pyproject.toml 更新依赖    | Complete | 2026-02-18 | alconna>=0.59.0                   |
| 5.6 | 更新测试                   | Complete | 2026-02-18 | 元数据断言更新                    |
| 5.7 | 运行测试验证               | Complete | 2026-02-18 | 27 passed, 0 failed               |

## Progress Log
### 2026-02-18
- 分析了两种重构方案 (方案 A: 轻度重构 vs 方案 B: 深度重构)
- 确定采用方案 A — 仅替换消息发送层为 UniMessage
- 更新记忆库: activeContext, progress, techContext, systemPatterns, tasks
- 实施代码修改:
  - `__init__.py`: +require("nonebot_plugin_alconna"), supported_adapters=None
  - `handler.py`: MessageSegment.image() → UniMessage.image(raw=...), MessageEvent → Event
  - `pyproject.toml`: nonebot-adapter-onebot → nonebot-plugin-alconna>=0.59.0
  - `plugin_test.py`: 元数据断言更新
- uv sync: nonebot-plugin-alconna==0.60.4 安装成功
- 27 个测试全部通过 (4 集成 + 23 单元)
