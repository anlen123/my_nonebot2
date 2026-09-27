# [TASK002] - 自动触发表情合成

**Status:** Completed
**Added:** 2026-02-12
**Updated:** 2026-02-12

## Original Request
添加一个配置，用于控制是否自动触发表情合成。对于用户的任意纯文本消息，当里面包含了两个相邻的可合成 emoji 时，自动发送合成 emoji。

## Thought Process
1. 需要一个新的配置项 `auto_emojimix` (bool, default=False) 来控制是否启用自动触发
2. 需要一个新的正则表达式来匹配消息中的相邻 emoji 对（不需要 `+` 号分隔）
3. 需要一个新的 `on_message` handler，优先级低于显式合成 handler
4. 自动模式下只在成功合成时发送图片，不发送错误消息（避免干扰正常聊天）
5. 跳过包含 `+` 号的消息，避免与显式合成处理器冲突
6. `block=False`，不阻断其他处理器

## Implementation Plan
- [x] 在 `config.py` 中添加 `auto_emojimix` 配置项
- [x] 在 `handler.py` 中添加 `auto_pattern` 正则和 `check_auto_emojis` 检查函数
- [x] 在 `handler.py` 中添加 `auto_emojimix_matcher` 和 `handle_auto_emojimix` 处理函数
- [x] 更新集成测试验证新配置和处理器
- [x] 更新单元测试 mock 配置
- [x] 验证所有 27 个测试通过

## Progress Tracking

**Overall Status:** Completed - 100%

### Subtasks
| ID  | Description                | Status   | Updated    | Notes                        |
| --- | -------------------------- | -------- | ---------- | ---------------------------- |
| 2.1 | 添加 auto_emojimix 配置项  | Complete | 2026-02-12 | bool, default=False          |
| 2.2 | 添加自动触发正则和检查函数 | Complete | 2026-02-12 | re.search 匹配两个相邻 emoji |
| 2.3 | 添加自动触发处理器         | Complete | 2026-02-12 | priority=14, block=False     |
| 2.4 | 更新测试                   | Complete | 2026-02-12 | 集成+单元测试 mock 均已更新  |
| 2.5 | 验证测试通过               | Complete | 2026-02-12 | 27 passed                    |

## Progress Log
### 2026-02-12
- 在 `config.py` 添加 `auto_emojimix: bool = False` 配置项
- 在 `handler.py` 中:
  - 添加 `from .config import plugin_config` 导入
  - 将原 `pattern` 重命名为 `explicit_pattern`
  - 新增 `auto_pattern` 正则，匹配两个相邻的 emoji
  - 新增 `check_auto_emojis` 函数，受 `auto_emojimix` 配置控制
  - 新增 `auto_emojimix_matcher` (priority=14, block=False)
  - 新增 `handle_auto_emojimix` 处理函数，只在成功合成时发送图片
- 更新 `tests/plugin_test.py` 验证新配置和处理器
- 更新 `tests/units/conftest.py` 添加 mock 配置
- 所有 27 个测试通过

## Design Decisions
1. **默认关闭**: `auto_emojimix` 默认 `False`，避免对不需要的用户造成干扰
2. **不阻断**: `block=False`，自动触发不会阻止其他处理器处理消息
3. **静默失败**: 自动模式下不支持的组合不发送错误消息，只在成功时发送
4. **跳过 + 号消息**: 包含 `+` 的消息交给显式处理器，避免冲突
5. **低优先级**: priority=14（显式处理器 priority=13 优先）
