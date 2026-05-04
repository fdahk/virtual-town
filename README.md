# AI 小镇 (Virtual Town)

> Web 版 2D AI 生活模拟小镇  
> 玩家可以创建角色进入小镇，与拥有记忆、性格、日程和自主行为的 NPC、动物互动。  
> 小镇中的 Agent 根据时间、环境、关系和记忆自主行动，对玩家行为产生持续反馈。

---

## 产品一句话

> AI 小镇是一个由 AI Agent 驱动、玩家可参与、事件会沉淀为记忆、角色会持续变化的 Web 版 2D 生活小镇。

核心闭环：

```
人物设定 → 自主计划 → 地图行动 → 玩家/NPC/动物交互 → 事件产生 → 记忆写入 → 后续行为变化
```

---

## 技术架构

| 层 | 选型 |
|----|------|
| 前端 | React + Vite + TypeScript + Phaser 3 + Tiled + Zustand + TanStack Query |
| 后端 | FastAPI + SQLAlchemy + Alembic + Pydantic v2 |
| 数据库 | PostgreSQL + pgvector（HNSW 索引） |
| 缓存/队列 | Redis + RQ（独立 worker 容器；`tasks` 表为权威状态与审计源） |
| 实时通信 | WebSocket（增量广播 + seq 检测 + 断线重连 + 快照恢复） |
| 可观测性 | 独立观测平台（trace/span、LLM / Tool / Task / Error 审计、WS 推送） |
| 大模型 | OpenAI 兼容接口（通义千问 / DeepSeek 默认走 DashScope 兼容模式） |
| 容器化 | Docker Compose |

详细方案见 `docs/实施方案/`。

---

## 目录结构

```
virtual-town/
├── backend/                    # FastAPI 后端
│   ├── app/
│   │   ├── api/                # REST 路由（含 /api/observability & /api/health/*）
│   │   ├── core/               # 配置 / 日志 / 错误码 / 事件总线 / TraceContext / Redis 客户端
│   │   ├── db/                 # ORM 模型、session、种子
│   │   ├── domain/
│   │   │   ├── world/          # 场景网格、A* 寻路、场景缓存
│   │   │   ├── simulation/     # 仿真引擎、规则版 Agent
│   │   │   ├── memory/         # 反思与日结
│   │   │   └── tasks/          # TaskQueue (enqueue) / runner (RQ 消费入口) / worker (RQ Worker 进程) / handlers / TTL worker
│   │   ├── llm/                # LLM 接入 + tool calling（全量 Observer 埋点）
│   │   ├── schemas/            # Pydantic schema
│   │   ├── services/           # WorldService / AgentService / PlayerService /
│   │   │                       # MemoryService / SimulationRuntime /
│   │   │                       # Observer / EventRouter
│   │   ├── websocket/          # WebSocket gateway + 观测 WS
│   │   ├── prompts/            # 版本化 prompt 模板
│   │   └── main.py             # FastAPI 入口（lifespan 统一拉起基础设施）
│   ├── alembic/                # 数据库迁移（含 pgvector HNSW 索引）
│   ├── tests/                  # 后端测试（含 trace / observer / task queue）
│   └── pyproject.toml
├── frontend/                   # React + Vite 前端
│   ├── src/
│   │   ├── app/                # 全局入口 + 路由切换（/observability 与玩家页面隔离）
│   │   ├── pages/              # 玩家页面（TownPage / CreatePlayerPage）
│   │   ├── pages/observability/ # 独立研发观测平台
│   │   ├── components/         # UI 组件
│   │   ├── game/               # Phaser 游戏层
│   │   ├── stores/             # Zustand
│   │   ├── api/                # REST / WS 客户端（ReliableSocket 自动重连 + 快照恢复）
│   │   └── types/              # 类型（含观测平台类型）
│   ├── public/assets/          # 美术资源
│   └── package.json
├── docs/                       # 产品需求与实施方案（权威）
├── scripts/                    # 一键启动、种子、重置、数据库备份
├── docker-compose.yml          # 开发向：全栈容器 + 代码目录挂载 + 热重载
├── docker-compose.demo.yml     # 体验向：镜像内构建、nginx 静态前端（scripts/start.sh）
├── .env.example
└── README.md
```

---

## 本地运行（三种方式）

共性准备：在仓库根目录执行 `cp .env.example .env`，按需编辑（`LLM_API_KEY` 可选；不配则 NPC 走规则行为，仿真仍可跑）。

### 方式一：推荐开发 — `scripts/dev.sh`（混合）

**适合**：日常改代码、断点调试、最快热重载。

| 组件 | 运行位置 |
|------|----------|
| PostgreSQL、Redis | Docker（`docker compose` 只起 `postgres`、`redis`） |
| 后端 API（uvicorn `--reload`） | 宿主机 `backend/.venv` |
| RQ Worker（可选 `watchfiles` 自动重启） | 宿主机 |
| 前端（Vite） | 宿主机 |

**依赖**：本机需安装 **Docker Desktop**、**Python 3.11+**、**Node 20+**；后端首次需在 `backend/` 执行 `python -m venv .venv && source .venv/bin/activate && pip install -e .[dev]`（或交给脚本在已有 `.venv` 时使用）。

```bash
./scripts/dev.sh
```

**访问地址**（默认端口，可在 `.env` 调整 `BACKEND_PORT` / `VITE_DEV_PORT`）：

| 用途 | URL |
|------|-----|
| 游戏前端 | <http://localhost:5173> |
| 研发观测台 | <http://localhost:5173/observability> |
| 后端 API | <http://localhost:8000> |
| OpenAPI | <http://localhost:8000/docs> |

停止：在运行 `dev.sh` 的终端 **Ctrl+C**（脚本会清理子进程）。数据库卷仍保留；下次 `./scripts/dev.sh` 可继续使用。

> `.env` 里需能通过 **localhost** 连上容器映射端口：`DATABASE_URL_LOCAL` / `DATABASE_URL_LOCAL_SYNC`、`REDIS_URL_LOCAL`（示例见 `.env.example`）。若本机 Postgres 占用了 `5432`，可把 `POSTGRES_PORT` 改为例如 `5433` 并同步改上述 `*_LOCAL` URL。

---

### 方式二：仅 Docker 体验 — `scripts/start.sh`（演示栈）

**适合**：给用户或演示环境：**只要装了 Docker**，不要求安装 Python / Node。

使用 **`docker-compose.demo.yml`**：镜像内安装依赖、前端 **nginx 托管构建产物**（非 Vite dev server），无源码目录挂载。

```bash
bash scripts/start.sh
# 或
./scripts/start.sh
```

脚本会检查 Docker、准备 `.env`、交互询问（或保留）`LLM_API_KEY`，再 `docker compose -f docker-compose.demo.yml up`。**若本机已有 `virtual-town-backend` / `virtual-town-frontend` 镜像，默认会跳过 `--build` 以加速二次启动**；改动了 `Dockerfile*`、`nginx.demo.conf` 等必须打镜像的内容后，请强制执行：

```bash
./scripts/start.sh --rebuild
```

其它参数：`./scripts/start.sh --no-open` 启动成功但不自动打开浏览器。

**访问地址**（默认 `FRONTEND_DEMO_PORT=8080`，见 `.env`）：

| 用途 | URL |
|------|-----|
| 游戏前端（nginx） | <http://localhost:8080> |
| 研发观测台 | <http://localhost:8080/observability> |
| OpenAPI | <http://localhost:8000/docs> |

停止：`docker compose -f docker-compose.demo.yml down`（数据卷默认保留；需清库可加 `-v`）。

> 大体积 **读档** 依赖 nginx `client_max_body_size`；若升级后仍 413，请确认已 **`--rebuild` 前端镜像**。

---

### 方式三：全容器开发 — `docker compose`（`docker-compose.yml`）

**适合**：希望与 CI/生产接近、或不想在本机装 Python/Node，但仍要 **卷挂载源码 + 热重载**。

```bash
docker compose up --build
```

后端 / worker / 前端容器内均为开发命令（如 `uvicorn --reload`、`npm run dev`），与 **方式一** 的访问端口习惯一致：前端 **5173**、后端 **8000**（见该 compose 内端口映射）。

---

### 其它脚本

| 脚本 | 作用 |
|------|------|
| `./scripts/dev.sh` | 混合开发一键启动（postgres/redis 容器 + 本机 API / worker / Vite） |
| `./scripts/start.sh` | 全 Docker 演示一键启动（`docker-compose.demo.yml`） |
| `./scripts/start.sh --rebuild` | 强制重新构建镜像后再启动 |
| `./scripts/seed.sh` | 初始化演示世界 |
| `./scripts/seed.sh --if-empty` | 数据库为空时才初始化 |
| `./scripts/reset_db.sh` | 销毁并重建数据库 |
| `./scripts/backup_db.sh` | `pg_dump` + asset manifest 快照到 `./backups/` |
| `python scripts/eval_prompts.py --mock` | Prompt 模板黄金样本自检（可加 `--strict` / 实调 LLM） |
| `python scripts/check_asset_licenses.py` | 校验 `manifest.json` 内资源许可证字段（可加 `--strict`） |

后端与观测相关的补充入口（详见 OpenAPI **`/docs`**）：`POST /api/agents/{agent_id}/decide`（单步 NPC 决策调试）、`POST /api/dialogue/*`（对话链路分段调试）。

---

## 手动开发模式

### 后端

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
alembic upgrade head
python -m app.db.seed
uvicorn app.main:app --reload
```

### 前端

```bash
cd frontend
npm install
npm run dev
```

运行后端后，可从 OpenAPI 重新生成前端类型：

```bash
cd frontend
npm run gen:types
```

---

## 演示脚本

（若用 **`start.sh` / demo 栈**，请打开 <http://localhost:8080> 替代下面步骤里的 5173。）

1. 打开 <http://localhost:5173> → 创建角色。
2. 进入小镇：小镇时间、NPC 列表、事件日志可见。
3. 走到咖啡店门口进入室内。
4. 靠近小芳点击对话：`小王喜欢喝什么？`
5. 小芳基于记忆作答，右侧面板显示相关记忆。
6. 点击豆豆抚摸：动物有情绪反应并写入事件。
7. 走到无护栏河边触发 `DROWNING` 状态。
8. 再次与小芳对话，确认她能感知刚才发生的事。
9. 阶段 19：观察 NPC 头顶状态气泡（💼 工作 / 🍴 吃饭 / 💤 睡眠 / 💬 对话 / ❓ 等待响应等）；
   忙碌时点击 NPC 发起对话会得到拒绝台词（软拒）或硬拒提示。两个 NPC 在地图上自发对话时，
   头顶会浮现台词气泡，事件日志记录 `dialogue.npc_to_npc_message`。

---

## 资源与授权

- 本项目视觉资源以 CC0 / 已购买授权为前提；详见 `docs/实施方案/视觉资源与美术资产方案.md`。
- LLM 调用仅在配置有效 `LLM_API_KEY` 时启用，所有外部调用都有超时与规则兜底。
- API Key、数据库密码只通过 `.env` 注入，禁止提交真实值。

---

## 研发观测平台

独立于玩家页面，用于**还原系统每一步运行过程**（一次决策、一次玩家对话、一次工具调用、一次记忆写入）。

访问地址（与启动方式一致）：

- **dev.sh / `docker-compose.yml` 全容器**：前端路由 <http://localhost:5173/observability>
- **`start.sh`（demo）**：前端路由 <http://localhost:8080/observability>（若改了 `FRONTEND_DEMO_PORT` 则替换端口）
- REST：`GET /api/observability/*`（经 demo 的 nginx 时为同源 `/api/...`）
- WS：`ws://localhost:8000/ws/observability/{simulation_id}`（浏览器若经 8080 同源访问，则由前端推断为 `ws://localhost:8080`，nginx 会转发到后端）

可观察的维度：

- **Dashboard**：仿真 / WS 在线数 / LLM 平均耗时 / 错误率 / 任务分布。
- **World Events**：世界事件流，按事件类型、实体、场景筛选。
- **Obs Events**：跨模块观测事件流（trace_id / category / level）。
- **Agent Runtime**：输入 `agent_id` 查看档案 + 状态 + Redis 热数据 + 最近事件 + 最近记忆。
- **Traces**：按 `trace_id` 聚合观测事件与 LLM / Tool / Task 明细。**仿真链路**下 MVP 通常为 **一整帧 world tick + 从这帧快照延续出去的异步任务（如 RQ）** 共用一个 ID；每条 **HTTP** 请求另起 trace。详见 `docs/实施方案/可观测性与错误处理方案.md` §3.3。
- **LLM Calls / Tool Calls / Tasks / Errors**：全量审计表 + 聚合。
- **Health**：`/api/health/db`、`/api/health/redis`、`/api/health/llm` 实时探测。

所有观测数据**默认脱敏**：API Key、Authorization、password、`raw_prompt` 自动打码，
超长字符串截断到 512 字符。

---

## 异步任务与数据层

- 所有 LLM / embedding / 反思 / 日结通过 `TaskQueue` 异步执行：
  - **消息层**：Redis + RQ（`rq>=1.16`），按优先级切 `vt:high / vt:default / vt:low` 三条队列。
  - **状态 / 审计**：`tasks` + `task_status_log` 表是权威源；Redis 只负责消息分发，
    RQ 结果 TTL 60s 即丢弃。
  - **部署**：`worker` 独立容器（`python -m app.domain.tasks.worker`），扩容用
    `docker compose up --scale worker=N`；`scripts/dev.sh` 本地开发自动并起。
- 幂等 key `{task_type}:{entity_id}:{simulation_step}`，重复 enqueue 不会重复入库；
  失败 + retryable + retry_count < max_retries 自动回推 RQ 并指数退避；终态
  `succeeded / failed / timeout` 落表供观测平台回放。
- 记忆检索：pgvector HNSW 索引（`vector_cosine_ops`，`m=16, ef_construction=64`）。
- Redis 短期记忆：`agent:{id}:short_memory`（TTL 24h）、`dialogue:{id}:recent_messages`、
  `world:{scene}:active_entities` 等；Redis 不可用时自动降级到 Postgres。
- TTL 清理 worker：过期短期记忆按重要度升级长期或归档；与 RQ 共享 worker 进程里的
  asyncio 持久 loop。

相关环境变量：

```
TASK_QUEUE_MODE=async         # async（默认，走 RQ）/ sync（inline LLM，用于回归）
TASK_QUEUE_BACKEND=rq         # 预留切 celery 的扩展点
TASK_QUEUE_HIGH=vt:high
TASK_QUEUE_DEFAULT=vt:default
TASK_QUEUE_LOW=vt:low
TASK_QUEUE_WORKER_COUNT=1
TASK_QUEUE_WORKER_CONCURRENCY=18       # 默认 18；须 ≤ db/session.py 连接池 ÷ ~2
OBSERVABILITY_FLUSH_INTERVAL=2
OBSERVABILITY_BATCH_SIZE=280
OBSERVABILITY_BUFFER_MAX_SIZE=4000
```

**近期默认调优（吞吐）**：适度提高 worker 并发与观测批量、`Observer` 落库改用 `session.add_all`；连接池调至 **22+22**/进程以匹配并发。**横向扩 worker** 时务必核对 Postgres `max_connections`。

**冷启动**（本仓库文档语境）：**backend 进程或 `vt_backend` 容器从停到再起**的这一次启动——会重新跑 `lifespan`、重新 `_load_state`、可选清 RQ / 收敛 `simulation.status`（见 `SIMULATION_AUTOSTART`、`SIMULATION_RESET_QUEUE_ON_START`）。**不是**「玩家第一次打开页面」；玩家侧只要不断开游戏 WebSocket，一般不会单独触发这一套。

**关页 / 断线会不会自动暂停？** 会——在默认配置下：`SIMULATION_AUTO_PAUSE_WHEN_NO_GAME_WS_CLIENTS=true` 时，**游戏场景** WebSocket（`/ws/simulations/{id}`）在**该 `simulation_id` 下最后一个连接**断开后，等待 `SIMULATION_AUTO_PAUSE_AFTER_IDLE_SECONDS`（默认 45s）仍无人重连，则 `POST` 等价暂停。**观测台** `/ws/observability` 不计入在线人数，避免「只看 Dashboard 仍被当作无人」误暂停。

**多玩家与「谁的世界」？** 当前是 **单世界 MVP**：数据库里通常只有 **一行** `simulation`（`engine._load_state` 用 `limit(1)`），全进程 **一个** `SimulationEngine`。REST `/players/me` **不做登录鉴权**，谁连到同一后端，看到的是**同一条** `GET /simulations/current`、同一个世界快照；多开浏览器等于**共享**这一个世界与同一个「玩家」角色，**没有**按用户 ID 隔离多实例世界。**不能**在同一套进程模型里同时跑多个相互独立、各有一套 22 NPC 的「租户世界」——要支持需要多引擎 / 多租户路由 / 鉴权，属于架构升级，不在当前 MVP 范围。

**观测台里事件 / 任务很多 = 性能不够？** 不一定。**任务队列 pending 大**多半说明 **LLM 慢 + worker 吞吐 < NPC 入队速率**（或历史积压未清），世界 tick 仍可能在跑，_NPC 决策_会滞后。**世界事件**条数随 `current_step` 增长是正常现象；若关心 **背压**，优先：**加 worker 副本**（`docker compose up --scale worker=N`）、酌情调 `TASK_QUEUE_WORKER_CONCURRENCY`（须 ≤ DB 连接池余量，见 `config.py` 注释）、略降 `SIMULATION_WORLD_TICK_HZ`（默认 2）或略增 `SIMULATION_AI_TICK_MINUTES` 减轻入队压力；若移动显得卡顿可调高 `SIMULATION_MOVEMENT_SUBSTEPS_PER_GAME_TICK`（会增加 WS delta 频率）。也可关 LLM 或压测时减小 NPC 数。若仍不足再考虑代码层批处理 / 优先级策略，而不是先改「整体架构」。


## E2E 验收

完整 6 个演示场景的 Playwright 自动化用例位于 `e2e/`：

```bash
./scripts/dev.sh                    # 起前后端
cd e2e
npm install
npx playwright install chromium
npm test
```

详见 `e2e/README.md`。

---

## 文档入口

| 文档 | 作用 |
|------|------|
| `docs/产品需求/AI小镇产品需求文档PRD.md` | 产品约束 |
| `docs/实施方案/架构总览.md` | 当前项目架构画像（与代码强一致，权威）|
| `docs/实施方案/React版AI小镇实施方案.md` | 架构演进史 / 技术选型背景 / 风险 |
| `docs/实施方案/实施方案目录与模块边界.md` | 模块拆分 |
| `docs/实施方案/数据模型与接口契约V1.md` | 前后端契约 |
| `docs/实施方案/智能NPC与记忆模块实施方案.md` | NPC / 记忆 / 交互请求协议（§9 阶段 19）|
| `docs/开发手册/契约文档/ToolCalling工具契约V1.md` | LLM 工具集（含阶段 19 社交 / 生活类工具）|
| `docs/实施方案/视觉资源与美术资产方案.md` | 美术资产决策与 fetch 流程 |
| `docs/实施方案/事件系统与任务调度模块实施方案.md` | 异步任务 / 事件 / 重试契约 |
| `docs/实施方案/可观测性与错误处理方案.md` | 观测平台与错误码规范 |
| `docs/实施方案/MVP开发任务拆分.md` | 开发迭代计划（§12 / §13 / §14 为基础设施层） |
| `docs/开发手册/prompts/开发记录.md` | 阶段交付进度（最新一条为阶段 19 社会化升级）|
| `docs/开发手册/debug/` | Bug 复盘文档 |
| `CREDITS.md` | 第三方资源署名 |
