# [TASK004] - 用户冷却限制（Rate Limiting）

**Status:** Completed
**Added:** 2026-02-15
**Updated:** 2026-02-15

## Original Request
限制用户在一定时间内的使用次数，防止滥用。使用带 TTL 的缓存库实现冷却机制。

## Thought Process
- service 不应该返回提示消息，而是抛出异常（已在本次会话中完成重构）
- 冷却限制放在 Rule 层（NoneBot 的 check 函数中），冷却中直接不匹配，无需显式回复
- 使用 `cachetools.TTLCache` 实现，key 为 user_id，过期自动清理
- 冷却时长通过配置项控制，允许管理员自定义
- 两种模式（显式/自动）共享同一个冷却缓存

## Implementation Plan
- [x] 1. `pyproject.toml` 添加 `cachetools` 依赖
- [x] 2. `config.py` 添加 `emojimix_cd: int = 5` 配置项（冷却秒数，0 = 不限制）
- [x] 3. `handler.py` 中实现冷却检查逻辑（在 Rule 函数中，非 handler 函数）
- [x] 4. 更新测试（集成测试 + 单元测试 conftest）
- [x] 5. 更新 README 配置表
- [x] 6. ruff + basedpyright + pytest 全部通过

## Key Decisions
- **冷却放在 Rule 而非 handler**：冷却中 Rule 返回 False，消息静默跳过
- **使用 cachetools.TTLCache**：自动过期，无需手动清理，零维护
- **配置化 cd 秒数**：管理员可灵活调整，设为 0 时不创建缓存（None）
- **按 user_id 限制**：通过 NoneBot 依赖注入 MessageEvent 获取

## Files Modified
- `pyproject.toml` - 添加 cachetools 依赖
- `config.py` - 添加 emojimix_cd 配置项
- `handler.py` - TTLCache 冷却逻辑集成到 Rule 函数
- `tests/plugin_test.py` - 添加 emojimix_cd 断言
- `tests/units/conftest.py` - 更新 mock 配置
- `README.md` - 更新配置表

## Progress Log
### 2026-02-15
- 创建任务，确定实现方案
- 完成全部实施：依赖、配置、冷却逻辑、测试、文档
- 根据用户反馈将冷却检查从 handler 移到 Rule 函数中
### 2026-02-15 (Refactoring)
- **Rule Composition**: 将 handler 逻辑重构为 pure Rule checker (`_check_explicit`, `_check_auto`) 和 read-only cooldown checker (`_check_cooldown`) 组合，确保冷却逻辑不会产生副作用。
- **Config Renaming**: 为了配置项语义统一，重命名了配置字段：
  - `emojimix` -> `emojimix_explicit`
  - `auto_emojimix` -> `emojimix_auto`
  - `emojimix_cd` (保持不变)
- **CI Fixes**: 修复 CI 对 `scripts/` 目录的 ruff 检查报错，通过 `pyproject.toml` 排除非源码目录。
- **Doc Updates**: 更新 README 配置表及 `.env` 示例。
