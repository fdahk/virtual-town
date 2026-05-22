# AI 小镇 v2 架构与技术选型变更说明

> 日期：2026-05-22（v2-draft-1）/ 2026-05-23（v2-draft-2 修订）
> 范围：Virtual Town（`/Users/mac/virtual-town`）从 v1 到 v2 的架构与技术选型变更
> 依据：`/Users/mac/virtual-town/docs/架构总览/架构总览.md`（v1 权威架构，与代码强一致）
> 关联：本目录 `AI小镇v2架构演进与技术壁垒分析.md`、`校招技术方向决策记录.md`
> **重要：第二章对演进文档做了实质性修正，请优先阅读。**

> **v2-draft-2 修订（2026-05-23）**：基于"极致优化 + 100% 自有渲染信号是头号目标"的明确化，将 Phaser 从"分层共存"改为"**完全移除**"。WebGPU 接管整个 2D 渲染管线（tilemap / agent / 文字 SDF / 输入 / 摄像机）。详见 §3 / §4.1 / §5 / §6 / §7 的相应更新。**Node 引入暂不考虑**——后端保持 Python FastAPI，没有 v2 需求需要 Node。

---

## 一、原架构分析（v1 现状摘要）

### 1.1 v1 技术选型（摘自架构总览 §2）

| 层级 | v1 选型 | 职责 |
|---|---|---|
| 前端框架 | React + TypeScript + Vite | Web 应用、路由、UI 面板 |
| 地图渲染 | Phaser 3 + Tiled | 瓦片地图、精灵、相机、移动动画、富气泡 |
| 前端状态 | Zustand + TanStack Query | 仿真增量/选中实体 + REST 缓存 |
| 后端框架 | FastAPI + Pydantic v2 | REST、WS、lifespan 拉起运行时 |
| 数据库 | PostgreSQL 16 + pgvector(HNSW) | 世界/Agent/记忆/任务/观测 |
| 缓存/队列 | Redis 7 + RQ（独立 worker 容器） | 异步任务/短期记忆/缓存 |
| 实时通信 | WebSocket（统一信封 + seq + 重连快照） | 仿真 delta/对话/交互事件 |
| LLM 接入 | OpenAI 兼容（DashScope/通义千问） | chat_json/embeddings/tool calling |
| 容器化 | Docker Compose | postgres/redis/backend/worker/frontend |

### 1.2 前端架构的三条硬约束（v2 必须遵守）

从《数据流与前端状态管理方案》与架构总览 §4 提炼，v1 前端有三条不可破坏的约束：

1. **单向数据流**：后端 → api/ws client → TanStack Query / Zustand → React 组件 / EventBus → Phaser 渲染。禁止反向依赖（React 不直接 fetch、Phaser 不直接请求后端、Phaser 不改 React state）。
2. **React / Phaser 经 EventBus 分离**：两者不直接互改状态，只通过 `gameEventBus` 通信。
3. **Phaser 无 authority**：碰撞、危险、寻路、场景切换以后端 WorldService 为权威；Phaser 只渲染 + 命中输入。`worldStore` 是前端唯一运行态来源。

### 1.3 后端架构关键事实

- FastAPI + pgvector(HNSW) + Redis/RQ 三优先级队列 + 自研 Observer 观测平台。
- Agent 决策管线：`perceive → retrieve → plan_ctx → decide(LLM tool calling) → persist → broadcast → reflect`。
- **perception（视野计算）当前在后端 Python 执行**，是权威态。
- LLM 多 Provider / Ollama 本地推理为预留接口（`OLLAMA_ENABLED`、`LLM_PROVIDER_PRIORITY`），当前实现仍是单 Provider。

### 1.4 关键发现：v1 在 22 NPC 时已是"决策吞吐"瓶颈

架构总览 §11.4 明确记录：

> 22 NPC 默认配置下，每 ~0.6 真实秒触发一次 `agent_decision` 入队；SimpleWorker 单线程消费 LLM 任务（5–15s/次），吞吐压到 ~0.2 jobs/s，绝大多数任务在 deadline 内被丢弃。

他们通过"自定义并发 worker（concurrency=16）"把吞吐拉到 ~3.2 jobs/s，才勉强追平 22 NPC 的入队速率（~1.6/s）。

**这条记录直接决定了 v2 的真实瓶颈在哪——见第二章。**

---

## 二、关键诚实修正（必须先读）

本章修正《AI小镇v2架构演进与技术壁垒分析》里一个站不住的前提。这个修正基于 v1 真实架构文档证据，不是观点。

### 修正 1：200 NPC 不会压垮 Phaser 渲染

Phaser 3 用 WebGL 批渲染（sprite batching），渲染 200 个精灵对它是 trivial 的——Phaser 官方 demo 常规跑上万精灵。**"scale 到 200 → Phaser 渲染撑不住 → 需要 WebGPU" 这个前提是错的。**

唯一在 200 量级可能真出现的 Phaser 渲染压力，是 `BubbleManager` 的富气泡——Phaser 文字渲染较贵，200 个 NPC 同时挂文字气泡会有 overdraw 成本。但这是个窄问题，不足以撑起"重写渲染层"。

### 修正 2：规模化的真瓶颈是后端 LLM 决策吞吐

由 §1.4，22 NPC 时决策入队 ~1.6/s、worker 吞吐 ~3.2/s，已经接近打平。线性外推到 200 NPC：

- 决策入队需求 ≈ 14.5 jobs/s
- 需要 ~5 个 worker 容器，或单 worker concurrency ~70+
- concurrency 70 × 2 session ≈ 140 DB 连接，远超当前连接池（40），要同步扩 PostgreSQL `max_connections`
- 还会撞 LLM provider 的 RPM 上限

**结论：200 NPC 首先是后端决策吞吐 + LLM 成本问题，不是前端渲染问题。**

### 修正 3：WebGPU 的正当性必须从"渲染吞吐"重构为"GPU 计算"

既然渲染吞吐撑不起 WebGPU，WebGPU 在本项目的正当理由要换成：

- **GPU 计算（compute）**：perception 是 O(N²) 的空间计算（N 个 agent × N 个候选实体），随规模增长真实变贵；Phaser **完全没有 compute 能力**——这是 WebGPU 独有、无法被 Phaser 替代的能力。**这才是真桥梁。**
- **计算驱动的视觉效果**：fog-of-war 感知可视化、按 agent 状态实时生成的视觉效果——依赖 compute 结果，自然落在 WebGPU 渲染层。
- **渲染与计算共用 GPU 资源**：compute 产出的 mask/buffer 直接被渲染采样、零拷贝——这个统一管线本身是 WebGPU-only 的架构优势。

**面试叙事相应改写**：不是"性能逼我换 WebGPU"，而是"perception 计算需要 GPU 并行，且我要让渲染和计算共享 GPU 资源，于是建了 WebGPU compute+render 统一管线"。这个叙事仍然成立、仍有图形深度，只是动机变了。

### 修正 4：浏览器端 WebGPU 推理不缓解后端决策瓶颈

NPC 的 `agent_decision` 跑在后端 RQ worker。浏览器里的 WebGPU 小模型**够不到后端这条队列**。所以端侧推理：

- ✅ 能做：玩家侧特性（玩家文本意图分类、客户端预览）、图形/AI 专长信号、端云混合架构演示
- ❌ 不能做：缓解 200 NPC 带来的后端决策吞吐危机

如果真想用"小模型预筛"缓解后端瓶颈，那是**服务端小模型**（NPC 决策前的廉价分类器，决定哪些决策不必上 LLM）——属于后端变更，和"浏览器 WebGPU 端侧推理"是两件事、两个不同的"端"。两者都可做，但**不要混为一谈、不要宣称浏览器端推理解决了规模危机**。

### 对演进文档的影响

《AI小镇v2架构演进与技术壁垒分析》中 M1/M2 的"渲染瓶颈驱动"叙事需按本章修正。M3（GPU compute perception）、M4（端侧推理）的技术内容仍成立，但 M1 的定位、M2 的正当性论证、整体叙事主线要改。**建议本文定稿后回头同步更新那份文档。**

---

## 三、技术选型变更对照表（v1 → v2）

| 层级 | v1 | v2 | 变更类型 |
|---|---|---|---|
| 前端框架 | React + TS + Vite | 不变 | — |
| **2D 渲染引擎** | Phaser 3 | **❌ 完全移除** | 移除 |
| **2D 渲染管线（v2 全新）** | 含在 Phaser 内 | **WebGPU 全量自有渲染层**（tilemap + agent 实例化 + 文字 SDF + 输入 + 摄像机 + 场景切换） | 🆕 新增 |
| 地图数据格式 | Tiled `.tmj` | **保留**（由自写 WebGPU 渲染器解析渲染，不再走 Phaser tilemap） | 渲染端切换 |
| GPU 计算 | 无 | **WebGPU compute（perception 可视化等）** | 🆕 新增 |
| 端侧推理 | 无 | **浏览器 WebGPU 模型推理（transformers.js / onnxruntime-web WebGPU EP）** | 🆕 新增 |
| 前端状态 | Zustand + TanStack Query | 不变（WebGPU 渲染器为 worldStore 新订阅者） | — |
| 后端框架 | FastAPI + Pydantic v2 | 不变 | — |
| 数据库 | PostgreSQL 16 + pgvector | 不变 | — |
| 缓存/队列 | Redis 7 + RQ | 不变（扩规模则增加 worker 副本，用现有机制） | — |
| 实时通信 | WebSocket（信封 v1.0） | 不变 | — |
| LLM 接入 | OpenAI 兼容 | 不变（可选：增服务端小模型预筛，独立于浏览器端侧推理） | 可选新增 |
| 容器化 | Docker Compose | 不变 | — |
| 仿真规模 | 22 NPC | 待定（见 §4.5） | 扩展 |

**核心观察**：v2 的技术选型变更**全部集中在前端**，后端基本不动。这正好契合"前端为主"的定位。

---

## 四、架构变更详述

### 4.1 前端渲染层：Phaser 完全移除，WebGPU 全量接管

```
┌─ WebGPU canvas（唯一渲染层）── tilemap + agent + 文字 SDF + 输入 + 摄像机 + compute ─┐
┌─ React DOM（z-index: 10）──── 面板/表单/调试/观测 ─────────────────────────────────┐
```

**为什么不再分层共存（v2-draft-2 修订）**：
- 头号目标已明确为"极致优化 + 100% 自有渲染层"
- 分层共存仍欠引擎的债（tilemap / 文字渲染仍是 Phaser），"100% 自有"简历叙事不成立
- 文字渲染（SDF）是 M2 最值钱的子模块；如果被 Phaser 接走，核心难点的 claim 也丢了

**代价（必须接受）**：
- WebGPU 必须自己实现：tilemap 渲染、SDF 文字渲染（含中文）、输入命中测试、摄像机、tween、场景切换
- 渲染层工作量约翻倍：M2 从 2 月 → 4 月，总时间 4–6 月 → 6–9 月
- 失去 Phaser 兜底；回退靠各 WebGPU 子模块独立 feature flag + 保留 v1 分支

详细架构与方案对比见演进文档第五章。

### 4.2 数据流变更：WebGPU 渲染器作为 worldStore 的新订阅者

v2 的 WebGPU 渲染器**必须遵守 §1.2 三条硬约束**：

- 它是 `worldStore` 的**第三个消费者**（与 React、Phaser 并列），不持有 authority。
- 它经 EventBus（或直接订阅 store）接收 agent 增量，**不自己维护一套坐标**——这正是《数据流方案》§1 明确点名要避免的反模式（"Phaser 和 React 各自维护一套角色位置"）。
- 它不请求后端。

单向数据流从 `后端 → store → {React, Phaser}` 扩展为 `后端 → store → {React, Phaser, WebGPU}`。**骨架不变，只多一个出口。**

### 4.3 新增：GPU 计算层

- WebGPU compute shader 计算 perception 可视化（fog-of-war）。
- **与后端权威 perception 的关系**：后端仍是权威态，前端 compute 是交互级可视化预览（类似游戏网络同步的客户端预测 + 服务端校验）。
- 不改变 §1.2 约束 3——authority 仍在后端。

### 4.4 新增：端侧推理层

- 浏览器内 WebGPU 模型推理。
- **正确定位（见修正 4）**：服务于玩家侧特性 + 专长信号，**不承担后端 NPC 决策**。
- 与后端已预留的多 Provider/Ollama 抽象是不同层：那是服务端 LLM provider 切换，这是浏览器内的 pre-LLM 过滤或玩家侧推理。两者不冲突、不互相替代。

### 4.5 仿真规模：诚实的规模目标

不同规模触发不同瓶颈，按"想要的故事"选：

| 规模 | 决策吞吐 | 渲染 | compute 故事 | 需要的架构改动 |
|---|---|---|---|---|
| 22（现状） | 已打平 | trivial | 弱 | 无 |
| ~100 | 需 ~3 worker / 扩并发 | 仍 trivial | perception O(N²) 开始值得上 GPU | 后端扩 worker（用现有机制） |
| 500–1000+ | LLM-per-NPC 不可行 | 文字气泡等开始真压 Phaser | 最强 | **必须分层认知**：少数 NPC 全 LLM + 多数 NPC 规则行为 |

**建议**：
- 若保持"每个 NPC 都 LLM 决策"，诚实的规模上限是 **~100**，WebGPU 故事建立在 **compute** 上。
- 若愿意做"分层认知架构"（hero NPC 全认知 + background NPC 规则/廉价行为），可上 1000+，此时渲染吞吐故事也真成立——但这是更大的架构改动。**分层认知本身是个很好的额外亮点**（真实游戏 AI 和 Generative Agents 后续研究都这么做）。

---

## 五、明确不变的部分（scope 边界）

为控制 blast radius，v2 明确**不动**：

- 后端框架、数据库、队列、WS 信封、LLM 接入方式
- Agent 决策管线结构、记忆系统、工具契约
- 单向数据流原则
- 观测平台
- 部署方式（Docker Compose）
- **Tiled 地图数据格式**（`.tmj` 文件继续作为地图数据来源，只是渲染器从 Phaser 换成自写 WebGPU）

**注意（v2-draft-2 修订）**：Phaser 不再保留，整个前端渲染层被替换。但**后端、数据契约、Agent 系统**全部不动——v2 的修改严格集中在前端渲染层 + 端侧推理层。

**v2 = 后端基本不动 + 前端渲染层重写 + 前端新增 GPU 计算 / 端侧推理层。**

---

## 六、对现有契约/约束的影响

| v1 约束/契约 | v2 影响 |
|---|---|
| 单向数据流 | 扩展（WebGPU 渲染器作为新消费者），原则不变 |
| React/Phaser 经 EventBus 分离 | **Phaser 移除**；改为 React/WebGPU 经 EventBus 分离，模式不变 |
| Phaser 无 authority | **N/A**（Phaser 移除）；WebGPU 同样无 authority |
| 后端 perception 权威 | 不变；前端 GPU perception 仅可视化预览 |
| WS 信封 v1.0 | 不变 |
| worldStore 唯一运行态来源 | 不变；WebGPU 渲染器订阅它 |
| 后端 API / 数据库 schema / 队列契约 | 全部不变 |
| Tiled `.tmj` 数据格式 | 不变（解析渲染从 Phaser 转到自写 WebGPU 渲染器） |

**核心：v2 的所有变更（含 Phaser 移除）都挂在 v1 已有的数据流骨架上，不破坏后端任何契约。**

---

## 七、迁移策略与回退

- **WebGPU 子模块独立 feature flag**：每个子模块（tilemap / agent / 文字 / 输入 / camera / compute / 端侧推理）独立可开关，单点出问题不阻塞其他模块的演进
- **渐进迁移顺序**：WebGPU 管线初始化 → agent 实例化 → tilemap → 文字 SDF → 输入/摄像机 → compute perception → 端侧推理，每步独立可 ship
- **没有 Phaser 兜底**：这是 v2-draft-2 的代价之一；最坏情况的回退靠**保留 v1 分支**——v2 推不动时可以 `git checkout` 回 v1，前端可用性不受影响
- **数据契约不变**：后端与 WS 信封不动，v2 前端任何阶段都能与现有后端协作（这也是 v1 分支回退的前提）

---

## 八、变更影响评估与置信度

| 项 | 评估/置信度 |
|---|---|
| v2 变更集中在前端、后端不动 | 95% |
| 所有变更不破坏 v1 后端契约（API / DB / WS） | 90% |
| "200 NPC 不压垮 Phaser 渲染、真瓶颈在后端决策吞吐" | 90%（基于 §11.4 实证 + Phaser 批渲染常识） |
| WebGPU 正当性应建立在 compute 而非渲染吞吐 | 85% |
| ~100 NPC 是 LLM-per-NPC 模式的诚实规模上限 | 75% |
| **WebGPU 全量自有渲染层（无 Phaser 兜底）9 月内完成** | **45%**（文字 SDF 是最大变数） |

**变更风险等级（v2-draft-2 修订后）：中–高**。Phaser 移除让渲染层从"增量"变成"重写"，没有兜底；最大不确定性集中在 WebGPU 学习曲线、文字 SDF、规模目标选择。但**所有变更仍严格不破坏后端契约**——最坏可 `git checkout` 回 v1 分支。

---

## 九、待决事项

- [ ] 规模目标定档：~100（compute 故事）还是 1000+（需分层认知架构，与"极致优化"叙事最配）
- [ ] 是否引入服务端小模型预筛（独立于浏览器端侧推理的可选后端变更）
- [x] ~~据本文修正同步更新 `AI小镇v2架构演进与技术壁垒分析.md` 的 M1/M2 章节~~ → 已在 v2-draft-2（2026-05-23）完成
- [ ] WebGPU 起点确认后，制定 P1 逐周计划
- [ ] Node 引入：暂不考虑（保持 Python 后端）；如未来出现强 narrative 理由再单独评估
