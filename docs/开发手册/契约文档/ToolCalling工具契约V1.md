# Tool Calling 工具契约 V1

> 目的：定义 LLM 可以调用的工具全集。所有智能行为必须通过这些工具进入系统，不能让 LLM 直接修改数据库或世界状态。
>
> 修订记录：
> - V1.0（2026-04-30）首版工具列表。
> - V1.1（2026-04-30 阶段二）落地审计：
>   - 12 个工具全部实现并按 `entity_type` 授权（详见 §1.1 实现矩阵）。
>   - 新增 `ToolSpec` 元数据 schema（注册时声明所属模块、授权身份、JSON Schema、是否触发重规划）。
>   - 新增 `ToolContext` 模型，明确执行时的上下文字段。
>   - 移除原 `enter_portal`：MVP 中由引擎在到达 `portal.from_tile` 时自动切换 scene，不需要 LLM 显式调用。
>   - 落地代码位置：`backend/app/llm/tools/`，注册器单例 `get_tool_registry()`。

---

## 1. 基本原则

1. LLM 只能选择工具，不能直接执行工具。
2. 工具执行前必须经过参数校验、权限校验和世界状态校验。
3. 工具执行结果必须返回结构化 `ToolResult`。
4. 工具执行成功后，必要时写入事件和记忆。
5. 工具失败不能导致仿真崩溃，必须返回可理解的失败原因。

### 1.1 实现与授权矩阵

`allowed_entity_types == None` 表示对所有身份开放；其余按列表显式授权。
ToolExecutor 在执行前会拒绝不在授权列表里的工具调用并返回 `PERMISSION_DENIED`。

| 工具 | 所属模块 | human | animal | player | 实现文件 |
|------|---------|:-----:|:------:|:------:|----------|
| `move_to_location`     | world     | ✅ | ✅ | ✅ | `world_tools.py` |
| `move_to_entity`       | world     | ✅ | ✅ | ✅ | `world_tools.py` |
| `interact_with_object` | world     | ✅ | ❌ | ✅ | `world_tools.py` |
| `avoid_danger`         | world     | ✅ | ✅ | ✅ | `world_tools.py` |
| `face_entity`          | dialogue  | ✅ | ✅ | ✅ | `dialogue_tools.py` |
| `write_memory`         | memory    | ✅ | ✅ | ✅ | `memory_tools.py` |
| `search_memory`        | memory    | ✅ | ✅ | ✅ | `memory_tools.py` |
| `react_to_pet`         | animal    | ❌ | ✅ | ❌ | `animal_tools.py` |
| `make_sound`           | animal    | ❌ | ✅ | ❌ | `animal_tools.py` |
| `update_emotion`       | agent     | ✅ | ✅ | ✅ | `state_tools.py` |
| `wait`                 | simulation| ✅ | ✅ | ✅ | `state_tools.py` |
| `request_interaction`  | dialogue  | ✅ | ❌ | ✅ | `social_tools.py`（阶段 19）|
| `socialize`            | dialogue  | ✅ | ❌ | ❌ | `social_tools.py`（阶段 19）|
| `end_chat`             | dialogue  | ✅ | ❌ | ✅ | `social_tools.py`（阶段 19）|
| `work_at_location`     | life      | ✅ | ❌ | ❌ | `life_tools.py`（阶段 19）|
| `have_meal`            | life      | ✅ | ❌ | ❌ | `life_tools.py`（阶段 19）|
| `rest_at`              | life      | ✅ | ❌ | ❌ | `life_tools.py`（阶段 19）|
| `browse_shop`          | life      | ✅ | ❌ | ❌ | `life_tools.py`（阶段 19）|
| `observe_environment`  | life      | ✅ | ✅ | ❌ | `life_tools.py`（阶段 19）|

> **阶段 19 变更**：`talk_to_entity` 已被 `request_interaction` 替代——后者走完整
> 请求-评估-广播-自动派发流程，`talk_to_entity` 仅写意图记忆，功能重复。
> 老的 `talk_to_entity` 现已从 `ToolRegistry` 中移除。

---

## 2. 通用模型

### 2.1 ToolCall

```json
{
  "tool": "move_to_location",
  "arguments": {
    "location_id": "loc_hobbs_cafe",
    "reason": "准备上班"
  },
  "confidence": 0.82,
  "thought": "小芳快到上班时间了，应该去咖啡店。"
}
```

### 2.2 ToolResult

```json
{
  "tool": "move_to_location",
  "success": true,
  "result": {
    "action_id": "action_001"
  },
  "error": null,
  "events": ["evt_001"],
  "memory_candidates": []
}
```

### 2.3 ToolError

```json
{
  "code": "TARGET_NOT_REACHABLE",
  "message": "目标地点当前不可达。",
  "retryable": true
}
```

错误码：

| code | 说明 |
|------|------|
| `INVALID_ARGUMENTS` | 参数不合法 |
| `PERMISSION_DENIED` | 无权限 |
| `TARGET_NOT_FOUND` | 目标不存在 |
| `TARGET_NOT_REACHABLE` | 目标不可达 |
| `OUT_OF_RANGE` | 超出交互距离 |
| `STATE_CONFLICT` | 当前状态不允许执行 |
| `TOOL_TIMEOUT` | 工具执行超时 |
| `INTERNAL_ERROR` | 内部错误 |

### 2.4 ToolSpec

工具注册元数据，由每个 Tool 子类的 `spec()` 方法返回。
ToolRegistry 据此生成 OpenAI 兼容的 `functions` JSON 与 prompt 内的工具目录文本。

```ts
interface ToolSpec {
  name: string;
  description: string;
  owner_module: "world" | "dialogue" | "memory" | "animal" | "agent" | "simulation";
  allowed_entity_types?: Array<"human" | "animal" | "player"> | null;
  parameters_schema: object;          // JSON Schema，注入 prompt 与 OpenAPI
  rerun_on_failure?: boolean;         // 失败时是否触发重规划，默认 false
}
```

### 2.5 ToolContext

ToolExecutor 在调用每个 Tool 前构造，对 Tool 子类只读。
当工具需要与引擎内存态交互（修改 `EngineAgent.path`、`emotion` 等），
通过 `app.services.simulation_runtime.get_simulation_runtime().engine.get_agent(ctx.agent_id)` 拿到。

```ts
interface ToolContext {
  session: AsyncSession;              // 当前 DB 会话（外层负责提交 / 回滚）
  agent_id: string;
  entity_type: "human" | "animal" | "player";
  scene_id: string;
  position: [number, number];
  simulation_id: string;
  world_time: string;                 // ISO 8601
  source: "llm" | "rule" | "system";  // 影响事件 source 字段
  extra: Record<string, unknown>;
}
```

---

## 3. 世界移动工具

### 3.1 move_to_location

用途：让 Agent 移动到某个地点。

参数：

```json
{
  "location_id": "loc_hobbs_cafe",
  "reason": "去咖啡店上班"
}
```

校验：

1. location 存在。
2. Agent 当前不是 `DROWNING`、`SLEEPING`、`CHATTING` 等不可移动状态。
3. 有可达路径。

成功结果：

```json
{
  "action_id": "action_001",
  "path": [{ "x": 10, "y": 12 }, { "x": 11, "y": 12 }],
  "estimated_arrival_time": "2026-04-30T08:05:00+08:00"
}
```

---

### 3.2 move_to_entity

用途：移动到某个实体附近，例如走向玩家、NPC、动物。

参数：

```json
{
  "target_entity_id": "player_001",
  "distance": 1,
  "reason": "想和玩家打招呼"
}
```

校验：

1. 目标实体存在。
2. 目标实体在同一 scene，或存在可用 portal 路径。
3. 距离参数在 1-5 之间。

---

### 3.3 avoid_danger

用途：让 Agent 躲避危险区域。

参数：

```json
{
  "hazard_type": "deep_water",
  "reason": "离河水太近"
}
```

结果：

```json
{
  "safe_tile": { "x": 14, "y": 18 },
  "action_id": "action_avoid_001"
}
```

---

## 4. 交互工具

### 4.1 interact_with_object

用途：与世界物品交互，例如使用咖啡机、打开门、坐椅子。

参数：

```json
{
  "object_id": "obj_cafe_coffee_machine",
  "interaction_type": "make_coffee",
  "reason": "准备咖啡"
}
```

校验：

1. object 存在。
2. interaction_type 在 object.available_interactions 中。
3. Agent 在交互半径内。

---

### 4.2 enter_portal（MVP 不实现）

> **状态**：MVP 移除。
> **替代方案**：Portal 切换由引擎自动完成 —— `SimulationEngine._on_agent_arrived`
> 在 Agent 到达 `portal.from_tile` 且 `interaction_type == "auto_enter"` 时触发
> `_teleport(to_scene_id, to_tile)`。LLM 只需 `move_to_location` 把目标地点放在
> portal 的入口 tile 上即可，不需要单独工具。
> 后续如要支持 `click_enter`（玩家手动确认）类型 portal，再恢复此工具。

---

## 5. 对话工具

### 5.1 ~~talk_to_entity~~ ▸ request_interaction（阶段 19 替代）

> **变更**：`talk_to_entity` 已废弃移除；社交统一走 `request_interaction`，
> 走完整的请求-评估-接受/拒绝-自动派发对话流程，详见 §5.3。

---

### 5.2 face_entity

用途：让 Agent 面向某个实体。

参数：

```json
{
  "target_entity_id": "player_001"
}
```

---

### 5.3 request_interaction（阶段 19）

用途：发起一次交互请求（chat / help / trade）。对方根据当前状态、关系、紧迫度
自动决定接受 / 软拒 / 硬拒。

参数：

```json
{
  "target_entity_id": "npc_xiaofang",
  "kind": "chat",
  "reason": "想聊一下昨天的事",
  "priority": 3
}
```

校验：

1. 目标实体存在；2. 同场景且距离 ≤ 4 格；3. 不能向自己发起；
4. 阶段 19 冷却：连续 2 次硬拒后 30 仿真分钟禁止再次向同对方发起。

结果：

```json
{
  "request_id": "uuid",
  "status": "accepted",
  "decision": "accept",
  "decline_kind": null,
  "npc_line": null,
  "reason": "ok"
}
```

接受 + 双方都是 NPC 时，引擎会自动派发 `npc_dialogue_loop`（§NPC-NPC 自主对话）。

---

### 5.4 socialize（阶段 19）

用途：基于附近熟人的好感度自动选择对象发起 chat 请求；NPC 触发主动社交时使用，
不需要手动指定 target_entity_id。

参数：

```json
{
  "topic": "想聊聊天气",
  "only_friends": true,
  "max_distance": 8
}
```

返回结构与 `request_interaction` 类似，附带自动选中的 `target_entity_id`。

---

### 5.5 end_chat（阶段 19）

用途：主动结束当前对话，释放双方 CHATTING 状态。NPC-NPC 对话循环里任一方调
此工具会立即停止后续轮次。

参数：

```json
{
  "reason": "我得去咖啡店开门了",
  "target_entity_id": "npc_xiaowang"
}
```

---

## 6. 记忆工具

### 6.1 write_memory

用途：写入记忆候选。

参数：

```json
{
  "memory_type": "event",
  "scope": "long_term",
  "description": "小王在咖啡店点了一杯美式咖啡。",
  "importance": 5,
  "subject": "小王",
  "predicate": "ordered",
  "object": "美式咖啡"
}
```

注意：

LLM 可以建议写入记忆，但 MemoryService 必须二次判断 importance、scope 和 ttl。

---

### 6.2 search_memory

用途：检索 Agent 记忆。

参数：

```json
{
  "query": "小王喜欢喝什么咖啡",
  "limit": 8,
  "include_short_term": true,
  "include_long_term": true
}
```

---

## 7. 动物工具

### 7.1 react_to_pet

用途：动物被玩家抚摸后的反应。

参数：

```json
{
  "actor_entity_id": "player_001",
  "reaction": "enjoy",
  "emotion": "happy"
}
```

可选 reaction：

```ts
type PetReaction = "enjoy" | "tolerate" | "escape" | "threaten";
```

---

### 7.2 make_sound

用途：动物发出声音。

参数：

```json
{
  "sound": "bark",
  "reason": "看到陌生人靠近"
}
```

可选 sound：

```ts
type AnimalSound = "bark" | "meow" | "purr" | "whine" | "growl";
```

---

## 8. 状态工具

### 8.1 update_emotion

用途：更新 Agent 情绪。

参数：

```json
{
  "emotion": "happy",
  "reason": "玩家友好地打招呼",
  "duration_minutes": 30
}
```

---

### 8.2 wait

用途：等待一段时间。

参数：

```json
{
  "duration_minutes": 10,
  "reason": "等待咖啡做好"
}
```

---

## 9. LLM 决策输出 Schema

所有 Agent 决策必须返回如下 JSON。`AgentDecisionOutput` 的 TS 等价类型详见
`数据模型与接口契约V1.md` §7.4。

```json
{
  "thought": "小芳觉得现在应该去咖啡店准备营业。",
  "emotion": "focused",
  "tool_calls": [
    {
      "tool": "move_to_location",
      "arguments": {
        "location_id": "loc_hobbs_cafe",
        "reason": "准备上班"
      },
      "confidence": 0.85,
      "thought": "现在 7:55，再不出门就迟到了。"
    }
  ],
  "memory_writes": [
    {
      "memory_type": "thought",
      "description": "小芳计划开始今天的咖啡店工作。",
      "importance": 3
    }
  ]
}
```

实施约束（`app/llm/agent_decision.py` 强制）：

- `tool_calls` ≥ 1 且实际执行只取前 **3** 个，超出截断。
- `memory_writes` 实际只写前 **4** 条，超出截断。
- 单条 `description` 截断到 400 字。
- `importance` 强制夹紧到 [1, 10]。
- LLM 输出无法通过 Pydantic 校验时，本轮决策放弃，引擎回退到规则版 `decide_human_action` /
  `decide_animal_action`，**不抛异常、不阻塞 tick**。

---

## 10. 编码落地要求

1. 每个工具必须有一个 Pydantic 参数模型（实际：每个 Tool 子类内部定义）。
2. 每个工具必须有单元测试覆盖成功、参数错误、权限错误、状态冲突
   （MVP 已覆盖：tool registry 注册 / 授权矩阵 / OpenAI functions 形态 / prompt catalog；
   单工具的 happy-path 测试由 E2E 间接覆盖，后续可补到 `backend/tests/test_tools_*.py`）。
3. ToolRegistry 必须集中注册工具（实际：`get_tool_registry()` 单例懒加载所有 `build_tools()`）。
4. LLM 返回 tool_calls 后，不能直接执行，必须走 ToolExecutor（实际：
   `app/llm/agent_decision.py` 中由 `decide_with_llm` 统一构造 `ToolContext` 并调用
   `executor.execute_batch`）。
5. ToolResult 必须写入事件系统，方便前端展示和回放（实际：引擎在 LLM 决策完成后写入
   `llm.task_finished` 事件，包含 results 摘要；工具自身产生的 `WorldEvent` 通过
   `_pending_events` 在下个 tick 广播）。
