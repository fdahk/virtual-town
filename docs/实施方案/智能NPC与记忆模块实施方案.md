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
    "name": "talk_to_entity",
    "description": "与人类、动物或玩家互动",
    "parameters": {
      "target_entity_id": "string",
      "topic": "string",
      "tone": "friendly | neutral | angry | shy"
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
