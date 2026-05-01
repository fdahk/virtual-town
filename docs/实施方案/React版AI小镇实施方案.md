# React 版 AI 小镇实施方案

---

## 0. 架构师审查修订说明

本文件只作为整体架构总说明。正式开发时，各核心模块以独立实施方案为准：

- `实施方案目录与模块边界.md`
- `世界与地图模块实施方案.md`
- `仿真循环设计模块实施方案.md`
- `智能NPC与记忆模块实施方案.md`
- `玩家与交互模块实施方案.md`
- `用户文本处理模块实施方案.md`
- `LLM与ToolCalling模块实施方案.md`
- `数据库与存储模块实施方案.md`
- `前端渲染与资源管线模块实施方案.md`
- `视觉资源与美术资产方案.md`
- `事件系统与任务调度模块实施方案.md`
- `工程化与接口规范实施方案.md`
- `测试验收与质量保障方案.md`
- `数据模型与接口契约V1.md`
- `ToolCalling工具契约V1.md`
- `MVP开发任务拆分.md`
- `数据流与前端状态管理方案.md`
- `安全权限与内容安全方案.md`
- `可观测性与错误处理方案.md`
- `Prompt工程与模型评测方案.md`
- `部署发布与运行维护方案.md`
- `实施方案完整性审查与补漏清单.md`

本次修订后的关键架构决策：

1. 地图方案不是因为原版使用 Phaser 就沿用，而是对 React DOM、Canvas、PixiJS、Phaser、Godot Web 做过评估后，选择 **React + Phaser 3 + Tiled**。Phaser 负责渲染，后端 World Service 负责权威世界逻辑。
2. 建筑内部地图是 MVP 必做能力，整体世界模型参考《星露谷物语》的室外大地图 + 室内空间 + Portal 切换。
3. 初始世界不只做 3 个 NPC，而是预置 **6 个人类 NPC + 2 只狗 + 2 只猫 + 1 个玩家角色**。狗、猫也属于 Agent。
4. 玩家模块是核心模块，玩家角色会被 NPC 感知、记忆和反应，不只是摄像机视角。
5. LLM 输出必须通过 tool calling / JSON Schema 进入系统，不允许依赖自然语言字符串解析。
6. **文档与仓库同步**：凡涉及「前端/后端目录树、REST 路径前缀、数据库表清单、WS 信封」的段落，以当前 Git 源码及 `数据模型与接口契约V1.md` 为准并保持随版本更新；本章保留产品级架构叙述，不替代契约文档。

---

## 1. 项目目标

本项目要实现一个可在浏览器中运行的 AI 小镇。小镇中包含多个 NPC，每个 NPC 有人物属性、日程规划、记忆、位置、当前行为和社交能力。NPC 能根据自身设定、时间、周围环境和记忆自主决定当前做什么，例如起床、上学、工作、吃饭、购物、去咖啡店、与其他 NPC 聊天等。

最终希望达到的效果不是单纯的角色动画，而是一个有基本自主性的模拟系统：

- NPC 有固定人物档案和生活习惯。
- NPC 能生成每日计划，并按时间推进执行。
- NPC 能感知附近 NPC、建筑和事件。
- NPC 能基于记忆和当前环境做决策。
- NPC 能进入建筑、触发事件、与其他 NPC 对话。
- 前端以 Web 小镇形式实时展示角色移动、状态和对话。

---

## 2. 总体技术选型

### 推荐技术栈

| 层级 | 技术选择 | 作用 |
|------|----------|------|
| 前端框架 | React + TypeScript + Vite | 构建主 Web 应用 |
| 地图渲染 | Phaser 3 | 渲染瓦片地图、角色精灵、室内外场景、移动动画 |
| UI | React + CSS（内联/原生布局）；部分数据用 TanStack Query | MVP 控制台与面板不设 Ant Design / shadcn 依赖 |
| 前端状态 | Zustand + TanStack Query | Zustand：仿真增量、选中实体等运行时状态；TanStack Query：Agent/记忆等 REST 缓存 |
| 后端框架 | FastAPI | REST、仿真 WebSocket、观测 WebSocket、`lifespan` 拉起运行时 |
| 实时通信 | WebSocket | `/ws/simulations/{id}` 推送增量；`/ws/observability` 观测流（可与玩家路由隔离部署） |
| 数据库 | PostgreSQL | Agent、地图、仿真、记忆、任务与观测审计 |
| 向量记忆 | pgvector | 记忆嵌入与会话向量检索 |
| 缓存/队列 | Redis + RQ（独立 worker） | MVP 选型为 **RQ**（非 Celery）；API 进程负责入队，`tasks` 表为权威任务状态 |
| LLM 接入 | OpenAI 兼容接口封装 | 支持通义千问、DeepSeek、OpenAI 等模型 |
| 地图编辑 | Tiled Map Editor | 制作室外地图、建筑内部地图、对象层和碰撞层 |
| 部署 | Docker Compose | 本地和服务器一键启动 |

### 最终推荐架构

采用 **React + Phaser + FastAPI + PostgreSQL/pgvector + WebSocket**。

React 负责产品界面，Phaser 负责游戏画面，FastAPI 负责仿真服务和 AI 调度，PostgreSQL/pgvector 负责结构化数据和长期记忆，WebSocket 负责实时同步。

---

## 3. 为什么不用原版技术组合

原版项目的前端组合是 **Django + Phaser**。这套组合能运行，但更像研究原型，不适合作为转正项目的产品化实现。

### 原版技术组合的问题

| 原版设计 | 问题 | React 版改进 |
|----------|------|--------------|
| Django 模板渲染 HTML | 页面结构老旧，前后端耦合重 | React 单页应用，组件化开发 |
| 文件系统同步 `environment/{step}.json` / `movement/{step}.json` | 延迟高、难调试、并发能力弱 | WebSocket 实时推送 |
| JSON 文件存储仿真状态 | 难查询、难统计、难恢复 | PostgreSQL 结构化存储 |
| 记忆存 JSON + embeddings JSON | 检索不方便，性能差 | pgvector 向量检索 |
| 单进程串行跑所有 NPC | 多 NPC 时 LLM 调用很慢 | 任务队列 + 并发执行 |
| Prompt 输出强依赖格式 | 容易被不同模型输出格式破坏 | 统一 JSON Schema 输出 |
| 前端主要展示地图 | 缺少产品化交互面板 | React 提供丰富配置和调试界面 |

### 原版值得参考的地方

原版的技术栈可以替换，但它的 AI 设计非常值得保留：

- **认知管线**：感知 → 检索 → 规划 → 反思 → 执行。
- **记忆流**：将事件、想法、对话统一成记忆节点。
- **检索评分**：时近性 + 重要度 + 相关性。
- **层次化规划**：日计划 → 小时计划 → 任务分解 → 具体行为。
- **空间记忆**：NPC 不是全知全能，只知道自己见过或设定中知道的地点。
- **事件三元组**：用 subject / predicate / object 表达世界事件。

因此本方案不是照搬原版代码，而是 **保留原版 Agent 架构思想，重做 Web 产品架构**。

---

## 4. React 版整体架构

```text
┌──────────────────────────────────────────────┐
│                  Browser                      │
│                                              │
│  React App                                   │
│  ├── 路由：Town / CreatePlayer / Observability │
│  ├── TanStack Query + Zustand                 │
│  └── Phaser（TownScene）：Tilemap + 精灵        │
└─────────────────────┬────────────────────────┘
                      │ REST + WebSocket（仿真 + 观测分离）
┌─────────────────────▼────────────────────────┐
│                 FastAPI Backend               │
│                                              │
│  ├── SimulationRuntime：仿真时钟、tick、广播      │
│  ├── SimulationEngine：决策入队、持久化、事件广播  │
│  │   └── _enqueue_reflect_batch()：反思/日结异步入 low 队列，不阻塞 tick │
│  ├── Agent / World / Memory / Player Service：REST 门面 │
│  ├── Dialogue / Planning / Safety / Tasks：对话、层次规划、限流审计、RQ 异步任务 │
│  ├── Observer + EventRouter：可观测性与领域订阅   │
│  ├── LLM：`app/llm`（chat_json / embed[+缓存] / tools）│
│  │   ├── EmbeddingService：Redis 缓存 TTL 1h，减少重复 API 调用 │
│  │   └── agent_decision：retrieve ‖ plan_ctx（asyncio.gather）│
│  └── WebSocket：`gateway.py`（统一信封推送）       │
└──────────────┬────────────────────┬──────────┘
               │                    │
       PostgreSQL + pgvector        Redis（会话、队列、缓存）
       ├── 地图/Agent/仿真/记忆      ├── vt:high    — agent_decision（priority≤3）
       ├── tasks / agent_plans      ├── vt:default — dialogue/relationship（priority 4-6）
       ├── 观测与 tool/llm 审计      ├── vt:low     — embedding(p=9)、reflection(p=8）
       └── …                       ├── agent:{id}:unreachable TTL 300s（不可达黑名单）
                                   ├── embed:{hash} TTL 3600s（embedding 向量缓存）
                                   └── agent runtime / dialogue / scene entities
```

---

## 5. 前端架构设计

### 5.1 前端项目结构（与 `frontend/src` 一致）

```text
frontend/
├── package.json                     # scripts: dev/build；openapi-typescript → src/types/api.ts（可选）
├── public/assets/                   # manifest + tilesets + sprites + portraits（见资源方案）
├── src/
│   ├── main.tsx                     # React root + QueryClientProvider
│   ├── app/
│   │   └── App.tsx                  # path 分流：/observability ↔ 小镇 / CreatePlayer
│   ├── pages/
│   │   ├── TownPage.tsx             # Phaser + 控制台 + WS 挂载点
│   │   ├── CreatePlayerPage.tsx
│   │   └── observability/           # ObservabilityPage、TraceDetail、AgentRuntimeCard
│   ├── game/
│   │   ├── PhaserGame.tsx           # React 挂载 Phaser Game
│   │   ├── eventBus.ts              # React ⇄ TownScene 桥接
│   │   ├── scenes/
│   │   │   └── TownScene.ts         # 单场景：_tilemap/camera/agent sprites（尚无 Boot/UIScene/systems 分包）
│   │   └── assets/
│   │       ├── manifest.ts          # fetch manifest JSON
│   │       └── usePortrait.ts
│   ├── components/                # AgentPanel, AgentList, ChatPanel, EventLog 等
│   ├── stores/
│   │   └── worldStore.ts           # Zustand：仿真快照、选中实体等
│   ├── api/
│   │   ├── http.ts                 # fetch 封装
│   │   ├── index.ts                # api.* 各领域请求（world/agents/player/sim…）
│   │   └── websocket.ts           # ReliableSocket + simulationSocket / observabilitySocket
│   └── types/
│       ├── domain.ts              # REST/领域类型（或由 openapi 生成 api.ts）
│       └── observability.ts
```

未落地、仍属方案演进的占位页（可作后续 Sprint）：专用 `AgentConfigPage` / `MapEditorPage` / `ReplayPage`；路由器仅为 `App.tsx` 内 `pathname` 判断而非 `router.tsx`。

### 5.2 React 和 Phaser 如何配合

React 不直接渲染每一格地图和每个角色，因为 DOM 不适合大量地图瓦片和实时动画。推荐做法是：

- React 负责页面布局和业务 UI。
- Phaser 负责地图和角色动画。
- React 把一个 `div` 容器交给 Phaser 挂载 Canvas。
- WebSocket 收到后端状态后，更新 Zustand。
- Zustand 状态变化后，通过事件总线通知 Phaser 移动角色。
- Phaser 中点击 NPC 时，通过事件通知 React 打开 NPC 面板。

```text
WebSocket 收到 movement
        ↓
Zustand 更新 agent positions
        ↓
EventBus.emit("agent:move", payload)
        ↓
Phaser TownScene 播放移动动画
        ↓
用户点击 NPC
        ↓
EventBus.emit("agent:selected", id)
        ↓
React 打开 AgentPanel
```

### 5.3 前端核心页面

#### 小镇主页面 `TownPage`

包含：

- 中间：Phaser 地图 Canvas。
- 左侧：NPC 列表、在线状态、当前行为。
- 右侧：选中 NPC 的详细信息。
- 底部：事件日志、对话日志。
- 顶部：仿真时间、暂停/继续、速度控制。

#### NPC 信息面板

展示：

- 姓名、年龄、职业、性格、背景。
- 当前坐标、所在建筑、当前行为。
- 今日计划。
- 当前记忆摘要。
- 最近对话。
- 手动干预按钮：让 NPC 去某处、让 NPC 和某人说话、修改人物设定。

#### 仿真控制台

提供：

- 开始仿真。
- 暂停仿真。
- 单步执行。
- 调整速度。
- ~~重置小镇。~~ （若需请以种子/运维脚本重置 DB，参见 `README` / `scripts/reset_db.sh`）
- ~~保存快照 / 回放历史。~~ MVP 前端未单独的「快照/回放」页；运行时状态以 Postgres + WS 增量为准，历史可通过观测库或后续迭代补齐。

NPC 面板中的「手动指挥 NPC」等亦为渐进能力：当前优先保证创建玩家、仿真控制、对话与观测闭环。

---

## 6. 后端架构设计

### 6.1 后端目录结构（与 `backend/app` 一致）

```text
backend/
├── alembic/                    # 迁移（非 app/db/migrations）
├── app/
│   ├── main.py                 # FastAPI、CORS、trace、lifespan（Observer、RQ 入队侧、TTL、仿真运行时）
│   ├── api/
│   │   ├── routes_world.py / routes_agents.py / routes_player.py / routes_simulation.py
│   │   ├── routes_memory.py / routes_dialogue.py / routes_observability.py (+ health)
│   │   └── __init__.py         # api_router 聚合
│   ├── websocket/
│   │   ├── gateway.py          # /ws/simulations/{id}，ConnectionManager + 信封广播
│   │   └── observability.py    # 观测 WS
│   ├── domain/
│   │   ├── simulation/          # engine.py、rule_agent、schedule（认知主循环）
│   │   ├── world/               # grid、scene_cache（权威网格与缓存）
│   │   ├── memory/              # reflection 等
│   │   ├── planning/           # hierarchical、importance、relationship
│   │   ├── dialogue/           # conversation_store、query_rewrite、intent、reply
│   │   ├── tasks/               # queue、runner、handlers、worker、ttl_worker、registry
│   │   └── safety/               # content、rate_limit、tool_audit
│   ├── services/
│   │   ├── simulation_runtime.py / agent_service.py / world_service.py / memory_service.py
│   │   ├── player_service.py / event_router.py / observer.py
│   ├── llm/
│   │   ├── client.py、agent_decision.py、embedding.py、conversation.py
│   │   └── tools/               # executor、registry、world/memory/state/animal/dialogue ...
│   ├── prompts/                 # 每 prompt 一目录：`v1.jinja2` + metadata/examples
│   ├── db/models.py / session.py
│   ├── schemas/
│   └── core/                    # config、errors、logging、redis、trace、rate_limit_middleware …
├── tests/
└── pyproject.toml
```

认知管线**未**拆成独立 `domain/agent/perceive.py` 等模块；等价逻辑分布在 `SimulationEngine`、`app/llm/agent_decision.py`、`domain/memory/reflection.py`、`domain/planning` 等处。

### 6.2 后端核心组件

| 组件 | 职责 |
|------|------|
| `SimulationRuntime` | 对外 REST/WS 挂载的仿真生命周期（start/pause/step、广播） |
| `SimulationEngine` | tick：`decide`/规则、`persist`、事件、反思批次、delta 广播 |
| `AgentService` / `WorldService` / `MemoryService` | Agent 档案与运行态、世界查询、记忆 CRUD / 检索 |
| `PlayerService` | 玩家创建、`/players/me/move|interact|talk` |
| `app.llm` | OpenAI 兼容 `chat_json`、embeddings、结构化 tool 管线 |
| `TaskQueue` + RQ Worker | LLM 决策、embedding、日计划、对话、rewrite 等异步任务落地 |
| `ConnectionManager`（`websocket/gateway.py`） | 仿真 WS 信封推送 |
| `Observer` / `EventRouter` | LLM/Task/Trace 入库与观测 API |

---

## 7. Agent 认知架构

React 版继续参考原版的核心管线：

```text
每个仿真 tick：

1. perceive   感知
2. retrieve   检索记忆
3. plan       规划行为
4. reflect    反思总结
5. execute    执行动作
6. broadcast  推送给前端
```

### 7.1 感知 `perceive`

输入：

- 当前 NPC 坐标。
- 视野半径。
- 地图建筑/物品。
- 附近 NPC。
- 当前世界事件。

输出：

- 当前可见对象。
- 当前可见 NPC。
- 新事件列表。

改进点：

- 原版从 Maze tile 的 `events` 集合中读取事件。
- React 版后端直接从数据库和内存中的 WorldState 读取事件。
- 感知结果统一为结构化 JSON，方便调试和前端展示。

### 7.2 检索 `retrieve`

保留原版三因素评分：

```text
score =
  recency_weight    * recency_score +
  importance_weight * importance_score +
  relevance_weight  * relevance_score
```

推荐默认权重：

```text
recency_weight = 0.5
importance_weight = 3.0
relevance_weight = 2.0
```

改进点：

- 原版记忆存在 JSON 文件里，检索时在 Python 内存中计算。
- React 版用 PostgreSQL + pgvector 存储记忆向量。
- 相关性检索可由数据库完成，性能更好。

### 7.3 规划 `plan`

规划分为四层：

```text
人物设定 + 当前日期
        ↓
每日计划 daily_plan
        ↓
小时计划 hourly_schedule
        ↓
任务分解 task_decomposition
        ↓
当前行动 action
```

行动结构：

```json
{
  "agent_id": "isabella",
  "action_type": "go_to",
  "description": "Isabella is preparing coffee at Hobbs Cafe",
  "target_location_id": "hobbs_cafe_counter",
  "duration_minutes": 30,
  "emotion": "focused",
  "emoji": "☕",
  "start_time": "2026-04-30T08:00:00",
  "end_time": "2026-04-30T08:30:00"
}
```

改进点：

- 原版很多 prompt 要求返回自然语言，再用字符串切割解析。
- React 版必须要求模型返回严格 JSON。
- 后端使用 Pydantic 校验，如果失败就自动重试或走 fail-safe。

### 7.4 反思 `reflect`

触发条件：

- 重要事件累计分超过阈值。
- 一段关键对话结束。
- 一天结束。
- 用户手动触发。

输出：

- 新的 thought 记忆节点。
- 人物状态更新，例如 `currently` 字段变化。

示例：

```json
{
  "type": "thought",
  "description": "Isabella realizes that Maria is interested in helping with the Valentine's Day party.",
  "evidence_memory_ids": ["mem_1001", "mem_1002"],
  "importance": 7
}
```

---

## 8. 世界与地图设计

### 8.1 地图方案选择

推荐使用 **React + Phaser 3 + Tiled Map Editor**，详细评估见 `世界与地图模块实施方案.md`。

理由：

- React DOM 适合业务 UI，不适合大地图瓦片、摄像机、角色动画和大量碰撞反馈。
- PixiJS 渲染能力很强，但它更偏底层渲染器，场景、地图、动画和输入系统需要自行组织。
- Phaser 3 是完整 2D 游戏框架，对 Tiled、Tilemap、Sprite、Camera、Scene 支持成熟，适合星露谷式 2D 小镇。
- 本方案不把 Phaser 作为世界真相。Phaser 只负责渲染和输入，碰撞、危险、建筑切换、事件触发以后端 World Service 为准。
- 建筑内部地图是必做能力，通过 Portal 连接室外大地图和室内 scene。

美术资源策略（详见 `视觉资源与美术资产方案.md`）：

- **Kenney · Tiny Town（CC0）** 提供 tileset / 建筑 / 家具；
- **LPC Universal Spritesheet（CC-BY-SA 3.0 / GPL 3.0）** 提供 4 方向人物/动物 walk 动画；
- **AI 生成立绘（项目自有）** 提供 NPC 对话头像；
- MVP tile 尺寸统一为 32×32；
- 所有外部资源通过 `scripts/fetch_assets.sh` 下载、`CREDITS.md` 登记。

### 8.2 地图数据结构

地图由三类数据组成：

1. **视觉地图**：Tiled 导出的 `.tmj` / `.json`，给 Phaser 渲染。
2. **逻辑地图**：后端保存每个格子的可行走、建筑、物品、区域等信息。
3. **交互对象**：建筑、物品、事件触发点。

```json
{
  "tile": {
    "x": 12,
    "y": 8,
    "walkable": true,
    "world": "town",
    "sector": "commercial_street",
    "arena": "hobbs_cafe",
    "object": "coffee_machine"
  }
}
```

### 8.3 建筑和事件

建筑需要有可交互属性：

```json
{
  "id": "hobbs_cafe",
  "name": "Hobbs Cafe",
  "type": "cafe",
  "entry_tiles": [{ "x": 20, "y": 14 }],
  "interaction_tiles": [{ "x": 24, "y": 16 }],
  "available_actions": ["drink_coffee", "work", "chat", "host_party"],
  "open_hours": {
    "start": "08:00",
    "end": "20:00"
  }
}
```

---

## 9. 数据库设计

### 9.1 核心表（与 `backend/app/db/models.py` / Alembic 对齐）

以下为**逻辑分组**，列级定义以 **`数据模型与接口契约V1.md`** 与 OpenAPI 为准。

```text
# 地图与世界
map_scenes, map_tiles, locations, portals, world_objects

# Agent 与运行时
agents                    # 档案；日程规则在 schedule_template(JSONB)，无独立 agent_schedules 表
agent_states              # 场景、格子坐标、emotion、当前目标等运行时字段
relationships
agent_actions
agent_plans               # 层次规划缓存（与日计划等相关 prompt 对应）

# 记忆与对话
memories                  # memory_type / embedding / importance / importance_detail 等
dialogue_messages

# 仿真与事件
simulations
world_events

# 异步任务与可观测审计
tasks, task_status_log
observability_events
llm_calls
tool_calls
```

### 9.2 记忆表示示例

契约字段名为 `memory_type`（本节示例中与旧称 `type` 同义）。

```json
{
  "id": "mem_001",
  "agent_id": "agent_isabella",
  "memory_type": "event",
  "subject": "Isabella",
  "predicate": "talked_to",
  "object": "Maria",
  "description": "Isabella talked to Maria about the Valentine's Day party.",
  "importance": 6,
  "keywords": ["Isabella", "Maria", "party"],
  "created_at": "2026-04-30T10:00:00",
  "last_accessed_at": "2026-04-30T10:30:00"
}
```

---

## 10. API 与 WebSocket 设计

### 10.1 REST API

**完整路由与请求体以 `数据模型与接口契约V1.md` 与 `/docs` 为准。** 与早期草案差异要点：

| 前缀 / 示例路径 | 说明 |
|-----------------|------|
| `/api/world/scenes`、`/tiles`、`/locations`、`/portals`、`/objects`、`/pathfinding` | 地图与物件（非 `/api/world/map` 单路由） |
| `/api/agents`、`/api/agents/{id}/state|profile|relationships|memories|memory/search`、`/reflect`、`/decide` | Agent 门面 |
| `/api/players`、`/api/players/me`、`…/move|interact|talk` | 玩家 |
| `/api/simulations/current`、`…/start|pause|resume|step|speed`、`…/events` | 仿真（路径带 `simulations` 复数） |
| `/api/dialogue/rewrite-query`、`retrieve-context` | 调试链路子集 |
| `/api/observability/*`、`/api/health/*` | 观测与健康检查 |

本项目 **不提供** MVP 草案中的「`POST /api/agents` 任意创建」「`POST /api/simulations/reset`」等未被契约采纳的别名；重置世界走运维脚本。

### 10.2 WebSocket 消息

仿真连接（玩家小镇页）：

```text
ws://localhost:8000/ws/simulations/{simulation_id}
```

服务端与客户端一律使用 **统一信封**：

```json
{
  "version": "1.0",
  "type": "simulation.delta",
  "payload": { "step": 42, "world_time": "...", "entity_updates": [], "events": [] },
  "sent_at": "2026-04-30T08:30:00Z"
}
```

此外还有 `dialogue.message_created`、`world.scene_changed`、`world.hazard_triggered` 等（见契约 §9）。

---

## 11. LLM 调用设计

### 11.1 模型接入策略

后端通过 **`app/llm/client.py`** 的 OpenAI 兼容客户端提供 `chat`、`chat_json`、重试与超时；`embeddings` 在 **`app/llm/embedding.py`**。**无需**单独的 `LLMService` 文件命名；调用方注入 `LLMClient` / `embed_text` / `embed_batch` 即可。

等价能力示意：

```python
async def chat_json(prompt: str, schema: type[BaseModel]) -> BaseModel:
    ...

async def embed(text: str) -> list[float]:
    ...
```

支持配置：

```env
LLM_PROVIDER=dashscope
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_API_KEY=...
LLM_CHAT_MODEL=qwen-plus
LLM_REASONING_MODEL=qwen-max
LLM_EMBEDDING_MODEL=text-embedding-v2
```

### 11.2 Prompt 输出必须 JSON 化

原版项目的最大工程问题之一是依赖自然语言格式解析。例如任务分解要求模型输出：

```text
1) Isabella is brushing her teeth. (duration in minutes: 5, minutes left: 55)
```

不同模型很容易输出：

```text
1) Isabella brushes her teeth (5 min)
✅ Total: 12 subtasks ...
```

这会导致解析器崩溃。因此 React 版后端必须要求模型返回 JSON：

```json
{
  "subtasks": [
    {
      "description": "Isabella brushes her teeth",
      "duration_minutes": 5
    }
  ]
}
```

并用 Pydantic 做校验：

```python
class Subtask(BaseModel):
    description: str
    duration_minutes: int

class TaskDecomposition(BaseModel):
    subtasks: list[Subtask]
```

### 11.3 失败兜底策略

LLM 输出失败时不能让仿真崩溃。每个核心能力都需要 fail-safe：

| 能力 | 失败兜底 |
|------|----------|
| 起床时间 | 使用人物 lifestyle 或默认 7:00 |
| 每日计划 | 使用规则生成基础日程 |
| 任务分解 | 将大任务平均切成 5/10/30 分钟 |
| 地点选择 | 选择当前行动类型对应的默认建筑 |
| 对话生成 | 返回一句简短问候 |
| 反思生成 | 跳过本次反思 |
| Embedding 失败 | 暂存记忆，稍后重试 |

---

## 12. 仿真循环设计

### 12.1 时间步进

推荐每个 tick 对应游戏内 1-5 分钟，而不是原版的 10 秒。

理由：

- 原版每 10 秒一步，更适合细动画，但 LLM 成本太高。
- Web 转正项目更关注行为逻辑和可展示效果。
- 1-5 分钟一步能减少 LLM 调用次数。

推荐配置：

```text
默认 tick = 5 分钟游戏时间
前端动画时长 = 0.5-2 秒真实时间
```

### 12.2 循环流程

```text
while simulation.running:
    current_time += tick_minutes

    1. 找出需要决策的 NPC
    2. 并发执行 NPC 认知管线
    3. 更新 NPC 行动和位置
    4. 写入世界事件
    5. 写入记忆
    6. 推送 WebSocket
    7. 等待下一 tick
```

### 12.3 并发策略

推荐将 NPC 分为两类：

- **轻量 tick**：NPC 仍在执行已有行动，只更新位置，不调用 LLM。
- **重决策 tick**：当前行动结束，需要调用 LLM 决定下一步。

这样可显著降低成本。

```text
如果 agent.current_action 未结束：
    只移动/更新进度
否则：
    perceive → retrieve → plan → execute
```

---

## 13. 路径规划设计

路径规划可以从简单到复杂逐步实现。

### MVP 阶段

使用 A* 算法在二维网格上寻路：

- `walkable = true` 的格子可以走。
- 建筑入口作为目标点。
- NPC 每 tick 沿路径前进若干格。

### 后续优化

- 避让其他 NPC。
- 对不同地形设置不同移动成本。
- 支持建筑内部地图。
- 支持人群拥堵和排队。

---

## 13.5 响应延迟优化（2026-05-01 落地）

基于链路追踪数据（trace_c99a781e6279，总耗时 ~182s）识别出三类主要瓶颈，已完成以下优化：

### 不可达目标熔断

| 位置 | 改动 |
|------|------|
| `app/llm/tools/world_tools.py` | `TARGET_NOT_REACHABLE` 时调用 `_mark_unreachable()` 写入 Redis 黑名单 |
| `app/llm/agent_decision.py` | `_perceive()` 读取黑名单，过滤感知地点和附近实体列表 |
| `app/core/redis_client.py` | 新增 `key_agent_unreachable()` key 构造器（TTL 300s） |

效果：断开"工具失败→再次入队→LLM 再次选同一目标→再次失败"循环，节省约 75s/trace。

### 决策链路并行化

`decide_with_llm()` 中的记忆检索（embed + pgvector）与规划上下文查询（planning）
由串行改为 `asyncio.gather()` 并发，节省约 6s/决策周期。

### Embedding 向量缓存

`EmbeddingService.embed()` 新增 Redis 缓存层（key: `embed:{sha256[:32]}`，TTL 1h），
相同文本直接命中缓存，减少重复 API 调用。

### 任务队列优先级分离

| 任务 | 队列 | 优先级 |
|------|------|--------|
| `agent_decision` | `vt:high` | 5 |
| `write_memory_embedding` | `vt:low` | 9 |
| `daily_reflection` | `vt:low` | 8 |

`write_memory_embedding` 降为 low 队列，消除 embedding 积压对决策任务的影响（trace 中 ~71s 积压）。

### 反思任务异步化

`_maybe_reflect_batch()`（内联 LLM 调用，偶发阻塞 tick 5-10s）替换为
`_enqueue_reflect_batch()`，投递 `daily_reflection` 任务到 low 队列，
world tick 不再等待反思 LLM 结果。

---

## 14. 与原版架构详细对比

| 维度 | Stanford 原版 | React 版方案 | 改进价值 |
|------|---------------|--------------|----------|
| 前端框架 | Django Template + Phaser | React + Phaser | 组件化、可维护、适合 Web 产品 |
| 通信方式 | 文件系统同步 | WebSocket + REST | 实时性更好，状态更清晰 |
| 后端框架 | Python 脚本 + Django 桥接 | FastAPI 服务化 | API 清晰，支持异步 |
| 存储 | JSON 文件 | PostgreSQL | 可查询、可恢复、可统计 |
| 向量检索 | embeddings JSON | pgvector | 更适合长期扩展 |
| LLM 输出 | 自然语言 + 字符串解析 | JSON Schema + Pydantic | 更稳定，适配不同模型 |
| NPC 决策 | 串行执行 | 可并发执行 | 多 NPC 更快 |
| 地图 | Tiled + CSV | Tiled + 后端逻辑地图 | 保留成熟地图方案 |
| 回放 | 读 movement JSON | 数据库事件流回放 | 更容易做历史查看 |
| 调试 | 终端 print | Web 调试面板 | 更适合展示和答辩 |

---

## 15. MVP 功能范围

为了转正任务可控，建议 MVP 不要一开始做 25 个以上 NPC，也不要完全复刻原版。但 MVP 必须覆盖完整闭环：真实世界地图、建筑内部空间、玩家交互、NPC/动物 Agent、记忆和对话。

### MVP 必做

1. React + Phaser 小镇地图展示。
2. 室外大地图 + 至少 3 个建筑内部地图。
3. 6 个人类 NPC、2 只狗、2 只猫在地图上运行。
4. 玩家可以创建角色、移动、进入建筑、点击 NPC/动物/物品。
5. NPC 和动物都有初始模型、关系、状态和记忆。
6. NPC 能生成每日计划，并按计划选择地点和行动。
7. NPC、动物、玩家之间能在交互半径内触发对话或反应。
8. 对话、事件、环境后果写入记忆。
9. 前端显示 NPC 当前行为、对话日志、今日计划、记忆和关系摘要。
10. 支持暂停、继续、单步执行。
11. 世界具备基础现实规则：碰撞、河流危险、护栏阻挡、建筑出入口切换。

### MVP 可暂缓

1. 复杂反思机制。
2. 25 个以上 NPC 大规模仿真。
3. 用户登录系统。
4. 复杂多楼层建筑和大型室内迷宫。
5. 复杂经济系统。
6. 高级地图编辑器。
7. 多人在线。

### 推荐演示场景

以 3 个 NPC 为主：

- 小明：学生，早上去学校，下午去奶茶店。
- 小芳：咖啡店店员，白天在咖啡店工作。
- 小王：程序员，上午在家工作，中午去餐馆，晚上去健身房。

可展示的故事线：

1. 早上小明从家出发去学校。
2. 小芳去咖啡店准备营业。
3. 小王中午去咖啡店买咖啡。
4. 小王和小芳对话。
5. 小芳记住小王喜欢美式咖啡。
6. 下次小王出现时，小芳主动提到咖啡偏好。

---

## 16. 分阶段实施计划

### 第一阶段：基础 Web 小镇

目标：先把地图和角色跑起来。

任务：

- 初始化 React + Vite + TypeScript。
- 接入 Phaser。
- 加载 Tiled 地图。
- 显示 3 个 NPC 精灵。
- 实现点击 NPC 打开信息面板。
- 实现前端假数据驱动移动。

验收：

- 浏览器能看到小镇。
- NPC 能沿路径移动。
- 点击 NPC 能看到资料。

### 第二阶段：后端服务与实时通信

目标：前后端联通。

任务：

- 初始化 FastAPI。
- 定义 Agent / Location / Event 数据结构。
- 实现 WebSocket 推送。
- 实现 `start/pause/step/reset` 接口。
- 前端接收 WebSocket 并驱动 Phaser。

验收：

- 后端每 tick 推送 NPC 位置。
- 前端实时播放移动动画。
- 可暂停和单步执行。

### 第三阶段：规则版 NPC 行为

目标：先不接 LLM，验证仿真框架。

任务：

- 为 NPC 编写固定日程。
- 按当前时间选择行动。
- 根据行动目标寻路。
- 到达地点后触发事件。
- 写入事件日志。

验收：

- 小镇一天流程能自动运行。
- NPC 会按时间去不同建筑。
- 事件日志能显示行为。

### 第四阶段：LLM 规划

目标：接入通义千问，让 NPC 自主生成计划。

任务：

- 实现 LLMService。
- 实现每日计划生成。
- 实现任务分解。
- 实现地点选择。
- 使用 JSON Schema 校验输出。
- 加入失败兜底。

验收：

- NPC 每天能生成不同计划。
- NPC 行为符合人物设定。
- LLM 格式错误不会导致系统崩溃。

### 第五阶段：记忆系统

目标：让 NPC 记住发生过的事情。

任务：

- PostgreSQL 接入。
- pgvector 接入。
- 实现记忆写入。
- 实现记忆检索。
- 实现时近性/重要度/相关性评分。
- 前端提供 MemoryViewer。

验收：

- NPC 事件会被保存为记忆。
- 决策时能检索相关记忆。
- 前端能查看某个 NPC 的记忆。

### 第六阶段：对话与社交

目标：NPC 之间能自然交互。

任务：

- 检测 NPC 距离。
- 判断是否对话。
- 生成多轮对话。
- 对话写入双方记忆。
- 前端显示气泡和聊天面板。

验收：

- 两个 NPC 靠近时可能聊天。
- 聊天内容与人物和记忆相关。
- 对话后双方记忆更新。

### 第七阶段：反思与产品化

目标：增强智能感和展示效果。

任务：

- 实现重要度累计。
- 触发反思。
- 更新人物当前状态。
- 实现仿真回放。
- 增加调试面板。
- 优化 UI 和演示脚本。

验收：

- NPC 能从经历中形成总结。
- 行为能受过去经历影响。
- 项目可完整演示。

---

## 17. 开发优先级建议

优先级从高到低：

1. **小镇可视化**：先让领导看到 Web 小镇。
2. **NPC 移动**：角色动起来，视觉效果立刻提升。
3. **行为状态面板**：展示 AI 决策过程，证明不是随机移动。
4. **LLM 计划生成**：体现 AI 自主性。
5. **记忆系统**：体现长期智能。
6. **对话系统**：体现 NPC 社交能力。
7. **反思系统**：作为高级亮点。

不要一开始就陷入记忆和反思的复杂实现。先做出可展示闭环，再逐步增强智能。

---

## 18. 风险与解决方案

### 风险 1：LLM 输出不稳定

解决：

- 所有 prompt 要求 JSON。
- Pydantic 校验。
- 自动重试。
- fail-safe 兜底。
- 日志记录原始输出，方便调试。

### 风险 2：LLM 调用太慢

解决：

- 行动未结束时不调用 LLM。
- 多 NPC 并发调用。
- 缓存每日计划。
- 简单行为走规则，不走模型。
- 用户可调仿真速度。

### 风险 3：成本过高

解决：

- MVP 只做 3-5 个 NPC。
- tick 设置为 5 分钟。
- 只在关键节点调用 LLM。
- 使用 qwen-plus，不默认使用 qwen-max。
- 对高频 prompt 做缓存。

### 风险 4：前端地图性能问题

解决：

- 地图和角色用 Phaser Canvas/WebGL。
- React 只做 UI 面板。
- 避免用 DOM 渲染大量格子。

### 风险 5：架构过重导致延期

解决：

- MVP 已采用 **Redis + RQ + 独立 worker**；未先引入 Celery。
- 已使用 PostgreSQL **pgvector** 与 Alembic 迁移；按阶段接 LLM。
- 每阶段保留可演示闭环（规则日程 → LLM）。

---

## 19. 仓库布局（当前 monorepo）

与 `virtual-town` 根目录一致；**前端/后端细部见 §5.1 / §6.1**。

```text
virtual-town/
├── backend/                 # FastAPI，见 §6.1
├── frontend/                # Vite + React，见 §5.1
├── docs/                    # 需求与实施方案（本章为总览）
├── scripts/                  # dev.sh、seed、reset_db、评测与 license 工具等
├── e2e/                     # Playwright（若启用）
├── generative_agents/       # 原版参考代码，非运行时依赖
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## 20. 结论

本项目建议采用 **React + Phaser + FastAPI + PostgreSQL/pgvector + WebSocket** 的现代 Web 架构。

和斯坦福原版相比，本方案保留其最有价值的 Agent 架构思想：

- 感知、检索、规划、反思、执行。
- 记忆流。
- 三因素检索。
- 层次化日程规划。
- 空间地址与地图事件。

同时替换掉原版不适合产品化的部分：

- 用 React 替代 Django 模板。
- 用 WebSocket 替代文件系统轮询。
- 用数据库替代 JSON 文件。
- 用 pgvector 替代 embeddings JSON。
- 用 JSON Schema 替代自然语言字符串解析。
- 用异步任务替代串行 LLM 调用。

如果用于转正任务，建议把重点放在 **可展示、可解释、可扩展** 三点：

1. 可展示：Web 小镇、NPC 移动、对话气泡、行为日志。
2. 可解释：前端能看到 NPC 当前计划、记忆和决策原因。
3. 可扩展：后端架构清晰，后续可增加 NPC、建筑、事件和更多模型。

最终交付时，哪怕 MVP 只有 3 个 NPC，只要能展示"人物设定 → 自动计划 → 地图行动 → 交互对话 → 形成记忆 → 后续行为受影响"这一闭环，就已经能很好地体现类似 Stanford 小镇的核心效果。
