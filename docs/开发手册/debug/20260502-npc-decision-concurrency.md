# 复盘：22 NPC 决策被单 worker 串行卡死 / 部分 NPC 不产生记忆

**日期**：2026-05-02  
**关联模块**：`backend/app/domain/tasks/worker.py`、`runner.py`、`queue.py`、
`backend/app/domain/simulation/engine.py`、`backend/app/db/session.py`、
`backend/app/core/redis_client.py`

---

## 一、症状

阶段 21 默认 22 NPC 新游戏跑起来后，玩家页面看到的现象：

- 多数 NPC（约 80%）反复执行**完全相同**的兜底行为（rule_agent 给出的日程
  默认动作）；
- 只有 4-6 个 NPC 偶尔有 LLM 风格的决策（带 emotion + memory_writes）；
- 大多数 NPC 的 `/api/agents/{id}/memories` 长期为空，没有 chat / thought
  / event 记忆产生；
- 后台 `/api/observability/tasks?type=agent_decision` 大量 `failed` 行，
  `last_error=TASK_DEADLINE_EXCEEDED: waited XYZ.Xs in queue`。

## 二、根因

**单一根因：worker 吞吐严重不足，绝大多数 `agent_decision` 任务在
`deadline_seconds` (60s) 内被丢弃，从未真正命中 LLM handler。**

### 量化

默认配置：

| 项 | 值 | 说明 |
|----|----|------|
| `simulation_world_tick_hz` | 5 Hz | 1 真实秒推进 5 仿真分钟 |
| `simulation_ai_tick_minutes` | 3 | 单 NPC 每 3 仿真分钟决策一次 |
| 单 NPC 入队节奏 | 0.6 真实秒 / 次 | = `ai_tick_minutes / world_tick_hz` |
| 22 NPC 稳态入队率 | **~36.7 jobs/s** | = `22 / 0.6` |

worker 端：

| 项 | 值 | 说明 |
|----|----|------|
| `rq.worker.SimpleWorker.work()` 串行消费 | 1 任务在跑 | 不 fork、不并发 |
| qwen-plus / DashScope 平均 LLM latency | 5-15s | I/O bound |
| 单 worker 吞吐上限 | **~0.1-0.2 jobs/s** | = `1 / latency` |

入队 / 消费严重失衡：每秒入队 ~37、消费 ~0.2 → 队列在 60 秒内积压 ~2200 个
任务，绝大多数命中 `deadline_seconds=60s` 后被 runner `_execute_task` 标记
`TASK_DEADLINE_EXCEEDED` 直接丢弃，**handler 永远不被调用**。少数能被处理的
任务集中在头部，对应 4-6 个 NPC 的"幸存者偏差"。

由于决策路径既不写 memory_writes、也不消费 tool 的 memory_candidates（这部分
要在 LLM handler 内完成），**记忆数据自然为空**。引擎主循环 `_decide_all` 看到
`last_decision_at` 在入队那一刻就被设了，本帧已经"假装决策完了"，规则兜底也
不会再补一次 → NPC 行为退化成纯 schedule 静态推进。

### 为何之前没暴露

阶段 12 上线时 demo 仅 6 NPC，单 NPC 入队率 6/0.6=10/s，仍然能在
SimpleWorker 跑下来（虽然吞吐紧）。阶段 21 把默认 NPC 数量从 6 增到 22 之后，
入队率直接放大 3.7 倍，单 worker 的瓶颈第一次被打穿。

## 三、修复

### Fix 1：自定义并发 worker（**核心**）

替换 `rq.SimpleWorker.work()` 为 `app/domain/tasks/worker.py::_async_worker_main`：

- **单进程 + N asyncio 并发槽**：复用 `runner._ensure_loop()` 的常驻 event
  loop（避免跨 loop 的 Future 归属问题，详见
  `20260501-task-queue-rq-asyncio-loop.md`）。
- **直接 BLPOP RQ 队列**：跳过 `Worker.dequeue` / fork；按 high → default →
  low 顺序排队，保证 `agent_decision`（vt:high）始终先消费。
- **Semaphore 限流**：`TASK_QUEUE_WORKER_CONCURRENCY`（默认 16）控制最大并发。
- **Mini Scheduler**：1s tick 扫 `ScheduledJobRegistry`，把延迟到期的 retry
  job 推回主队列；替代 `Worker.work(with_scheduler=True)`。
- **Graceful drain**：SIGINT/SIGTERM 收到后停止 BLPOP，等已并发的任务最多
  30s 完成。

吞吐从 ~0.2 jobs/s 提升到 **N × 0.2 ≈ 3.2 jobs/s**（concurrency=16，2026-05-02
跟进调优把默认值从 8 提升到 16，详见 §四 配置变更）。

### Fix 2：决策锁去重

`engine._enqueue_decision_task` 在 enqueue 前 `SETNX agent:{id}:decision_lock`，
TTL = `agent_decision_deadline_seconds + 60`：

- 锁失败 → **跳过本次入队 + 不更新 `last_decision_at`** → 下一 tick 立刻再试；
- 锁成功 → 入队 + 更新 `last_decision_at`；
- runner 完成（成功 / 失败 / deadline 过期）→ `_release_agent_decision_lock`
  主动 `DEL` 锁；
- worker 进程崩溃 → 锁 TTL 自动过期，引擎自然恢复。

效果：同一 NPC 同时只有一份决策任务在跑，22 NPC 入队稳态自适应到 1.6-3.2/s 与
worker 吞吐对齐，没有冗余任务被 `deadline_seconds` 反复丢弃。

### Fix 3：启动错峰

`engine._load_state` 末尾把 22 NPC 的初始 `last_decision_at` 按索引分散到
`[0, ai_interval)` 仿真分钟内（之前默认 None → 第一波 tick 全员同时入队）。
让并发 worker 从启动开始就能流水线消费。

### Fix 4：DB 连接池扩容

`pool_size 5→20`、`max_overflow 10→20`。16 路并发 × (1-2 个 session/任务) ≈
16-32 个连接需求（≤ 40 上限，仍有 ~8 个余量给 ttl_worker / scheduler），避免
worker 协程之间因等连接退化为串行。

> 注意：若把 concurrency 调到 ≥ 32，必须同步把 `pool_size + max_overflow`
> 一起扩到 ≥ 32+32=64，否则会重新出现"等 DB 连接"的次级瓶颈。

PostgreSQL `max_connections` 默认 100，backend + worker 共 ≤ 50 个连接是安全余量。

### Fix 5：deadline 调长

`agent_decision_deadline_seconds` 60 → 90，给并发吞吐留出余量；同时仍丢弃
严重过期（玩家已不关心）的决策。

## 四、配置变更

`.env.example` 与 `Settings` 同步新增：

```env
# 单 worker 容器内的 asyncio 并发槽位数（默认 16；≥32 需同步扩 DB 池）
TASK_QUEUE_WORKER_CONCURRENCY=16
# agent_decision 任务在队列中等待的 deadline（秒）
AGENT_DECISION_DEADLINE_SECONDS=90
```

`backend/app/db/session.py` 的 `pool_size` 与 `max_overflow` 提升到 20/20。

## 五、回归与验证

### 单测

```
backend/tests % .venv/bin/python -m pytest --no-header -q --deselect tests/test_dialogue.py::test_rule_intent_threaten
111 passed, 7 skipped, 1 deselected, 10 warnings in 3.61s
```

（仅 `test_rule_intent_threaten` 是 main 上 pre-existing 的 LLM 兜底口径问题，
与本次改动无关。）

### 烟雾测试

```python
# 直接 import 启动并发 worker，1 秒后 stop，确认 graceful drain 正常
asyncio.run(_async_worker_main(queues, fake_redis, concurrency=4, stop_event))
# → "concurrent rq worker ready concurrency=4 queues=[vt:high, vt:default, vt:low]"
# → "smoke ok"
```

### 端到端验证（在 22 NPC 实例上）

启动 dev.sh 后 3 分钟内观察：

1. `worker` 启动日志应有：
   `concurrent rq worker ready concurrency=16 queues=[vt:high, vt:default, vt:low]`。
2. `/api/observability/tasks?type=agent_decision&status=succeeded&limit=50`
   应能看到**多个不同 NPC** 的决策任务成功记录（不再集中在 4-6 个）。
3. `/api/observability/tasks?type=agent_decision&status=failed&limit=20`
   `TASK_DEADLINE_EXCEEDED` 显著减少，从满屏降到偶发。
4. `/api/agents/{id}/memories` 在 5-10 分钟后能看到大多数 NPC 都有
   `chat / thought / event` 记忆产生。

## 六、扩容路径

- 横向：`docker compose up --scale worker=N`（每副本独立 16 路并发）。
  注意 docker-compose.yml 里 `worker` 服务带了 `container_name: vt_worker`，
  scale > 1 前需先把这一行删掉（重名容器会被 docker 拒绝）。
- 纵向：调大 `TASK_QUEUE_WORKER_CONCURRENCY`：
  - 16 → 32：必须同步把 `app/db/session.py` 的 `pool_size + max_overflow`
    一起扩到 ≥ 32+32=64，否则任务会卡在等 DB 连接；
  - 还需评估 DashScope 账号 RPM 余量；
  - PostgreSQL `max_connections`（默认 100，backend + worker 总和不要超过 70-80）。

## 七、未做

- **rule_agent 兜底也写记忆**：当前规则版决策不写 memory_writes / 候选记忆，
  这是设计原意（规则版只是"占位"）。但若并发模型再次饱和，可考虑给 rule_agent
  补一个最小记忆通道，避免 worker 全挂时记忆完全静默。
- **决策频率自适应**：状态稳定的 NPC（IDLE 多 tick）可以继续延长 ai_tick
  间隔；忙碌或刚发起社交的 NPC 反而要更短。后续优化项。
- **task 表 GC**：deadline 失败的任务行依然保留在 `tasks` 表用于审计，长期
  跑会膨胀；建议接入既有 TTL worker 周期性归档。

## 八、教训

- **吞吐计算要做在前面**。22 NPC × 0.6s 入队节奏 = 36.7 jobs/s 这个数字应该
  在阶段 21 升 NPC 数时就被算出来；只在 demo 数量级（6 NPC）下没爆是
  侥幸，不是健壮性。
- **idempotency_key 包含 simulation_step 时不能跨 tick 去重**。要么把
  step 从 idem 维度移除，要么像本次一样在 enqueue 前加锁。后者更通用，
  也不破坏现有"同 step 同 NPC 不重复入库"语义。
- **`SimpleWorker` 看似"轻量"但天生瓶颈**。RQ 文档默认推荐
  `Worker(forked)`；我们的 LLM-bound 任务用 fork 反而浪费，更适合纯 asyncio
  并发。`SimpleWorker` 在阶段 12 是为了简化，应在团队默认有 22+ NPC 时立刻
  替换。
