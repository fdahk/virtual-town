# Tool Calling 工具契约 V1

> 目的：定义 LLM 可以调用的工具全集。所有智能行为必须通过这些工具进入系统，不能让 LLM 直接修改数据库或世界状态。

---

## 1. 基本原则

1. LLM 只能选择工具，不能直接执行工具。
2. 工具执行前必须经过参数校验、权限校验和世界状态校验。
3. 工具执行结果必须返回结构化 `ToolResult`。
4. 工具执行成功后，必要时写入事件和记忆。
5. 工具失败不能导致仿真崩溃，必须返回可理解的失败原因。

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

### 4.2 enter_portal

用途：通过 Portal 进入建筑或离开建筑。

参数：

```json
{
  "portal_id": "portal_hobbs_cafe_door"
}
```

结果：

```json
{
  "from_scene_id": "scene_town_outdoor",
  "to_scene_id": "scene_hobbs_cafe_inside",
  "position": { "x": 5, "y": 9 }
}
```

---

## 5. 对话工具

### 5.1 talk_to_entity

用途：向某个实体说话或发起对话。

参数：

```json
{
  "target_entity_id": "npc_xiaofang",
  "topic": "询问今天咖啡店是否营业",
  "tone": "friendly",
  "message": "今天咖啡店几点开门？"
}
```

校验：

1. 目标实体存在。
2. 双方在交互半径内。
3. 目标实体当前不是不可对话状态。

结果：

```json
{
  "conversation_id": "conv_001",
  "message_id": "msg_001"
}
```

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

所有 Agent 决策必须返回：

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
      "confidence": 0.85
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

---

## 10. 编码落地要求

1. 每个工具必须有一个 Pydantic 参数模型。
2. 每个工具必须有单元测试覆盖成功、参数错误、权限错误、状态冲突。
3. ToolRegistry 必须集中注册工具。
4. LLM 返回 tool_calls 后，不能直接执行，必须走 ToolExecutor。
5. ToolResult 必须写入事件系统，方便前端展示和回放。
