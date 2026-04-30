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
| 数据库 | PostgreSQL + pgvector |
| 缓存/队列 | Redis |
| 实时通信 | WebSocket（增量广播） |
| 大模型 | OpenAI 兼容接口（通义千问 / DeepSeek 默认走 DashScope 兼容模式） |
| 容器化 | Docker Compose |

详细方案见 `docs/实施方案/`。

---

## 目录结构

```
virtual-town/
├── backend/                    # FastAPI 后端
│   ├── app/
│   │   ├── api/                # REST 路由
│   │   ├── core/               # 配置、日志、事件总线
│   │   ├── db/                 # ORM 模型、session、种子
│   │   ├── domain/             # 领域逻辑（world/simulation/agent/memory）
│   │   ├── llm/                # LLM 接入、tool calling
│   │   ├── schemas/            # Pydantic schema
│   │   ├── services/           # 应用服务
│   │   ├── websocket/          # WebSocket gateway
│   │   ├── prompts/            # 版本化 prompt 模板
│   │   └── main.py             # FastAPI 入口
│   ├── alembic/                # 数据库迁移
│   ├── tests/                  # 后端测试
│   └── pyproject.toml
├── frontend/                   # React + Vite 前端
│   ├── src/
│   │   ├── app/                # 全局入口与 provider
│   │   ├── pages/              # 页面组件
│   │   ├── components/         # UI 组件
│   │   ├── game/               # Phaser 游戏层
│   │   │   ├── scenes/
│   │   │   ├── systems/
│   │   │   └── eventBus.ts
│   │   ├── stores/             # Zustand
│   │   ├── api/                # REST/WS 客户端
│   │   └── types/              # 类型（含 OpenAPI 生成）
│   ├── public/assets/          # 美术资源
│   └── package.json
├── docs/                       # 产品需求与实施方案（权威）
├── scripts/                    # 一键启动、种子、重置
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## 一键启动

```bash
cp .env.example .env
# 编辑 .env，可选填入 LLM_API_KEY。未配置时 NPC 走规则行为，仿真照常可运行。

./scripts/dev.sh
```

启动完成后：

- 前端：<http://localhost:5173>
- 后端：<http://localhost:8000>
- OpenAPI：<http://localhost:8000/docs>

若无需本地 Python/Node 环境，也可完全走容器：

```bash
docker compose up --build
```

### 其它脚本

| 脚本 | 作用 |
|------|------|
| `./scripts/dev.sh` | 一键启动完整开发环境 |
| `./scripts/seed.sh` | 初始化演示世界 |
| `./scripts/seed.sh --if-empty` | 数据库为空时才初始化 |
| `./scripts/reset_db.sh` | 销毁并重建数据库 |

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

1. 打开 <http://localhost:5173> → 创建角色。
2. 进入小镇：小镇时间、NPC 列表、事件日志可见。
3. 走到咖啡店门口进入室内。
4. 靠近小芳点击对话：`小王喜欢喝什么？`
5. 小芳基于记忆作答，右侧面板显示相关记忆。
6. 点击豆豆抚摸：动物有情绪反应并写入事件。
7. 走到无护栏河边触发 `DROWNING` 状态。
8. 再次与小芳对话，确认她能感知刚才发生的事。

---

## 资源与授权

- 本项目视觉资源以 CC0 / 已购买授权为前提；详见 `docs/实施方案/视觉资源与美术资产方案.md`。
- LLM 调用仅在配置有效 `LLM_API_KEY` 时启用，所有外部调用都有超时与规则兜底。
- API Key、数据库密码只通过 `.env` 注入，禁止提交真实值。

---

## 文档入口

| 文档 | 作用 |
|------|------|
| `docs/产品需求/AI小镇产品需求文档PRD.md` | 产品约束 |
| `docs/实施方案/React版AI小镇实施方案.md` | 总体架构 |
| `docs/实施方案/实施方案目录与模块边界.md` | 模块拆分 |
| `docs/实施方案/数据模型与接口契约V1.md` | 前后端契约 |
| `docs/实施方案/ToolCalling工具契约V1.md` | LLM 工具集 |
| `docs/实施方案/MVP开发任务拆分.md` | 开发迭代计划 |
