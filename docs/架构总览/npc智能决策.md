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

### 2.5 同步 vs 异步

| 模式 | 何时用 | 行为 |
|------|--------|------|
| `TASK_QUEUE_MODE=async`（默认） | 生产 / dev | 主循环投递 `agent_decision` 任务到 RQ `vt:default`；worker 进程消费并跑 LLM；本轮 NPC 仍走规则兜底确保不静止；下一 tick 看到 worker 写回的状态。 |
| `TASK_QUEUE_MODE=sync` | 回归测试 / e2e | 主循环内直接 `await decide_with_llm`，全失败时再回退规则。 |

异步模式下 worker 进程**不持有 SimulationEngine 实例**，所以 `decide_with_llm`
中需要触达引擎的部分（如 emotion 同步）用 try/except 容错，避免炸 worker。

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
| rule_agent 结构性失败 | 在 `AgentDecision.metadata` 标 stuck → engine `_apply_decision` 推入 `_pending_unreachable` → tick 末 flush |

key：`agent:{agent_id}:unreachable`（Redis SET，TTL 300s）

读取：
- `agent_decision._perceive` → 过滤 LLM 看到的 NPC / Location；
- `engine._get_unreachable_locations` → 内存镜像传给 `decide_human_action`，
  规则版命中黑名单的 slot 直接转 `_wander_within_scene_or_wait`。

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
