# 响应延迟优化 — 根因分析与修复记录

**日期**：2026-05-01  
**Trace**：trace_c99a781e6279（55 events · 25 LLM calls · 6 tool calls · 12 tasks · ~182s）

---

## 一、问题描述

玩家操作后，世界完成一次完整响应耗时异常长（超过 180 秒）。Trace 数据显示存在
多次 LLM 调用均以 `move_to_location FAIL` 告终并重新入队的循环，以及 Embedding 
任务队列严重积压的问题。

---

## 二、根因分析

### 根因 1：move_to_location 不可达目标无限循环（约 95s）

- 小芳的目标场景在当前 tick 内无路可达（`TARGET_NOT_REACHABLE`），但 `ToolResult.fail(retryable=True)` 只是审计标志，不阻止上层在下一个 `ai_tick_minutes` 后重新入队 `agent_decision` 任务。
- LLM 在新的决策周期仍然选择同一不可达目标（感知 prompt 中地点列表未过滤），再次失败。
- 此循环在 trace 中重复 **6 次**，浪费约 95s。

### 根因 2：Embedding 任务队列积压（约 71s）

- 5315ms 时写入记忆，`write_memory_embedding` 入默认队列。
- Worker 此时正在执行 `agent_decision` 任务，`write_memory_embedding` 排在队尾。
- 直到 76564ms（约 71s 后）才开始执行，严重积压。
- 两类任务共用同一优先级队列，决策任务和 embedding 任务相互争抢 worker。

### 根因 3：单次决策链路全串行（约 6-12s 额外延迟/cycle）

- `_retrieve`（memory search）与 `get_planning_service().get_current_context()`（planning）串行执行，但两者互不依赖，可并发。
- `_maybe_reflect_batch` 在 `_tick_inner` 内 `await` LLM，偶发时阻塞 tick 5-10s。

---

## 三、修复措施

### Fix 1：不可达目标熔断（P0）

**文件**：`backend/app/llm/tools/world_tools.py`、`backend/app/llm/agent_decision.py`、`backend/app/core/redis_client.py`

- `world_tools.py`：新增 `_mark_unreachable(agent_id, target_id)` 函数，在 `move_to_location` / `move_to_entity` 返回 `TARGET_NOT_REACHABLE` 时写入 Redis `agent:{id}:unreachable` 集合（TTL 300s，与 `ai_tick_minutes` 对齐）。
- `agent_decision.py`：`_perceive()` 中新增 `_get_unreachable_set(agent_id)` 读取黑名单，从感知地点和附近实体列表中过滤不可达目标后再组装 prompt。LLM 不再看到不可达选项，从根本上断开失败循环。
- `redis_client.py`：新增 `key_agent_unreachable()` key 构造器。

### Fix 2：并行化 retrieve + planning_context（P1）

**文件**：`backend/app/llm/agent_decision.py`

- `decide_with_llm()` 中将 `_retrieve` 和 `_safe_plan_ctx` 改为 `asyncio.gather()` 并发执行，节省约 6s/cycle。

### Fix 3：Embedding 向量 Redis 缓存（P1）

**文件**：`backend/app/llm/embedding.py`、`backend/app/core/redis_client.py`

- `EmbeddingService.embed()` 新增 Redis 缓存层，key 为 `embed:{sha256(text)[:32]}`，TTL 1h。
- 新增 `key_embedding_cache()` key 构造器。

### Fix 4：write_memory_embedding 降为 low 队列（P1）

**文件**：`backend/app/services/memory_service.py`

- enqueue 时设置 `priority=9`（low 队列），保证 `agent_decision`（priority≤3，high 队列）始终优先执行，消除决策任务因 embedding 任务积压而延迟的问题。
- `deadline_seconds` 同步调整为 120s（low 队列的 SLA 更宽松）。

### Fix 5：反思任务异步入队（P2）

**文件**：`backend/app/domain/simulation/engine.py`

- 将 `_maybe_reflect_batch()` 内联 LLM 调用替换为 `_enqueue_reflect_batch()`，投递 `daily_reflection` 任务到 low 队列（priority=8）。
- 保留 `_last_reflect_at` / `_last_summary_day` 内存频率控制，避免过度入队。
- 日结任务以 `summary:{day_key}` 为幂等 extra，保证一天只执行一次。

---

## 四、预期收益

| 优化项 | 预期节省 |
|--------|---------|
| 不可达目标熔断 | ~75s / trace（消除 6 轮循环） |
| 并行化 retrieve + planning | ~6s / 决策周期 |
| Embedding 缓存 | ~1s / 重复查询（减少 API 调用） |
| Embedding 低优先级队列 | 消除 ~71s 积压（决策任务不再被 embedding 堵塞） |
| 反思任务异步化 | 消除偶发 tick 阻塞 5-10s |

---

## 五、注意事项

1. 不可达黑名单 TTL 为 5 分钟，与 `ai_tick_minutes` 对齐。地图更新后黑名单会自动过期，不会产生永久误屏蔽。
2. Embedding 缓存仅缓存成功返回的向量，失败时不缓存，确保不会缓存空值。
3. `daily_reflection` 幂等 key 设计：反思用 `simulation_step` 作为幂等维度（每步唯一），日结用 `summary:{day_key}` 保证一天一次。
4. Worker 进程需要至少 2 个队列监听实例才能同时消费 high + low 两种优先级任务，建议生产环境提高 worker 并发数。
