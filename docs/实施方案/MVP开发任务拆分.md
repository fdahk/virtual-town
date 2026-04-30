# MVP 开发任务拆分

> 目的：将架构方案拆解为可以直接创建 issue / sprint 的开发任务。

---

## 1. MVP 目标

第一版 MVP 要跑通完整闭环：

```text
玩家进入小镇
  ↓
室外/室内地图切换
  ↓
6 个人类 NPC + 2 狗 + 2 猫运行
  ↓
NPC 按计划移动和行动
  ↓
玩家与 NPC/动物互动
  ↓
文本处理、记忆检索、LLM 回复
  ↓
事件和记忆写入
  ↓
前端实时展示状态变化
```

---

## 2. Sprint 0：项目骨架与工程化

### 后端任务

1. 初始化 `backend` FastAPI 项目。
2. 配置 SQLAlchemy、Alembic、PostgreSQL、pgvector。
3. 配置 Redis。
4. 实现 `.env.example` 和配置读取。
5. 编写 Docker Compose。
6. 编写 `scripts/dev.sh`、`scripts/seed.sh`、`scripts/reset_db.sh`。

### 前端任务

1. 初始化 `frontend` React + Vite + TypeScript。
2. 配置 ESLint、Prettier。
3. 配置 API 客户端。
4. 配置 Zustand。
5. 建立基础页面布局。

### 验收

```bash
./scripts/dev.sh
```

能启动前端、后端、PostgreSQL、Redis，并打开：

- 前端：`http://localhost:5173`
- 后端 OpenAPI：`http://localhost:8000/docs`

---

## 3. Sprint 1：数据库与种子数据

### 任务

1. 实现 `agents`、`agent_states`、`relationships` 表。
2. 实现 `map_scenes`、`map_tiles`、`locations`、`portals`、`world_objects` 表。
3. 实现 `memories`、`world_events`、`agent_actions` 表。
4. 创建 Alembic migration。
5. 编写种子数据：
   - 室外地图。
   - 咖啡店、学校、杂货店 3 个室内地图。
   - 6 个人类 NPC。
   - 2 只狗、2 只猫。
   - 默认玩家角色。
   - 初始关系。

### 验收

1. 空库可迁移到最新 schema。
2. 种子脚本可重复执行。
3. 通过 API 能读取 Agent、Scene、Location。

---

## 4. Sprint 2：前端地图渲染与资源管线

### 任务

1. 接入 Phaser 3。
2. 实现 `PhaserGame.tsx`。
3. 加载室外 Tiled 地图。
4. 加载至少 3 个室内地图。
5. 实现 scene 切换。
6. 实现 NPC/动物/玩家 sprite 渲染。
7. 实现点击实体事件。
8. 实现调试层：碰撞层、危险区、portal、对象层。

### 验收

1. 页面能显示小镇地图。
2. 玩家能进入咖啡店内部。
3. 点击 NPC/动物能在 React 面板显示基本信息。

---

## 5. Sprint 3：世界服务与移动碰撞

### 任务

1. 实现 WorldService。
2. 实现 A* pathfinding。
3. 实现碰撞检测。
4. 实现 portal 切换。
5. 实现 hazard 检测，例如河流落水。
6. 实现物品交互基础接口。

### 验收

1. 玩家不能穿墙或穿家具。
2. 玩家进入 portal 后切换 scene。
3. 玩家进入无护栏河流触发 `DROWNING`。
4. 玩家被护栏阻挡时收到 collision event。

---

## 6. Sprint 4：仿真循环与 WebSocket

### 任务

1. 实现 SimulationEngine。
2. 实现 world tick。
3. 实现 Agent 状态机。
4. 实现 WebSocket connection manager。
5. 实现 `simulation.delta` 推送。
6. 实现 start/pause/resume/step/speed API。

### 验收

1. 前端能实时收到 entity_updates。
2. NPC/动物能按后端状态移动。
3. 暂停后 world tick 停止。
4. 单步执行只推进一个 tick。

---

## 7. Sprint 5：规则版 Agent 行为

### 任务

1. 实现固定日程读取。
2. 实现当前行动选择。
3. 实现 move_to_location 行动。
4. 实现 wait/interact 行动。
5. 实现动物基础行为：wander、approach、avoid、sleep。

### 验收

1. 小芳早上去咖啡店。
2. 小明去学校。
3. 小王去咖啡店。
4. 狗和猫能自主巡游或休息。

---

## 8. Sprint 6：LLM 与 Tool Calling

### 任务

1. 实现 LLMService。
2. 接入通义千问 chat 和 embedding。
3. 实现 ToolRegistry。
4. 实现 ToolExecutor。
5. 实现 `move_to_location`、`talk_to_entity`、`write_memory`、`react_to_pet` 等基础工具。
6. 实现 JSON Schema / Pydantic 校验。
7. 实现 fallback。

### 验收

1. LLM 决策能返回合法 tool_calls。
2. 非法 tool_call 不会执行。
3. LLM 超时后 Agent 进入规则兜底行为。

---

## 9. Sprint 7：记忆系统

### 任务

1. 实现 MemoryService。
2. 实现 Redis 短期记忆。
3. 实现 PostgreSQL 长期记忆。
4. 实现 pgvector 语义召回。
5. 实现重要度评分。
6. 实现每日总结。

### 验收

1. NPC 看见玩家进入咖啡店后写入短期记忆。
2. 对话后双方写入 chat memory。
3. 查询“小王喜欢什么”能召回相关记忆。
4. 一天结束生成 summary。

---

## 10. Sprint 8：玩家交互与用户文本处理

### 任务

1. 实现玩家创建角色。
2. 实现玩家移动 API。
3. 实现玩家点击 NPC 打开对话。
4. 实现玩家点击动物触发互动。
5. 实现 query 改写。
6. 实现意图识别。
7. 实现上下文召回。
8. 实现结构化回复。

### 验收

1. 玩家能问小芳“小王喜欢喝什么？”。
2. 小芳能根据记忆回答。
3. 玩家抚摸狗，狗能根据关系和性格反应。
4. 对话和互动写入记忆。

---

## 11. Sprint 9：演示闭环与质量保障

### 任务

1. 打磨 UI 面板。
2. 增加事件日志。
3. 增加记忆查看器。
4. 增加关系查看器。
5. 增加 Playwright 演示测试。
6. 优化性能。
7. 编写 README。

### 验收

演示脚本完整跑通：

1. 玩家创建角色。
2. 进入小镇。
3. 进入咖啡店。
4. 与小芳对话。
5. 小芳根据记忆回答。
6. 抚摸狗。
7. 狗做出反应。
8. 玩家走入河流触发危险。
9. 事件和记忆面板显示全过程。

---

## 12. 第一周建议落地范围

第一周不要碰复杂 LLM，先把底座建好：

1. 工程骨架。
2. 数据库迁移。
3. 种子数据。
4. Phaser 地图加载。
5. 玩家/NPC sprite 渲染。
6. 基础移动和 WebSocket delta。

这样后续所有智能模块都有可运行载体。
