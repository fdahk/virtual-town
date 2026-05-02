# NPC 智能决策系统总览

> 范围：本文聚焦"NPC 决策大脑"模块本身——它如何感知、思考、选工具、影响世界、
> 并产生记忆。该文档与 `架构总览.md` §6（智能 NPC 与记忆模块）配套，是新人
> 排查 NPC 行为问题、调参促进社交活跃度的第一站。
>
> 配套文档：
> - 整体分层与运行时架构 → `docs/架构总览/架构总览.md`
> - LLM Tool 协议 → `docs/开发手册/契约文档/ToolCalling工具契约V1.md`
> - 详细实施计划 → `docs/实施方案/智能NPC与记忆模块实施方案.md`

## 0. 一句话回答常见问题

| 问题 | 答案 |
|------|------|
| NPC 真的用 LLM 决策吗？ | 是。主路径：`LLM 结构化 JSON 输出 + ToolCalling`；LLM 不可用或失败时回退 rule_agent 规则版。 |
| 决策频率是多少？ | 每个 NPC 独立有 5 仿真分钟（默认）的"AI tick"，不是每个 world tick 都决策。可调：`SIMULATION_AI_TICK_MINUTES`。 |
| LLM 决策放在主循环里吗？ | 默认 **async**：主循环只投递 RQ 任务，worker 进程跑 LLM。tick 永不卡。`TASK_QUEUE_MODE=async`。 |
| NPC 怎么会主动社交？ | 三条线并行：① LLM 自己看到 social_need 高 → 调 `socialize` / `request_interaction` / `go_to_entity`；② 引擎邂逅扫描器配对附近空闲 NPC；③ 玩家 / 任务系统也能注入请求。 |
| 想让 NPC 多社交怎么办？ | 调小 `SOCIAL_NEED_TRIGGER_THRESHOLD`、调大 `NEEDS_SOCIAL_GROWTH_PER_MINUTE`、调大 `SOCIAL_ENCOUNTER_DISTANCE`、调小 `SOCIAL_ENCOUNTER_SCAN_MINUTES`。详见 §7。 |

## 1. 整体决策栈

```text
┌──────────────────────────────────────────────────────────────────────────┐
│                          SimulationEngine._tick_inner                    │
│  （每 1 / world_tick_hz 真实秒一次，默认 5Hz；推进世界 +1 仿真分钟）       │
├──────────────────────────────────────────────────────────────────────────┤
│ 1. _handle_input          # 应用玩家输入（移动 / 对话注入）              │
│ 2. 推进 agent.path        # 沿已有路径走一格；走完触发 _on_agent_arrived │
│ 3. _check_player_environment / 危险地块                                   │
│                                                                          │
│ ─────── 阶段 19+ 的"自主驱动力" ────────                                  │
│ 4. _evolve_basic_needs    # social_need / hunger / energy 随时间变化      │
│ 5. _refresh_pursuits      # go_to_entity 设的追踪：到达即清，重决策      │
│ 6. _decide_all            # 各 NPC 走自己的 AI tick                      │
│ 7. _scan_social_encounters# 引擎层邂逅扫描器（同场景空闲 + 高 social_need）│
│ 8. _enqueue_reflect_batch # 反思 / 日结（vt:low 队列异步）                │
│                                                                          │
│ 9. _tick_natural_events   # 火灾 / 暴风雨 / 告示牌等环境事件              │
│ 10. _persist_tick + 广播  # WorldEvent → DB / WS / Observer / 记忆投影    │
└──────────────────────────────────────────────────────────────────────────┘
```

`_decide_all` 不是"每 tick 给所有 NPC 都决策一次"。它对每个 NPC 单独判断：
- `agent.state in {SLEEPING, CHATTING, BUSY_REFUSING}` → 跳过；
- `pursuing_entity_id` 已设（追踪中）→ 跳过；
- `last_decision_at` 距今 < `ai_tick_minutes` 仿真分钟 → 跳过；
- `busy_until` 未到 → 跳过；
- 命中以上条件后才进入决策路径，最多 5% 左右的 NPC 在同一 tick 内决策。

## 1.5 端到端执行架构（决策的物理流转）

上面的"决策栈"是逻辑视角；这一节讲"物理视角"——决策实际发生在哪些**进程**之间、
状态写到哪里、谁是单写者。**这是排查"NPC 为什么不动 / 没记忆"等并发问题的根基**。

### 1.5.1 进程拓扑

```text
┌──────────────────────┐  ┌──────────────────────┐  ┌────────────────┐  ┌──────────────┐
│  backend (uvicorn)   │  │  worker × N 副本     │  │  postgres      │  │  redis       │
│  ──────────────────  │  │  ──────────────────  │  │  ────────────  │  │  ──────────  │
│  FastAPI HTTP+WS     │  │  自定义并发 RQ Worker │  │  pgvector 16   │  │  队列+缓存   │
│  SimulationEngine    │  │  (asyncio + Sem)     │  │  业务真相 SoR  │  │  锁+pub/sub  │
│  TaskQueue.enqueue   │  │  decide_with_llm     │  │                │  │              │
│  规则版兜底           │  │  ToolExecutor        │  │                │  │              │
└────────┬─────────────┘  └────────┬─────────────┘  └───────┬────────┘  └──────┬───────┘
         │ enqueue                 │ BLPOP                  │ asyncpg          │ redis-py
         │ (vt:high/default/low)   │                        │                  │
         └─────────────────► redis ◄────────────────────────┼──────────────────┘
         │                                                  │
         └──────── postgres ◄────────────────────────────────┘
                  (agents / memories / tasks / world_events / ...)
```

### 1.5.2 各进程职责

| 进程 | 副本数 | 职责 | 不能做什么 |
|------|--------|------|------------|
| **backend** | **1**（单写者） | 推进 world_time；维护 EngineAgent 内存镜像；处理玩家输入；把 LLM 决策**入队**（不直接调 LLM）；广播 WebSocket | **不调 LLM**；不能起多份（会破坏 world_time 单调性） |
| **worker** | 1..N（默认 1） | `BLPOP` 队列；并发跑 `decide_with_llm`、reflection、consolidation、rumination 等任务；通过工具修改 DB | **不持有引擎实例**；不能拿 EngineAgent 内存（必须通过 DB / Redis） |
| **postgres** | 1 | 业务真相唯一源（SoR）：agents / memories / tasks / world_events / relationships … | — |
| **redis** | 1 | RQ 队列（`vt:high/default/low`）；缓存（embedding / unreachable）；分布式锁（决策锁） | — |

> 关键约束：**有状态层（postgres、redis）单实例 + 单写者引擎**保证一致性；
> **无状态计算层（worker）**才能任意横向扩展。

### 1.5.3 一次决策的端到端时序

```text
T+0.0s  backend 主循环 _decide_all
        ├─ 满足 ai_tick 条件的 NPC：先跑规则兜底（保证本轮不静止）
        └─ 调 _enqueue_decision_task：
              SETNX agent:{id}:decision_lock TTL=150
              insert tasks (idempotency_key=agent_decision:{id}:{step})
              RPUSH vt:high <task_id>
        ── 主循环立即返回，绝不等 LLM ──

T+?ms   某个 worker 协程槽 (sem) 拿到许可
        ├─ BLPOP vt:high → task_id
        ├─ Job.fetch
        ├─ runner._execute_task(task_id)
        │     └─ decide_with_llm(agent_id, world_time)
        │           Perceive → Retrieve → Plan(LLM HTTP) → Execute(tools)
        ├─ 工具直接 update agents / memories / world_events
        ├─ task 状态 → SUCCEEDED
        └─ DEL agent:{id}:decision_lock         ← 立即释放锁

T+?+next_tick
        backend 主循环再次跑：
        - reload_from_db 把 worker 改过的 agent 状态加载回 EngineAgent
        - 走 path / 触发新一轮 ai_tick
```

**两个要点**：
- 主循环永远不阻塞——LLM 慢与否都不会卡 world_tick；本轮的规则兜底保证 NPC 至少有"即时行为"。
- worker 的副作用通过 **DB**（持久状态）+ **Redis**（瞬态信号）回传给引擎，没有任何"内存共享 / RPC 回调"——这正是允许 worker 横向扩展的前提。

### 1.5.4 状态的单一真相源（SoR）

| 状态 | 单一真相源 | 谁能写 | 怎么读 |
|------|-----------|--------|--------|
| Agent.state / position / busy_until | Postgres `agents` | 引擎单写、worker 通过工具写 | 引擎 reload_from_db、worker session.get |
| Memory（含向量） | Postgres `memories` | worker / 投影器写 | MemoryService.search |
| 决策任务 | Postgres `tasks` + Redis 队列 | engine 入队、worker 改状态 | Observer / API |
| 决策"在飞"状态 | Redis `agent:{id}:decision_lock` | engine SETNX、runner DEL | engine SETNX 失败即知道有人在飞 |
| 不可达黑名单 | Redis `agent:{id}:unreachable` | 工具 / 引擎写 | engine 内存镜像 + perceive |
| 世界事件 | Postgres `world_events` + Redis pub/sub | engine 单写 | WS 广播 / event_projector |

只要每条状态有且只有一个写者方向，就不会有"多副本同步"问题——这是我们能放心起多个 worker 副本的基础。

## 2. LLM 路径：Perceive → Retrieve → Plan → Execute

主决策入口 `app/llm/agent_decision.py::decide_with_llm`。

```text
┌────────────┐     ┌────────────┐     ┌─────────────┐     ┌──────────────┐
│  PERCEIVE  │ ──► │  RETRIEVE  │ ──► │   PLAN      │ ──► │   EXECUTE    │
│ engine 内存 │     │ memories   │     │ Jinja prompt│     │ ToolExecutor │
│ + 不可达过滤│     │ + 相似度    │     │ + LLM JSON  │     │ → 副作用     │
└────────────┘     └────────────┘     └─────────────┘     └──────────────┘
                                                                │
                              ┌─────────────────────────────────┴───────┐
                              │  落库：tool 产生的 memory_candidates     │
                              │       LLM 自身 memory_writes             │
                              │       emotion 同步回 EngineAgent         │
                              └──────────────────────────────────────────┘
```

### 2.1 Perceive（感知）

- 当前 (scene, x, y) + state（含 status_effects / social_need / hunger / energy）。
- 同场景半径 10 格内的 NPC（按距离排，最多 6 个）。
- 当前场景的地点列表（最多 16 个），并**过滤** `agent:{id}:unreachable` 黑名单。
- 自然事件（火、告示牌、钓鱼点、可采摘对象、萤火虫）。

不可达黑名单同时过滤 NPC 与 Location，避免 LLM 反复选必然失败的目标
（参见 §6 不可达兜底）。

### 2.2 Retrieve（记忆检索）

- query = "附近实体名拼接" 或 "今天要做什么"。
- `MemoryService.search` 走 pgvector + importance + recency 三因素排序，取 top-6。
- 默认检索覆盖 **`working` / `short_term` / `long_term`**；`archived` 与
  `consolidated` 不进默认集合（详见 §10）。
- **访问强化**（阶段 20）：search 命中即刷新 `last_accessed_at`；
  `recency = max(exp(-Δ_created/168h), exp(-Δ_accessed/24h))`，
  常被回忆的旧记忆继续保持高分。
- **事件投影**（阶段 19++ / 20）：`event_projector` 把所有有 actor 的 `WorldEvent`
  按 importance 投影到该 actor 的记忆里：≥4 进 `short_term`，2-3 进 `working`
  （30 分钟过期）。让"看到的事"立刻能被检索到。

### 2.3 Plan（结构化输出）

模板：`backend/app/prompts/agent_decision/v1.jinja2`

prompt 由 7 个 block 拼接：

```text
profile_block      # 名字 / 职业 / 性格 / 长期目标
state_block        # 时间 / 精力 / 饥饿 / 社交 / 情绪
perception_block   # 周围实体 / 地点 / 自然事件 / 不可达提示
plan_block         # 今日 daily_summary + 当前 hourly segment + 当前 task
social_block       # social_need 与阈值；同场景熟人 / 别处熟人；工具提示
memory_block       # top-k 相关记忆
tool_catalog       # 按 entity_type 过滤后的工具描述
```

LLM 必须返回严格的 JSON：

```json
{
  "thought": "...",
  "emotion": "happy|sad|...",
  "tool_calls": [{"tool": "...", "arguments": {...}, "confidence": 0.8, "thought": "..."}],
  "memory_writes": [{"memory_type": "thought", "description": "...", "importance": 5}]
}
```

输出经 `AgentDecisionOutput` Pydantic 校验，最多取前 3 个 tool_calls + 前 4 个
memory_writes（防止 LLM 暴走）。

### 2.4 Execute（工具执行）

- `ToolExecutor.execute_batch(ctx, calls)` 串行执行（保证顺序与依赖）。
- 每个 `ToolResult` 携带：
  - `success` / `error` / `result` / `metadata`；
  - `memory_candidates`：工具自动产出的记忆候选（如 `request_interaction`
    被对方拒绝 → 写一条 `thought`，"我想找 X，但 ta 拒绝了"）。
- 工具的副作用直接修改 `EngineAgent` / DB / Redis；引擎下个 tick 看到。
- 失败工具不会让整个决策失败，只会被记录到 `ToolCallRecord`。

### 2.5 决策的执行模型：同步 / 异步 / 并发 / 副本

这一节回答 4 个常见的疑问：
- LLM 是在哪个进程里被调用的？
- worker 是单线程的吗？怎么并发的？
- 为什么用 asyncio 而不是多线程 / 多进程？
- 横向扩 worker 副本会不会导致状态冲突？

#### 2.5.1 同步模式 vs 异步模式

| 模式 | 何时用 | 行为 |
|------|--------|------|
| `TASK_QUEUE_MODE=async`（默认） | 生产 / dev | 主循环投递 `agent_decision` 任务到 RQ `vt:high`；worker 进程消费并跑 LLM；本轮 NPC 仍走规则兜底确保不静止；下一 tick 看到 worker 写回的状态。 |
| `TASK_QUEUE_MODE=sync` | 回归测试 / e2e | 主循环内直接 `await decide_with_llm`，全失败时再回退规则。 |

异步模式下 worker 进程**不持有 SimulationEngine 实例**，所以 `decide_with_llm`
中需要触达引擎的部分（如 emotion 同步）用 try/except 容错，避免炸 worker。

#### 2.5.2 worker 的并发模型（阶段 21+）

文件：`app/domain/tasks/worker.py`。

历史包袱：阶段 12 的 worker 跑的是 `rq.SimpleWorker`，串行消费——每次 LLM 调用
5–15s，整个 worker 吞吐只有 0.1–0.2 jobs/s，22 NPC × ~37 enqueues/s 的入队
节奏直接把队列打爆，绝大多数任务在 `deadline_seconds` 内被丢弃。玩家观感：
**只有少数 NPC 真正决策，其它 NPC 反复同样行为且没有记忆**。

阶段 21+ 改造为**单进程 + N 个 asyncio 协程槽**：

```python
# 极简伪代码（实际见 worker._async_worker_main）
sem = asyncio.Semaphore(concurrency)             # 默认 16 张"许可证"

async def _execute_one(task_id, job):
    async with sem:                               # 进门要拿许可证；满了就排队
        await runner._execute_task(task_id)       # 调 LLM、读写 DB

async def _async_worker_main():
    while not stopping:
        if len(pending) >= concurrency:
            await asyncio.wait(pending, FIRST_COMPLETED)   # 满载就等一个完
            continue
        job_id = await asyncio.to_thread(redis.blpop, queues, 1)
        asyncio.create_task(_execute_one(task_id, job))    # 不等它完，立即拉下一个
```

附加组件：
- **Mini Scheduler**（`_mini_scheduler_loop`）：每 1s 把 `ScheduledJobRegistry` 里
  到期的 `enqueue_in` job 回推到普通队列，让 `enqueue_retry(delay_seconds>0)` 可用
  （RQ 自带的 scheduler 依赖 `Worker.work(with_scheduler=True)`，我们已不再调用）。
- **Graceful drain**：收到 SIGINT/SIGTERM 后停止 BLPOP，再等已在飞的协程最多 30s
  完成，避免任务被强切。
- **TTL Worker 跑在同一 loop**：避免 SQLAlchemy AsyncEngine 跨 loop 报
  "Future attached to a different loop"（见
  `docs/开发手册/debug/20260501-task-queue-rq-asyncio-loop.md`）。

#### 2.5.3 关键概念辨析：进程 / 线程 / 协程槽位

| 名词 | 含义 | 我们的项目里 |
|------|------|------------|
| **进程**（process） | 一个独立的 OS 程序实例 | `backend`（FastAPI+引擎，1 份）、`worker`（队列消费者，1..N 份）、`postgres`、`redis` 各是一个独立进程 |
| **线程**（thread） | 进程内的执行流，共享内存；受 Python GIL 约束 | 我们 worker 进程**只用一条主线程**跑事件循环，BLPOP 用 `asyncio.to_thread` 短暂借线程 |
| **协程**（coroutine） | `async def` 函数对象，由事件循环切换；纳秒级开销 | 每个 `agent_decision` 任务是一个协程 |
| **协程槽位**（asyncio Semaphore） | 限制"同时在飞"的协程数量 | `Semaphore(16)` = 同进程内最多 16 个 LLM 决策并行 |

直观对比：

```text
旧 SimpleWorker（同进程、单线程、串行）：
  [LLM 1 ......][LLM 2 ......][LLM 3 ......]
  0s──10s        20s         30s
  吞吐 ~0.1 jobs/s

新并发 worker（同进程、单线程、16 协程槽）：
  [LLM  1 ........]
  [LLM  2 .........]
  [LLM  3 ......]
  ...
  [LLM 15 .........]
  [LLM 16 ........]
  0s ──── 10s
  吞吐 ~1.6-3.2 jobs/s（×16，受 LLM 端 RPM 约束）
```

LLM 调用 90% 时间都在等 HTTP 响应、CPU 几乎闲置。同一线程内塞 8 个 await
点，事件循环在等待时切别人，对总耗时几乎没影响、吞吐近线性 ×N。

#### 2.5.4 为什么是 asyncio 而不是多线程 / 多进程

| 方案 | 优点 | 我们项目里的劣势 |
|------|------|----------------|
| 多进程 | 绕开 GIL，CPU 密集任务并行 | LLM 是 I/O 密集不是 CPU 密集；每进程 50-100MB、独占 DB 连接池、单独 LLM client，重 |
| 多线程 | 切换成本低，I/O 时释放 GIL | 我们整套 I/O 栈是 async-native（asyncpg / httpx.AsyncClient / dashscope async SDK），同步版本要么不存在要么需要桥接；线程 + asyncio 混跑容易踩"future 跨 loop"问题 |
| **asyncio 协程槽位** ✅ | 每协程几 KB、纳秒级切换；与现有栈天然匹配；同 loop 内 SQLAlchemy / Redis 客户端可复用连接池 | 不能用于 CPU 密集（pgvector / 计算 BFS 路径都已经在 DB 侧或 numpy 层处理了，不在 worker 进程里） |

> 一句话：**asyncio 是我们这套技术栈的本命并发模型**——主循环、tools、DB、Redis、HTTP 全是 async/await 链路，多线程/多进程都是绕路。

需要更高吞吐时的扩容路径：
1. **垂直**：`TASK_QUEUE_WORKER_CONCURRENCY=16 → 32 → 64`，单进程更多协程槽；
   注意 ≥ 32 时必须同步把 `app/db/session.py` 的 `pool_size + max_overflow`
   一起扩到 ≥ 该数值的 2 倍，否则任务会卡在等 DB 连接。
2. **水平**：`docker compose up --scale worker=N`，多个 worker 容器各自跑 N 个槽位，
   通过共享 Redis 队列自然分流（BLPOP 原子，不会重复消费）；
3. 上限受 LLM provider RPM 与 PostgreSQL `max_connections` 约束。

#### 2.5.5 决策锁：同 NPC 同时只允许一份决策在飞

光扩 worker 还不够——22 NPC × `1 / ai_tick_minutes` 的入队节奏（≈37/s 仿真倍速下）
仍可能超过吞吐。我们额外用决策锁限制**入队速率**：

`engine._enqueue_decision_task` 在 enqueue 前先 `SETNX agent:{id}:decision_lock`
(TTL = `agent_decision_deadline_seconds + 60`)：

```text
SETNX 失败 → 跳过本次入队 + 不更新 last_decision_at → 下一 tick 再试
SETNX 成功 → insert tasks（幂等键去重） + RPUSH vt:high + 更新 last_decision_at
runner 完成 / deadline 触发 / 失败 → DEL 锁 → 下一 tick 立即可入队
worker 进程崩溃 → 锁 TTL 自动过期，引擎自然恢复
```

锁的语义是**"这个 NPC 已经有一份决策在飞"**，叠加 task 表的幂等键
`agent_decision:{id}:{step}` 形成双保险——SETNX 抢的是"飞行中"窗口、幂等键抢的是
"同一仿真步入库"窗口，两者覆盖所有重复情况。

效果：22 NPC 默认配置下入队稳态从 37/s 自适应压到 ~1.6-3.2/s（= 并发槽 × 平均完成率），
与吞吐对齐——所有 NPC 都能按 `ai_tick_minutes` 节奏拿到 LLM 决策与 memory_writes，
不再有"哑巴 NPC"。

#### 2.5.6 启动错峰

仅有决策锁还不够。所有 NPC 启动时 `last_decision_at = None`，第一个 tick 就**全员
同时**满足"AI tick"条件。哪怕队列总量不爆，第一波 burst 也会让前 16 个 NPC 把锁
全占了，剩下的等 TTL（150s+）才轮到，体感"前 16 个动得快、后面像睡着了"。

`engine._load_state` 末尾把 22 NPC 的初始 `last_decision_at` 按索引错开到
`[world_time - ai_interval, world_time)` 区间内：

```python
ai_interval = sim.ai_tick_minutes
humanlike = [a for a in self._agents.values() if not a.is_player]
n = max(1, len(humanlike))
for i, a in enumerate(humanlike):
    if a.last_decision_at is None:
        offset_min = (i * ai_interval) / n
        a.last_decision_at = (
            self._world_time - timedelta(minutes=ai_interval)
            + timedelta(minutes=offset_min)
        )
```

第一波决策呈流水线节奏分散到一个 `ai_tick_minutes` 区间内，并发槽位从启动开始就
能均匀填满，没有空转也没有挤爆。

#### 2.5.7 worker 多副本的状态一致性

worker 可以横向扩到 N 副本。下表说明为什么不会出现"多副本同步问题"：

| 风险 | 怎么避免 |
|------|---------|
| 同一任务被两个 worker 拿到 | Redis `BLPOP` 是原子操作，list 弹一次只给一个客户端 |
| 同一 NPC 同时被两份决策处理 | 决策锁 SETNX 是原子的，`tasks` 表的幂等键是唯一约束，重复入队会被 DB 拒掉 |
| 两个 worker 同时改同一 NPC 字段 | 每个工具用独立 AsyncSession 事务；冲突由 Postgres 行锁/隔离级别处理；工具内先 `await session.refresh(agent)` 再写 |
| 缓存（embedding / unreachable）跨副本不一致 | 缓存层一律放 Redis，单实例；副本只是无状态的"读 / 改 / 写"通道 |
| 决策结果回传给 backend | 全部走 DB——backend 下个 tick `reload_from_db` 自然看到 |

**关键设计**：所有"会被多写者修改"的状态都收敛到 Postgres / Redis 这两个有状态层；
backend 与 worker 都是无状态计算单元，只读 / 改 / 写共享存储，不互相 RPC、不
共享内存。这就是允许 worker 任意 scale 的根因。

#### 2.5.8 为什么不能"开多份 SimulationEngine"

有人可能会想："既然 worker 能多副本，为什么不让 backend 也起多份引擎来分摊压力？"

不行。引擎在我们架构里是**世界状态的唯一单写者**：
- `world_time` 由它单调推进；多份引擎 tick 同一个仿真会让 world_time 分裂；
- agents 内存镜像、邂逅扫描、WebSocket 广播全在引擎内部；
- `_persist_tick` 写 DB 时多写者会互相覆盖。

**而且也没必要**——LLM 决策从来不在引擎里跑，引擎只入队不调 LLM。"扩并发"的
正确姿势永远是扩 worker（垂直加协程槽位 / 水平加副本），而不是扩引擎。

将来真正需要"扩仿真"的场景是支持多个独立游戏世界（多用户、多存档同时运行），
方案是按 `simulation_id` 分片——每个仿真自己一份引擎实例、共享同一 worker pool，
而不是同一仿真起两份引擎。

## 3. 工具目录（按用途分组）

工具用 `ToolRegistry` 集中注册，按 `allowed_entity_types` 给 LLM 看不同子集。
完整签名见 `ToolCalling工具契约V1.md`。

```text
社交（dialogue 模块） — social_tools.py
├── request_interaction   - 向 ≤4 格内实体发起 chat / help / trade 请求
├── socialize             - 自动从附近熟人/陌生人挑一个发请求
├── go_to_entity          - 跨场景前往某 NPC + 移动追踪（阶段 19+++）
└── end_chat              - 主动结束当前对话

生活（life 模块） — life_tools.py
├── work_at_location      - 工作 / 营业（busy_until + interruptible=False）
├── have_meal             - 用餐：hunger ↓
├── rest_at               - 休息：energy ↑
├── browse_shop           - 闲逛商铺
└── observe_environment   - 写一条 emotion 记忆

世界 — world_tools.py
├── move_to_location      - 走到地点（A* + 跨场景 portal）
├── interact_with_object  - 拾取 / 使用世界对象
└── pick_item             - 采摘 / 钓鱼

记忆 — memory_tools.py
├── write_memory          - 主动写一条记忆
└── search_memory         - 在自己记忆中检索

状态 — state_tools.py
├── update_emotion        - 修改 emotion
└── wait                  - 主动等待

动物 — animal_tools.py（仅动物 NPC）
├── react_to_pet
└── make_sound
```

## 4. Engine 层的"自主驱动力"

光靠 LLM 不够：① 5 分钟才决策一次，社交意图衰减太慢；② 没有外部刺激
LLM 也想不到要主动社交。所以引擎层有 3 个独立的"自主驱动力"：

### 4.1 `_evolve_basic_needs`（每 world tick）

让 `social_need / hunger / energy` 随仿真分钟自然变化：

| 状态 | social_need | hunger | energy |
|------|-------------|--------|--------|
| IDLE / MOVING / WAITING | +growth/min | +growth/min | -decay/min |
| SLEEPING | 暂停 | 暂停 | 恢复 ×4 |
| EATING | 暂停 | 衰减 ×6 | 暂停 |
| RESTING | 暂停 | 暂停 | 恢复 ×3 |
| CHATTING | 衰减 ×2 | 暂停 | 暂停 |

参数：
- `NEEDS_SOCIAL_GROWTH_PER_MINUTE`（默认 0.0030 → 阈值 0.40 ≈ 130 分钟）
- `NEEDS_HUNGER_GROWTH_PER_MINUTE`（默认 0.0020）
- `NEEDS_ENERGY_DECAY_PER_MINUTE`（默认 0.0015）
- `NEEDS_SOCIAL_DECAY_AFTER_CHAT`（默认 0.55，单次社交后一次性扣的比例）

### 4.2 `_scan_social_encounters`（每 N 仿真分钟）

主动配对附近空闲、社交需求高的 NPC，绕开 LLM 决策延迟：

```text
filter:
- entity_type=human 且 非 player
- state ∈ {IDLE, WAITING} 且 path 空
- busy_until 未到
- 不在邂逅冷却中
- social_need ≥ threshold

pair:
按 social_need 降序遍历 initiator
对每个 initiator 找同场景 ≤ encounter_distance 内最近的 idle 同类
→ asyncio.create_task → InteractionService.create_request(kind=chat)
→ 评估器走完 accept/soft/hard 后广播 + 必要时启动 NPC-NPC 对话循环
```

参数：
- `SOCIAL_ENCOUNTER_SCAN_MINUTES`（默认 2）
- `SOCIAL_ENCOUNTER_DISTANCE`（默认 5）
- `SOCIAL_ENCOUNTER_COOLDOWN_MINUTES`（默认 10）

### 4.3 `_refresh_pursuits`（每 world tick）

`go_to_entity` 工具发起的"主动追踪"：
- 距离 ≤ 2 → 清 pursuit + `last_decision_at = None` → 下个 tick 立即重决策；
- path 走空但还没到 → 重新规划（处理目标移动）；
- 目标消失 / CHATTING → 清 pursuit。

`_decide_all` 在 pursuit 期间跳过该 NPC，避免规则/LLM 重选目标清掉 path。

## 5. 规则版兜底（rule_agent.py）

LLM 没配置、调用失败、worker 还没回写、async 模式下首轮，都走规则版：

```text
storm_active        → _decide_storm_shelter（跑去最近室内）
nearby_fires        → _decide_fire_response（按性格选灭火 / 围观 / 逃离）
否则按 schedule_template:
  pick_current_slot(world_time) → ScheduleSlot(activity, location_id)
  _choose_goal_tile_for_slot   → (target_scene, target_tile)
  跨场景：_find_inter_scene_route BFS → 第一跳 portal → A*
  同场景：直接 A* 到 target
路径不可达 / portal 缺失 / target 黑名单 → _wander_within_scene_or_wait
                                          + metadata.stuck=True
                                          + unreachable_location_id
```

`metadata.stuck=True` → engine 不写 `last_decision_at`，下一 tick 立即重试 +
把 location_id 加入 5 分钟黑名单（`agent:{id}:unreachable`）。

## 6. 不可达兜底（阶段 19++）

| 来源 | 写入时机 |
|------|---------|
| LLM 工具 (`move_to_location` / `request_interaction` / `go_to_entity`) | 工具执行失败时 `world_tools._mark_unreachable` 写 Redis |
| rule_agent 结构性失败 | 在 `AgentDecision.metadata` 标 `unreachable_location_id` → engine `_apply_decision` 推入 `_pending_unreachable` → tick 末 flush |

key：`agent:{agent_id}:unreachable`（Redis SET，TTL 300s）

读取：
- `agent_decision._perceive` → 过滤 LLM 看到的 NPC / Location；
- `engine._get_unreachable_locations` → 内存镜像传给 `decide_human_action`，
  规则版命中黑名单的 slot 直接转 `_wander_within_scene_or_wait`。

> **wander 兜底也必须写黑名单**（2026-05-02 修复）：rule_agent 的
> `_make_recovery_or_wait` 在找到 recovery_tile 时返回 wander 决策——这种
> "找到了近邻空地，但走不到真正目标"的情况，**必须**在 metadata 里带
> `unreachable_location_id`，否则下个 ai_tick 又会原样选同一目标，引发
> "绕行→到达→绕行→到达"刷屏循环。详见
> `docs/开发手册/debug/20260502-recovery-wander-loop.md`。
>
> `stuck` 与 `unreachable_location_id` 的协议：
> - `stuck=True` → 引擎清 `last_decision_at` 立即重决策（仅用于 wait 兜底）；
> - `unreachable_location_id` → 写 5 分钟黑名单（wander / wait 都应该带）；
> - 两者独立：wander 分支只带 unreachable，不带 stuck（让 NPC 按 ai_tick
>   节奏推进，避免每 tick 重复决策）。

> **真根因警惕：「物理不可达」要在世界生成层就堵死**（2026-05-02 二修）：
> 黑名单 / wander 兜底只能掩盖"被障碍墙挡住"这种**逻辑上仍然可达**的场景。
> 当目标本身就在地图外（``Location.entry_tiles`` 越界、``portal.from_tile``
> 落在 ``in_bounds=False`` 的格上），任何 BFS / A* 都会永远失败、整个
> 黑名单循环就变成"真不可达 → 假装在闲逛 → 又重决策 → 又判定不可达"
> 的无限刷屏。22 NPC 测试时观察到的多名 NPC 进不了家 / 工作地、几乎
> 不产生记忆，根因正是默认 outdoor 90×60 装不下 BUILDING/HOME 的
> 硬编码 120×90 布局。
>
> 修法分两层：
> 1. ``world_gen/outdoor.py`` 入口 fail-fast（``_assert_layout_within_bounds``）
>    + ``WORLD_GEN_OUTDOOR_DEFAULT_*`` 默认改 120/90 并 ``ge=120/90``；
> 2. ``backend/tests/test_world_gen_reachability.py`` 在 CI 跑端到端
>    "中央广场 → outdoor portal → 室内 entry" 全链路 A*，任何破坏
>    世界连通性的改动立即失败。
>
> 详见 `docs/开发手册/debug/20260502-world-bounds-mismatch.md`。

## 7. 调参指南：让 NPC 更社交、更"过日子"

> 默认值已经过一轮调整，下面列出的是各参数对体验的影响和推荐区间。

### 7.1 NPC 决策频率

| 参数 | 默认 | 调小→效果 | 调大→效果 | 推荐区间 |
|------|------|-----------|-----------|----------|
| `SIMULATION_AI_TICK_MINUTES` | 3 | 决策更频繁，反应更灵 | LLM 调用减少，可省成本 | 2-5 |
| `SIMULATION_WORLD_TICK_HZ` | 5 | 移动更平滑 | 节省 CPU | 3-10 |

### 7.2 社交触发

| 参数 | 默认 | 影响 |
|------|------|------|
| `SOCIAL_NEED_TRIGGER_THRESHOLD` | 40 | 越低 NPC 越早觉得"该社交了"。低于 30 几乎一直主动；高于 60 NPC 会很冷漠。 |
| `NEEDS_SOCIAL_GROWTH_PER_MINUTE` | 0.0030 | 决定 social_need 上升速度。0.0030 大约 130 仿真分钟从 0 到 0.4 阈值。 |
| `NEEDS_SOCIAL_DECAY_AFTER_CHAT` | 0.55 | 单次社交结束扣的比例。越大越容易再次饿（更频繁社交）。 |
| `SOCIAL_ENCOUNTER_SCAN_MINUTES` | 2 | 邂逅扫描间隔。越小越频繁配对。 |
| `SOCIAL_ENCOUNTER_DISTANCE` | 5 | 配对的曼哈顿距离上限。越大越容易撞上。 |
| `SOCIAL_ENCOUNTER_COOLDOWN_MINUTES` | 10 | 同 NPC 邂逅触发冷却。越小越主动。 |

### 7.3 生活节奏

| 参数 | 默认 | 影响 |
|------|------|------|
| `NEEDS_HUNGER_GROWTH_PER_MINUTE` | 0.0020 | 越大 → NPC 越频繁找饭吃（触发 `have_meal`）。 |
| `NEEDS_ENERGY_DECAY_PER_MINUTE` | 0.0015 | 越大 → 越频繁打瞌睡 / 选 `rest_at`。 |

### 7.4 拒绝 / 互动协议

| 参数 | 默认 | 影响 |
|------|------|------|
| `INTERACTION_STRANGER_FAMILIARITY_THRESHOLD` | 0.2 | 陌生人 / 熟人界限。越小 NPC 越"开放"，越多陌生人能成功搭话。 |
| `INTERACTION_HIGH_PRIORITY_THRESHOLD` | 8 | 高优先级（≥ 此值）任务硬拒打扰。降低 → NPC 更容易被打断。 |
| `SOCIAL_COOLDOWN_AFTER_REFUSAL_MINUTES` | 30 | 连续硬拒后冷却。越小越容易"被骚扰"，越大保护"我说过不"。 |

### 7.5 记忆投影 / 合并 / 沉思（阶段 20）

| 参数 | 默认 | 影响 |
|------|------|------|
| `MEMORY_PROJECTION_DEFAULT_MIN_IMPORTANCE` | **2** | 不在白名单事件需 ≥ 此重要度才落记忆（阶段 20：4→2）。降低 → 更多事件能影响决策。 |
| `MEMORY_PROJECTION_DEFAULT_DEBOUNCE_MINUTES` | 30 | 同 (actor, type, target) 的去重窗口。 |
| `MEMORY_DIALOGUE_PER_MESSAGE` | true | 对话每条消息都为参与者写一条 chat 记忆（working scope）。 |
| `MEMORY_CONSOLIDATION_ENABLED` | true | 每仿真日合并低重要度 archived 记忆为 long_term summary。 |
| `MEMORY_CONSOLIDATION_MIN_BUCKET_SIZE` | 3 | 主题桶 ≥ 此条数才触发 LLM 合并（避免过碎）。 |
| `MEMORY_RUMINATION_ENABLED` | true | 每个 NPC 每仿真日抽样重要长期记忆产生新 thought。 |
| `MEMORY_RUMINATION_IMPORTANCE_THRESHOLD` | 7 | 沉思候选记忆的最低重要度。 |

## 8. Prompt 调优要点

模板：`backend/app/prompts/agent_decision/v1.jinja2`

强制三条**必须遵守的硬约束**：
1. 输出严格 JSON。
2. tool_calls 1-3 个，且只能来自下方目录。
3. 有"当前任务"时，工具必须服务于该任务。

社交决策提示走"三步策略"：
1. 同场景熟人 → `socialize` / `request_interaction`；
2. 别处熟人 → `go_to_entity` 主动过去；
3. 完全没熟人 → `socialize` 自动降级到附近陌生邻居。

生活决策提示按状态触发：
- `hunger > 0.5` → `have_meal`；
- `energy < 0.3` → `rest_at`；
- 当前任务为工作 → `work_at_location`；
- 闲暇 + 在商铺 → `browse_shop`；
- 不要轻易 `wait`，宁可 `observe_environment` 写一条记忆。

## 9. 调试 / 排查清单

NPC 一直 `WAITING` / `IDLE` 不动？
1. 看 `/api/observability/llm-calls`：本 NPC 最近一条 LLM 调用时间。
2. 看 `agent.last_decision_at`：是否被 ai_tick 间隔卡住。
3. 看 `agent.busy_until`：是否被 work / eat / rest 锁住。
4. 看 `agent:{id}:unreachable`：是否日程目标全被拉黑。
5. 看 `tool_calls` 表：最近调用的工具是否全部失败。
6. 看 RQ 队列：是不是 worker 挂了导致 LLM 决策不回写。
7. 看 Redis `agent:{id}:decision_lock`：锁是否被遗留（worker 崩溃但未清理）；
   `redis-cli ttl agent:<id>:decision_lock` 看剩余秒数；锁 TTL = deadline+60s，
   理论上不会卡 NPC > ~150s。

22 NPC 多数表现一致 / 没有记忆产生？典型症状即"决策吞吐被串行 worker 卡死"。
1. 看 `/api/observability/tasks?type=agent_decision&status=failed`：是不是大量
   `TASK_DEADLINE_EXCEEDED`。
2. 看 worker 日志启动行：`concurrent rq worker ready concurrency=N`，N 应 ≥ 8（默认 16）。
3. 调 `TASK_QUEUE_WORKER_CONCURRENCY` 与 `AGENT_DECISION_DEADLINE_SECONDS`，
   并视情况扩 `docker compose up --scale worker=2`；
   注意 concurrency ≥ 32 时必须同步扩 DB 池（`session.py` 的 `pool_size + max_overflow`）。
4. 复盘见 `docs/开发手册/debug/20260502-npc-decision-concurrency.md`。

NPC 不社交？
1. 看 `agent.social_need`：是不是低于阈值。
2. 看 `social_block` 在 prompt 里的内容：是不是"附近暂无熟人"+"暂无外场景熟人"。
3. 看 `_scan_social_encounters` 日志：是不是没人满足条件。
4. 看 Relationship 表：是不是 seed 数据里没有任何 familiarity > 0.2 的边。
5. 调小 `SOCIAL_NEED_TRIGGER_THRESHOLD` / 调大 `NEEDS_SOCIAL_GROWTH_PER_MINUTE`。

NPC 反复 stuck 在 "无路通往目标场景"？
1. 看 `agent.current_goal` / `slot.location_id`：目标在哪个场景。
2. 看 `Portal` 表：当前场景到目标场景是否有任何 portal 链。
3. 多跳 BFS 默认最多 4 跳；超过需要修改 `rule_agent._find_inter_scene_route`。

## 10. 记忆生命周期（阶段 20）

> 详见专题：[`docs/实施方案/记忆系统重构方案.md`](../实施方案/记忆系统重构方案.md)。

```text
            投影 / 工具写入 / 对话每条消息
                    │
   importance ≥ 4  ▼   importance < 4
   ┌──────── short_term ─────────┐  ┌─── working ───┐
   │  TTL 24 仿真小时             │  │ TTL 30 分钟    │
   └─────────────┬────────────────┘  └────────┬──────┘
       importance ≥ 7│ <7 / working                │ TTL 过期
                     ▼                            ▼
                long_term                     archived
                                                  │ 每仿真日 22:00
                                                  ▼
                                       memory_consolidation
                                                  │ 同主题 ≥3 条
                                                  ▼
                                       long_term summary
                                       原文 → consolidated
                                       summarized_into_id 指向 summary
                ▲
                │ memory_rumination：每 NPC 每 24 仿真小时
                │ 抽样 importance≥7 的长期记忆 → LLM 重新感悟 → thought
                │ 同时给原记忆 importance+1 + 刷新 last_accessed_at
```

**关键设计**：

- **没有任何记忆会被物理删除**。"遗忘" = 退出默认检索（`archived` / `consolidated`）。
- **summary 永远可追溯**：通过 `evidence_memory_ids` 链回原文（即 `summarized_into_id` 指向 summary 的那一批）。
- **访问强化**：search 命中即刷 `last_accessed_at`，`recency` 取 `max(创建半衰=168h, 访问半衰=24h)`，让被频繁回忆的旧记忆继续保持高分。

## 11. 关键文件索引

| 文件 | 作用 |
|------|------|
| `backend/app/llm/agent_decision.py` | LLM 决策主入口（PRPE 流程） |
| `backend/app/llm/tools/` | 工具实现（social / life / world / memory / state / dialogue / animal） |
| `backend/app/llm/tools/registry.py` | 工具注册表 + entity_type 过滤 |
| `backend/app/prompts/agent_decision/v1.jinja2` | 决策 prompt 模板 |
| `backend/app/domain/simulation/engine.py` | 主循环 + 自主驱动力 + persist |
| `backend/app/domain/simulation/rule_agent.py` | 规则版兜底（多跳路由 + 不可达兜底） |
| `backend/app/domain/simulation/schedule.py` | schedule_template 解析 |
| `backend/app/domain/dialogue/interaction_evaluator.py` | 请求-同意-拒绝 评估 |
| `backend/app/domain/dialogue/npc_dialogue_loop.py` | NPC-NPC 对话循环 |
| `backend/app/domain/memory/event_projector.py` | WorldEvent → 个人记忆投影 |
| `backend/app/domain/memory/consolidation.py` | 阶段 20：archived → long_term summary 合并 |
| `backend/app/domain/memory/rumination.py` | 阶段 20：重要长期记忆抽样沉思 |
| `backend/app/domain/memory/reflection.py` | 反思 + 日结 |
| `backend/app/services/memory_service.py` | write / search / 访问强化 |
| `backend/app/services/interaction_service.py` | 交互请求生命周期管理 |
| `backend/app/core/config.py` | 所有可调参数集中定义 |
