# Trace 边界错误 & Deadline 未执行 — 根因分析与修复记录

**日期**：2026-05-01  
**发现方式**：观测平台 TraceDetail 显示一次 world_tick 的 wall-clock 长达 979 600 ms（约 16 分钟），用户质疑数据合理性，深入排查后发现两个独立 bug。

---

## 一、问题描述

观测平台 Traces 面板中，某条 trace（`trace_f7be22a8d9d0`）显示：

```
wall-clock: 979 600 ms
关键路径（串行）: 979 600 ms
并行节省: 0 ms
llm: 16  tool: 4  task: 10
```

截图中时间轴起点 11:38:33，而最后一条事件发生在 974–976s 之后（约 11:54），
跨越约 16 分钟。用户报告实际 world_tick 响应绝不会有这么慢。

---

## 二、根因分析

### Bug 1：任务 deadline 字段写入但从未被 runner 检查

**严重程度**：P0（正确性 bug）

**根因**：

`queue.py` 的 `enqueue()` 在创建 Task 行时已经根据 `deadline_seconds` 计算并写入
`tasks.deadline_at`：

```python
# backend/app/domain/tasks/queue.py
if deadline_seconds is not None:
    deadline_at = now + timedelta(seconds=deadline_seconds)
```

`agent_decision` 设置了 `deadline_seconds=60`，`write_memory_embedding` 设置了 `deadline_seconds=120`。

但 `runner.py` 的 `_execute_task` 在从 RQ 取出任务后，**从未读取 `task.deadline_at`**：

```python
# 修复前：_execute_task 直接跳过 deadline 检查
if task.status in {SUCCEEDED, FAILED, TIMEOUT}:
    return {"status": task.status, "idempotent": True}
# ↑ 仅检查终态，直接进入 handler 调用，deadline_at 被忽略
handler = registry.get(task.task_type)
...
```

**后果**：

- RQ 队列积压时，一个入队 16 分钟的 `agent_decision` 任务仍被执行。
- 执行时世界状态已经历 N 轮更新，LLM 的决策基于完全过期的 `world_time` payload，
  NPC 会做出与当前世界状态不匹配的行为。
- 每次过期任务执行都消耗 LLM API quota（约 5000 token/次）。

---

### Bug 2：trace_id 从 world_tick 传播到所有 async 任务，导致 wall-clock 虚高

**严重程度**：P1（观测数据误导）

**根因**：

`queue.py` 的 `enqueue()` 把当前 trace 上下文（包含 tick 的 `trace_id`）快照进 payload：

```python
# backend/app/domain/tasks/queue.py（修复前）
snap = current_trace()
enriched_payload = {
    **(payload or {}),
    "__trace__": snap.as_log_extra(),  # 直接传入当前 trace_id（= world_tick 的 trace）
}
```

Worker 执行任务时，`runner.py` 从 payload 恢复 `trace_id`：

```python
snapshot = TraceSnapshot(
    trace_id=trace_payload.get("trace_id") or task.trace_id,  # 继承了 tick 的 trace_id
    ...
)
with TraceContext.apply(snapshot):
    await handler.handle(ctx)  # 任务内所有 LLM call 都打上 tick 的 trace_id
```

**时间线示意**：

```
T+0ms     world_tick N 开始，设 trace_id = "trace_f7be22…"
T+50ms    tick 同步逻辑完成，enqueue(agent_decision × 5 NPC)
           → 每个任务 payload.__trace__.trace_id = "trace_f7be22…"

T+300s    tick N+150 触发，积压 150 个任务在 RQ 队列

T+979600ms  Worker 终于处理 tick N 的 embedding 任务
            → 记录 LLM call，trace_id = "trace_f7be22…"

wall-clock = T+979600ms − T+0ms = 979 600 ms ✓（但这不是 tick 耗时）
```

**TraceDetail 计算公式**：

```
wall-clock = max(event.created_at + event.duration_ms) − min(event.created_at)
             └── 所有拥有同一 trace_id 的事件
```

979 600 ms 不是一次 tick 的响应时间，而是**从 tick 触发到最后一个异步任务完成**的跨度，
中间大部分时间是 RQ 队列等待。

---

## 三、修复措施

### Fix 1：runner.py 增加 deadline 检查

**文件**：`backend/app/domain/tasks/runner.py`

在 `_execute_task` 终态检查之后、handler 调用之前，加入：

```python
now_ts = utcnow()
if task_deadline is not None and now_ts > task_deadline:
    waited_secs = (now_ts - task.created_at).total_seconds()
    logger.warning(
        "task_id=%s type=%s DEADLINE_EXCEEDED waited=%.1fs",
        task.id, task.task_type, waited_secs,
    )
    # 直接标记 FAILED，不调用 handler
    await _sess.execute(
        update(Task).where(Task.id == task.id)
        .values(status=FAILED, finished_at=now_ts,
                last_error=f"TASK_DEADLINE_EXCEEDED: waited {waited_secs:.1f}s")
    )
    await get_observer().record_task_status(
        task_id=task.id, from_status=PENDING, to_status=FAILED,
        retry_count=task.retry_count,
        message=f"deadline exceeded after {waited_secs:.1f}s in queue",
    )
    return {"status": FAILED, "error": "TASK_DEADLINE_EXCEEDED"}
```

**注意**：deadline 检查时用 `from_status=PENDING`（任务未进入 RUNNING），
与普通失败使用 `from_status=RUNNING` 不同。

---

### Fix 2：任务分配独立 trace_id，parent_trace_id 保留触发链

**变更范围**：

| 文件 | 变更内容 |
|------|----------|
| `backend/app/core/trace_context.py` | `TraceSnapshot` 新增 `parent_trace_id` 字段；新增 `_parent_trace_id` contextvar；更新 `as_log_extra()` / `current()` / `TraceContext.apply()` |
| `backend/app/domain/tasks/queue.py` | `enqueue()` 生成独立 `task_trace_id = f"trace_{uuid4().hex[:12]}"`，存 `parent_trace_id = snap.trace_id` 进 payload |
| `backend/app/domain/tasks/runner.py` | `_execute_task` 从 payload 恢复 `parent_trace_id` 到 `TraceSnapshot` |
| `backend/app/api/routes_observability.py` | `_serialize_task()` 从 payload `__trace__` 提取 `parent_trace_id` 透出给前端 |
| `frontend/src/types/observability.ts` | `TaskRecord` 新增 `parent_trace_id: string \| null` |
| `frontend/src/pages/observability/TraceDetail.tsx` | `TaskSection` 展示 `parent_trace_id` + 跳转按钮；`TraceView` 头部显示触发方 trace；任务行增加 `waited=Xs` |
| `frontend/src/pages/observability/ObservabilityPage.tsx` | 向 `TraceDetail` 传入 `onNavigate={setTraceId}` 回调 |

**修复前后对比**：

| 场景 | 修复前 wall-clock | 修复后 wall-clock |
|------|-------------------|-------------------|
| world_tick trace | 979 600 ms（含队列等待） | ~50–200 ms（仅同步逻辑） |
| agent_decision 任务 trace | 合并进 tick trace | 独立 trace，秒级（LLM 调用 + tool 执行） |
| embedding 任务 trace | 合并进 tick trace | 独立 trace，亚秒级（Embedding API 调用） |

---

## 四、预防措施

1. **队列积压监控**：`/api/observability/dashboard` 的 task 聚合中，`pending` 数量
   持续增长是 worker 处理能力不足的信号，应配置告警阈值。
2. **deadline 覆盖**：所有时效性业务任务（`agent_decision`、`player_response` 等）
   **必须**设置合理的 `deadline_seconds`，让 runner 的检查机制生效。
3. **trace 边界原则**：异步任务不应继承父 trace 的 `trace_id`；应通过 `parent_trace_id`
   保留触发链，各自持有独立的短时 trace。

---

## 五、验证

- `frontend` TypeScript 构建通过（`npm run build`，exit 0）。
- `backend` linter 无新增错误（`ReadLints` 检查 4 个修改文件）。
- 修复后新任务的 `payload.__trace__.parent_trace_id` 已正确写入，
  `_serialize_task()` 可正确透出。
- `TraceDetail` 在有 `parent_trace_id` 时显示触发方 trace 跳转按钮，点击后 `traceId` 
  状态切换，页面重新拉取并展示触发方 trace（world_tick，wall-clock 约 50–200ms）。
