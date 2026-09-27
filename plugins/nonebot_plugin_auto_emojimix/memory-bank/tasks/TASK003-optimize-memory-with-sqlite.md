# [TASK003] - 内存优化：迁移至 SQLite 存储

**状态:** ✅ 已完成
**创建时间:** 2026-02-12
**完成时间:** 2026-02-15

## 原始需求
用户在 2GB 内存的小型服务器上运行插件，JSON 数据加载后占用约 50MB 内存（14 万条组合的 Python dict），
希望通过 SQLite 数据库减少内存占用。

## 实施结果

### 内存变化
- **之前**: ~50 MB（14 万条 `_combos` dict 常驻内存）
- **之后**: < 100 KB（仅 613 条 `_emoji_map` 在内存，组合数据在磁盘按需查询）

### 改动的文件

| 文件                           | 变更                                                                                                       |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------- |
| `service.py`                   | JSON + dict → sqlite3 只读连接。移除 `_combos`、`_dates`、`_base_url`，新增 `_db` 连接和 `_load_emoji_map` |
| `emojimix.db` (新)             | 单表 `combos(code1, code2, date)`，143,274 条，7.55 MB                                                     |
| `emojimix_data_compact.json`   | **已删除**                                                                                                 |
| `scripts/update_emoji_data.py` | 重写为直接输出 `.db` 文件（不再生成 JSON）                                                                 |
| `tests/units/test_service.py`  | `_combos` 断言改为 DB 查询断言                                                                             |
| `tests/plugin_test.py`         | `_combos` 断言改为 `_db` 断言                                                                              |

### 数据库表结构

```sql
CREATE TABLE combos (
    code1 TEXT NOT NULL,   -- "u1f602"
    code2 TEXT NOT NULL,   -- "u1f97a"
    date  TEXT NOT NULL,   -- "20210521"
    PRIMARY KEY (code1, code2)
);
```

### 技术决策

1. **sqlite3 标准库同步调用**，不用 aiosqlite/ORM
   - 本地主键查找 5-50μs，不阻塞事件循环
   - 零额外依赖
   - nonebot-plugin-orm 底层也是 aiosqlite → sqlite3，过度包装
2. **BASE_URL 硬编码为模块常量**，dates 数组不再需要
3. **DB 文件放 src 目录**，只读数据随包分发

---

## 讨论过程

### JSON vs SQLite 的真正区别
启动时两者都需加载 613 个 emoji 到内存（用于正则构建），差别不大。
真正的区别在运行时：JSON 的 14 万条 `_combos` 必须常驻内存，SQLite 的在磁盘按需查询。

### 调研过的方案
- **nonebot-plugin-orm**: SQLAlchemy + Alembic + aiosqlite，适合业务数据读写，不适合只读静态数据
- **nonebot-plugin-datastore**: 同上，更高层封装
- **aiosqlite**: 内部也是把 sqlite3 丢到线程池，对微秒级查询反而增加开销
- **sqlite3 直接调用**: ✅ 零依赖、最简、微秒级不阻塞

### 旧方案存档

<details>
<summary>早期讨论的多表方案（已放弃）</summary>

- 方案 A: 单表 combo_key + date_idx（仍需 dates 数组）
- 方案 B: 双表 emojis + combos（不必要，emoji 可从 combos DISTINCT 查出）
- 方案 C: 三表 metadata + emojis + combos（过度设计）
</details>

## 进展日志
### 2026-02-12
- 创建任务计划
- 讨论了多种数据库表结构方案
- 结论：暂缓实施

### 2026-02-15
- 实测数据规模：613 emoji、143,274 组合、39 日期
- 调研 NoneBot 异步数据库方案（nonebot-plugin-orm、aiosqlite）
- 确定方案：sqlite3 标准库 + 单表三字段
- 从 JSON 生成 `emojimix.db`（7.55 MB）
- 重写 `service.py`：移除 dict 存储，使用 sqlite3 只读连接
- 重写 `scripts/update_emoji_data.py`：直接输出 .db 文件
- 更新所有测试：**27 passed, 0 failed**
- 删除 `emojimix_data_compact.json`
- **任务完成** ✅
