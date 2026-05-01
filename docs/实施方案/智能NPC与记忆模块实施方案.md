# 智能 NPC 与记忆模块实施方案

> 模块定位：人类 NPC、动物 Agent、自主决策、成长记忆、关系网络  
> 核心原则：所有 Agent 通过结构化工具调用影响世界，LLM 不直接修改状态

---

## 1. 设计目标

本模块负责让人类 NPC、狗、猫等实体具备可解释的自主行为。Agent 需要拥有：

- 固定初始模型：身份、外貌、性格、职业、关系、生活习惯。
- 实时状态：位置、情绪、体力、饥饿、当前行动。
- 自主决策：基于自身状态、记忆、关系和环境选择行为。
- 成长记忆：经历事件后形成短期记忆、长期记忆和每日总结。
- 关系变化：对玩家、NPC、动物形成不同好感、信任、恐惧或厌恶。

---

## 2. Agent 类型

| 类型 | 示例 | 能力特点 |
|------|------|----------|
| Human NPC | 小明、小芳、小王 | 计划、对话、工作、学习、社交 |
| Animal Agent | 狗、猫 | 移动、感知、情绪反应、亲近/躲避 |
| Player Agent | 玩家角色 | 用户控制，但仍被世界和 NPC 感知 |

动物和玩家在底层都继承 AgentEntity，只是工具集和控制权不同。

---

## 3. 初始种子 Agent

第一版世界预置：

### 3.1 人类 NPC

| ID | 名字 | 职业/身份 | 性格 | 关系 |
|----|------|-----------|------|------|
| `npc_xiaoming` | 小明 | 高中生 | 腼腆、认真、好奇 | 小芳的邻居，认识小王 |
| `npc_xiaofang` | 小芳 | 咖啡店店员 | 热情、细心、外向 | 豆豆的主人，认识小明 |
| `npc_xiaowang` | 小王 | 程序员 | 理性、内向、喜欢咖啡 | 常去小芳工作的咖啡店 |
| `npc_lina` | 林娜 | 医生 | 冷静、负责 | 认识多数居民 |
| `npc_chenbo` | 陈伯 | 杂货店老板 | 和善、健谈 | 镇上老居民 |
| `npc_ayan` | 阿言 | 花店店主 | 敏感、艺术化 | 喜欢猫，常去河边写生 |

### 3.2 动物 Agent

| ID | 名字 | 类型 | 性格 | 关系 |
|----|------|------|------|------|
| `animal_dog_doudou` | 豆豆 | 狗 | 亲人、活泼 | 小芳的狗 |
| `animal_dog_heihei` | 黑黑 | 狗 | 警觉、护家 | 陈伯店门口常驻 |
| `animal_cat_mimi` | 咪咪 | 猫 | 独立、爱晒太阳 | 阿言常喂它 |
| `animal_cat_xiaobai` | 小白 | 猫 | 胆小、好奇 | 常在学校附近 |

---

## 4. 核心数据模型

### 4.1 AgentProfile

固定或低频变化的人物/动物基础模型。

```json
{
  "id": "npc_xiaofang",
  "entity_type": "human",
  "name": "小芳",
  "age": 24,
  "gender": "female",
  "appearance": {
    "hair": "short_black",
    "clothes": "cafe_uniform",
    "sprite_sheet": "xiaofang.png"
  },
  "occupation": "咖啡店店员",
  "personality": ["热情", "细心", "外向"],
  "background": "小芳在镇上的咖啡店工作，喜欢和顾客聊天。",
  "lifestyle": "早上7点起床，8点到咖啡店，晚上9点回家。",
  "long_term_goals": ["经营好咖啡店", "照顾豆豆"],
  "home_location_id": "loc_xiaofang_home"
}
```

### 4.2 AgentRuntimeState

高频变化状态。

```json
{
  "agent_id": "npc_xiaofang",
  "scene_id": "scene_town_outdoor",
  "position": { "x": 28, "y": 12 },
  "current_action": "walking_to_work",
  "emotion": "calm",
  "energy": 80,
  "hunger": 20,
  "social_need": 50,
  "status_effects": [],
  "current_goal": "去咖啡店上班"
}
```

### 4.3 Relationship

```json
{
  "from_agent_id": "npc_xiaofang",
  "to_entity_id": "npc_xiaowang",
  "familiarity": 65,
  "trust": 55,
  "affection": 40,
  "fear": 0,
  "last_interaction_at": "2026-04-30T09:30:00",
  "summary": "小王经常来咖啡店买美式咖啡。"
}
```

### 4.4 Memory

```json
{
  "id": "mem_001",
  "agent_id": "npc_xiaofang",
  "memory_type": "event",
  "scope": "long_term",
  "subject": "小王",
  "predicate": "ordered",
  "object": "美式咖啡",
  "description": "小王在咖啡店点了一杯美式咖啡。",
  "importance": 5,
  "emotional_valence": 1,
  "ttl_minutes": null,
  "embedding": [],
  "created_at": "2026-04-30T09:30:00",
  "last_accessed_at": "2026-04-30T09:30:00"
}
```

---

## 5. 记忆分层

| 层级 | 存储 | 生命周期 | 内容 |
|------|------|----------|------|
| Working Context | 进程内 | 当前决策 | 当前感知、当前目标、可用工具 |
| Short-term Memory | Redis | 10 分钟-24 小时 | 最近看见的人、刚发生的事件、当前对话 |
| Long-term Memory | PostgreSQL | 长期 | 重要事件、关系、承诺、偏好 |
| Semantic Memory | pgvector | 长期 | 可语义召回的事件、对话、总结 |
| Daily Summary | PostgreSQL | 每天生成 | 一天经历总结、遗忘后的摘要 |

### 5.1 遗忘规则

1. 低重要度短期事件过期后直接删除。
2. 中重要度事件在一天结束时压缩进 Daily Summary。
3. 高重要度事件保留为长期记忆。
4. 与关系变化相关的事件同步更新 Relationship。

### 5.2 重要度评分

```text
importance =
  base_event_score +
  emotion_weight +
  relationship_weight +
  novelty_weight +
  danger_weight
```

示例：

- 普通路过：1-2 分。
- 与朋友聊天：4-6 分。
- 被狗咬或落水：8-10 分。
- 玩家承诺明天见面：7-9 分。

---

## 6. 自主决策 Tool Calling

LLM 不直接返回自然语言行动，而是选择工具。

### 6.0 决策管线流程（当前实现）

```text
perceive()
  ├── 读取附近实体、当前场景地点
  └── 读取 Redis 不可达黑名单（agent:{id}:unreachable）并过滤感知列表

asyncio.gather(
  retrieve()    → embed(perception_query) + pgvector 记忆检索
  plan_ctx()    → get_current_context()（daily_plan / task_decomp / segment）
)

chat_json()     → qwen-plus / qwen-max，返回 AgentDecisionOutput

executor.execute_batch()
  ├── 成功 → 更新引擎内存态、写 AgentAction、触发世界事件
  └── TARGET_NOT_REACHABLE → _mark_unreachable() → Redis SADD TTL=300s
```

**并行化优化**：`retrieve` 与 `plan_ctx` 通过 `asyncio.gather` 并发执行，
不相互阻塞，节省约 6s/决策周期。

### 6.1 Human Agent 工具

```json
[
  {
    "name": "move_to_location",
    "description": "移动到指定地点",
    "parameters": {
      "location_id": "string",
      "reason": "string"
    }
  },
  {
    "name": "interact_with_object",
    "description": "与世界物品互动",
    "parameters": {
      "object_id": "string",
      "interaction_type": "string"
    }
  },
  {
    "name": "wait",
    "description": "等待一段时间",
    "parameters": {
      "duration_minutes": "integer",
      "reason": "string"
    }
  },
  {
    "name": "request_interaction",
    "description": "（阶段 19）发起 chat / help / trade 请求，需对方接受",
    "parameters": {
      "target_entity_id": "string",
      "kind": "chat | help | trade",
      "reason": "string",
      "priority": "integer (0..10)"
    }
  },
  {
    "name": "socialize",
    "description": "（阶段 19）基于好感度自动找熟人发起 chat",
    "parameters": {
      "topic": "string",
      "only_friends": "boolean",
      "max_distance": "integer"
    }
  },
  {
    "name": "end_chat",
    "description": "（阶段 19）主动结束当前对话",
    "parameters": {
      "reason": "string",
      "target_entity_id": "string?"
    }
  },
  {
    "name": "work_at_location",
    "description": "（阶段 19）专注工作 / 学习 / 营业，期间不接受打扰",
    "parameters": {
      "location_id": "string",
      "duration_minutes": "integer",
      "activity": "string"
    }
  },
  {
    "name": "have_meal",
    "description": "（阶段 19）吃饭，降低饥饿、改善心情",
    "parameters": {
      "food_object_id": "string?",
      "duration_minutes": "integer"
    }
  },
  {
    "name": "rest_at",
    "description": "（阶段 19）休息恢复精力",
    "parameters": {
      "location_id": "string?",
      "duration_minutes": "integer",
      "reason": "string"
    }
  },
  {
    "name": "browse_shop",
    "description": "（阶段 19）在商铺 / 集市闲逛",
    "parameters": {
      "location_id": "string",
      "interest": "string"
    }
  },
  {
    "name": "observe_environment",
    "description": "（阶段 19）写一条带情绪的小记忆",
    "parameters": {
      "description": "string",
      "emotion": "string"
    }
  }
]
```

### 6.2 Animal Agent 工具

```json
[
  {
    "name": "approach_entity",
    "parameters": {
      "target_entity_id": "string",
      "emotion": "curious | happy | cautious"
    }
  },
  {
    "name": "avoid_entity",
    "parameters": {
      "target_entity_id": "string",
      "reason": "fear | dislike | danger"
    }
  },
  {
    "name": "make_sound",
    "parameters": {
      "sound": "bark | meow | whine | purr"
    }
  },
  {
    "name": "react_to_touch",
    "parameters": {
      "reaction": "enjoy | tolerate | escape | threaten"
    }
  }
]
```

### 6.3 决策输出 Schema

```json
{
  "thought": "小芳想先去咖啡店准备开门，因为现在快到上班时间了。",
  "selected_tool": "move_to_location",
  "arguments": {
    "location_id": "loc_hobbs_cafe",
    "reason": "准备上班"
  },
  "emotion": "focused",
  "memory_writes": [
    {
      "type": "thought",
      "description": "小芳记得今天要照顾咖啡店。"
    }
  ]
}
```

---

## 7. 接口设计

权威接口表与字段定义以 `数据模型与接口契约V1.md` 为准；本节给出本模块视角的概览：

| 方法 | 路径 | 状态 | 说明 |
|------|------|------|------|
| `GET` | `/api/agents` | ✅ | 获取 Agent 列表 |
| `GET` | `/api/agents/{id}` | ✅ | 获取 Agent 详情 |
| `PATCH` | `/api/agents/{id}/profile` | ✅ | 修改初始模型（部分字段） |
| `GET` | `/api/agents/{id}/state` | ✅ | 获取运行状态 |
| `GET` | `/api/agents/{id}/memories` | ✅ | 查询记忆 |
| `GET` | `/api/agents/{id}/relationships` | ✅ | 查询关系 |
| `POST` | `/api/agents/{id}/memory/search` | ✅ | 记忆检索（三因素评分） |
| `POST` | `/api/agents/{id}/reflect` | ✅ 阶段二 | 手动触发反思，返回新写入的 thought |
| `POST` | `/api/agents/{id}/decide` | ✅ 阶段十五 | 手动单步决策（调试 / E2E）；走 `decide_with_llm` 完整管线 |

---

## 8. 验收标准

1. 初始种子包含 6 个人类 NPC、2 只狗、2 只猫。
2. 每个 Agent 有初始模型、运行状态、关系网络和记忆。
3. NPC 行动由 tool calling 结构化输出驱动（LLM 优先，规则兜底）。
4. 动物能自主反应，不只是地图装饰。
5. 一天结束（游戏内 22:30 后）能生成 Daily Summary，并处理短期记忆遗忘。
6. 玩家和 NPC 的互动会影响关系和后续行为。
7. 反思可通过 `POST /api/agents/{id}/reflect` 手动触发，产出 thought 必须 `evidence_memory_ids` 引用至少 2 条已有事件 / 对话记忆。
8.（阶段 19）NPC 头顶根据状态显示富气泡：CHATTING💬 / WORKING💼 / EATING🍴 / RESTING☕ / SLEEPING💤 / AWAITING_RESPONSE❓ / BUSY_REFUSING❌。
9.（阶段 19）玩家点击 NPC 进入 ChatPanel 时先发送请求评估，对方接受才能输入；软拒显示自然台词；硬拒显示原因。
10.（阶段 19）NPC-NPC 自主对话由请求-接受协议触发，双方轮流由 LLM 驱动，最多 10 轮，旁观者前端能看到台词气泡。

---

## 9. 交互请求协议（阶段 19）

### 9.1 状态机

```
IDLE ─request_interaction→ AWAITING_RESPONSE ─accepted→ CHATTING
                          ├─soft_declined→ BUSY_REFUSING(3s) → IDLE
                          └─hard_declined→ IDLE
CHATTING ─end_chat / 10 turns→ IDLE
WORKING / EATING / RESTING ─busy_until 到期→ IDLE
```

### 9.2 分级拒绝策略

| 触发条件 | 决策 |
|----------|------|
| 目标 SLEEPING / current_priority ≥ 8 / interruptible=False | hard_decline |
| 陌生人（familiarity < 0.2）+ 目标忙碌 | hard_decline |
| 熟人 + 目标忙碌 | soft_decline（LLM 生成台词，规则兜底） |
| 玩家发起 + fear > 0.6 | soft_decline |
| 其他 | accept |

### 9.3 数据模型

`InteractionRequest`：
```
id, requester_id, target_id, kind('chat'|'help'|'trade'),
status('pending'|'accepted'|'declined'|'expired'|'cancelled'),
reason, decline_kind('soft'|'hard'|null), npc_line,
requester_priority, target_priority_at_request,
created_at, resolved_at, expires_at
```

`AgentState` 新增字段：`busy_until / interruptible / current_priority / last_social_at`。

### 9.4 接口

- `POST /api/players/me/interaction-requests`：玩家发起请求，同步返回 accepted / declined。
- `POST /api/players/me/interaction-requests/{id}/cancel`：取消 pending。
- WebSocket 事件：`interaction.request_pending` / `interaction.accepted` / `interaction.declined` / `interaction.cancelled`。

### 9.5 NPC-NPC 自主对话循环

- 触发：双方 entity_type=human，且 `request_interaction(kind=chat)` 评估为 accept。
- 派发：`InteractionService` `asyncio.create_task` 启动 `npc_dialogue_loop.run_npc_dialogue`。
- 双方轮流调 LLM 生成台词，prompt 含双方人物档案 + 关系摘要 + 上一轮台词。
- 任一方 LLM 返回 `end_chat=true` 或满 10 轮强制结束，硬超时 60 秒。
- 每轮台词广播 `dialogue.npc_to_npc_message`，前端在说话者头顶显示气泡。
- 结束时双方写 chat 记忆 + 应用 `RelationshipChange`。

### 9.6 安全护栏

- 同一 NPC 对同一对方连续 2 次硬拒后，30 仿真分钟内冷却（Redis 计数器）。
- 接受成功后冷却清零。
- 工具层 `request_interaction` 在冷却生效时直接返回 STATE_CONFLICT，不再投递评估。

---

## 10. 自主社交闭环（阶段 19+）

光把"请求-接受协议"接好还不够——若没有持续的内驱动力，NPC 仍然只会按 schedule 走。
本节把"想找人 → 出手 → 落库"三段闭环的所有齿轮都串起来。

### 10.1 基础需求自然演化（`SimulationEngine._evolve_basic_needs`）

每个 world tick 根据流逝的仿真分钟数累加：

```
social_need += needs_social_growth_per_minute  · Δt   (默认 0.0010 / 分钟)
hunger      += needs_hunger_growth_per_minute  · Δt   (默认 0.0008 / 分钟)
energy      -= needs_energy_decay_per_minute   · Δt   (默认 0.0005 / 分钟)
```

| 当前状态 | 行为 |
|---------|------|
| `SLEEPING` | energy 加速回升；社交/饥饿停滞 |
| `EATING` | hunger 快速衰减 |
| `RESTING` | energy 回升 |
| `CHATTING` | social_need 快速衰减（社交需求被持续满足） |
| 其他 | 默认按上面公式累积 |

完成一次对话（NPC-NPC dialogue_loop / 玩家 talk）时，对应方的 `social_need *= (1 - needs_social_decay_after_chat)`，
默认衰减 55%——保留余量让连续社交不会瞬间清零。

> 这是 LLM 主动选择 `socialize` / `have_meal` / `rest_at` 的**唯一驱动信号**，
> 没有它，prompt 里的"社交需求"永远停在默认 0.3，阈值 0.55 永远达不到。

### 10.2 引擎层"自主社交邂逅"扫描器（`_scan_social_encounters`）

LLM async 路径有先天滞后：rule_agent 已经为 NPC 设了 path，等 worker 反应过来时
`agent.path` 非空就被冷却跳过了。引擎层的扫描器作为"安全网"补上这块：

**触发条件（每 `social_encounter_scan_minutes` 仿真分钟扫描一次，默认 5）**：

1. 候选 A：`entity_type==human`、`state ∈ {IDLE, WAITING}`、`path` 为空、
   `busy_until` 已到期，**且 `social_need ≥ threshold`**。
2. 候选 B：同场景内、同样空闲、与 A 曼哈顿距离 ≤ `social_encounter_distance`（默认 3）。
3. A、B 任一在 `social_encounter_cooldown_minutes`（默认 20 仿真分钟）冷却内则跳过。

匹配成功 → `asyncio.create_task` 调用 `InteractionService.create_request(kind="chat")`，
走完整的评估 / 广播 / 派发流程；接受 → 触发 `npc_dialogue_loop`。

这保证了**即使没有 LLM**，闲下来的两个空闲 NPC 也会自然撞见聊天。

### 10.3 工具产出的记忆候选必须落库（`agent_decision._consume_memory_candidates`）

`request_interaction` / `socialize` / `interact` 等工具在 `ToolResult.memory_candidates`
里附了候选记忆，但旧版本 `decide_with_llm` 只消费 LLM 自己的 `plan.memory_writes`，
工具候选**完全没人写库**——这是"NPC 记忆里看不到对话"的核心原因之一。

修复后流程：

```
执行工具 → ToolResult{success, result, memory_candidates: [...]}
        ↓
decide_with_llm 把每个候选用 importance.compute_importance 重新打分后
                 调 MemoryService.write 写入 short_term，关键词原样保留。
```

### 10.4 socialize 工具默认放宽

| 场景 | 旧行为 | 新行为 |
|------|--------|--------|
| 附近有熟人 | 选熟人 | 选熟人 |
| 只有陌生人 | `TARGET_NOT_FOUND` 直接失败 | 自动降级到陌生人池，按距离选最近的 |
| `only_friends=true` | 严格只熟人 | 严格只熟人（保留逃生通道） |

避免新场景或 seed 关系不全时 NPC 永远孤独。

### 10.5 配置项一览

| 变量 | 默认 | 说明 |
|------|-----:|------|
| `SOCIAL_NEED_TRIGGER_THRESHOLD` | 55 | LLM prompt 中"高于阈值，可考虑发起社交"的阈值（×100 标度） |
| `NEEDS_SOCIAL_GROWTH_PER_MINUTE` | 0.0010 | social_need 每仿真分钟增量 |
| `NEEDS_HUNGER_GROWTH_PER_MINUTE` | 0.0008 | hunger 每仿真分钟增量 |
| `NEEDS_ENERGY_DECAY_PER_MINUTE` | 0.0005 | energy 每仿真分钟衰减 |
| `NEEDS_SOCIAL_DECAY_AFTER_CHAT` | 0.55 | 单次社交完成后 social_need 衰减比例 |
| `SOCIAL_ENCOUNTER_SCAN_MINUTES` | 5 | 引擎邂逅扫描间隔（仿真分钟） |
| `SOCIAL_ENCOUNTER_DISTANCE` | 3 | 邂逅最大曼哈顿距离 |
| `SOCIAL_ENCOUNTER_COOLDOWN_MINUTES` | 20 | 同一 NPC 邂逅触发后冷却（仿真分钟） |
| `NPC_DIALOG_MAX_TURNS` | 10 | NPC-NPC 对话最大轮次 |
| `INTERACTION_HIGH_PRIORITY_THRESHOLD` | 8 | 目标当前任务紧迫度 ≥ 此值 → 硬拒 |

### 10.6 验收（手动观察 / 日志）

启动一段时间后应能在前端 / 数据库观察到：

1. NPC 头顶气泡偶发闪现 ❓（AWAITING_RESPONSE）→ 💬（CHATTING）→ 自然结束。
2. `interaction_requests` 表出现 `status=accepted` 的行，且 `reason` 含"自然邂逅"或工具理由。
3. `dialogue_messages` 表出现 `meta.source=npc_dialogue_loop` 的对话行。
4. `memories` 表中 `memory_type=chat` 且 `keywords` 含 "socialize" / 对方 name 的记录持续增长。
5. WS 流中 `interaction.*` / `dialogue.npc_to_npc_message` / `dialogue.npc_to_npc_ended` 事件按节奏出现。
