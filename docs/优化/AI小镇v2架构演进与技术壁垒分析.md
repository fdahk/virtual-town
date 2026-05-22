# AI 小镇 v2 架构演进与技术壁垒分析

> 项目路径：`/Users/mac/virtual-town`
> 当前状态：v1 已交付到阶段 19（Generative Agents 风格 + 22 NPC + Phaser 渲染 + 完整后端基础设施 + 观测平台）
> 目的：定义 v2 演进路径，**重点分析每一步如何提升技术壁垒**，为腾讯前端 + 全栈校招提供完整深度信号
> 时间预算：6–9 个月集中投入（Phaser 完全移除 + WebGPU 全量自有渲染层使渲染工作量翻倍）
> 相关：`校招技术方向决策记录.md`（决策依据）/ `AI小镇v2架构与技术选型变更说明.md`（v1→v2 技术选型 diff、关键诚实修正）

> **本版变更（v2-draft-2，2026-05-23）**：
> - **Phaser 完全移除**，WebGPU 接管整个 2D 渲染管线（tilemap / sprite / 文字 / 输入 / 摄像机），不再做分层共存——理由：极致优化 + 100% 自有渲染层信号是头号目标
> - **关键诚实前提**（详见变更说明 §2）：scale 到 200 NPC 不会压垮 Phaser 渲染，真瓶颈是后端 LLM 决策吞吐；WebGPU 正当性建立在 GPU compute + 100% 自有渲染 + 计算驱动视觉效果上，不在"渲染吞吐"
> - 时间预算从 4–6 月放宽到 6–9 月
> - 后端不变（保持 Python FastAPI；Node 引入暂不考虑）

---

## 一、v1 现状盘点：分层壁垒评估

| 层 | 当前实现 | L 等级 | 评估 |
|---|---|---|---|
| **AI Agent 系统** | LLM + Tool Calling、记忆架构（pgvector HNSW）、反思/日结、prompt 版本化、评测 | **L3–L4** | 已达专家级，是项目最强信号之一 |
| **后端基础设施** | FastAPI + SQLAlchemy + Alembic + Redis/RQ（优先级队列、幂等、退避重试）+ 自研观测平台（trace/span/LLM 审计）+ 可靠 WebSocket（断线重连 + 快照恢复） | **L3–L4** | 大幅超出"中型通用业务"水位 |
| **前端业务** | React + Zustand + TanStack Query + 可靠 WS 客户端 + 观测平台前端 | **L2–L3** | 工程合理，可靠 WS 客户端有亮点 |
| **图形渲染** | Phaser 3 + Tiled tilemap，scene 业务逻辑（7 个文件） | **L1–L2** | 调包级，无 GPU 控制权 |
| **端云推理分工** | 全部走服务端 LLM | **L1** | 没有端侧推理 |
| **大规模仿真** | 22 NPC，无 scale 压力 | N/A | 未触及规模问题 |

### 核心问题（v2 必须同时解决）

1. **后端 / 前端代码比 3:1 偏后端**，与"前端为主"目标倒挂
2. **图形渲染层是壁垒洼地**，与目标专长（图形渲染）不匹配
3. **缺一个"v2 升级动机"** —— 当前看起来是"完成度高的 v1"，缺乏"持续演进"信号；面试缺乏架构演进叙事

---

## 二、v2 目标定位

### 战术目标
- 把"图形渲染"层从 **L1–L2 → L3–L4**
- 把"端云推理分工"层从 **L1 → L3**
- 让前后端代码比从 3:1 修正到 **≈1:1 或 1.2:1 偏前端**
- 形成"v1 完成 → v2 因规模/性能瓶颈演进"的完整技术叙事

### 战略目标

让面试官从这一个项目里同时读到 5 类信号：
- 全栈系统（v1 已有）
- AI Agent 架构（v1 已有）
- **图形渲染专长（v2 新增）** ⭐
- **端云混合推理（v2 新增）** ⭐
- **性能瓶颈驱动技术决策（v2 新增）** ⭐

---

## 三、v2 演进路径

### 模块清单（按必要性分级）

| 模块 | 等级 | 工作量 | 壁垒提升 |
|---|---|---|---|
| **M1 NPC 规模扩展 + 后端决策吞吐扩容** | A 必做 | 1 月（含性能基线/压测 + worker/分层认知评估） | 触发 v2 演进、暴露后端真瓶颈 |
| **M2 WebGPU 全量自有渲染层** | A 必做 | **4 月**（agent 实例化 + tilemap + sprite + 文字 SDF + 输入 + 摄像机） | 图形 L2→L4 + **100% 自有信号** |
| **M3 WebGPU compute perception 可视化** | A 必做 | 1 月 | 图形 L3→L4 + 新增 GPU 计算能力 |
| **M4 端侧小模型推理（WebGPU）** | A 必做 | 1 月 | 端云推理 L1→L3 |
| **M5 v2 渲染层技术决策文档（ADR）** | A 必做 | 0.25 月 | 元产出，面试材料 |
| **M6 昼夜/天气 shader** | B 加分 | 0.5 月 | 图形信号强化 |
| **M7 小地图 + GPU 实例化** | B 加分 | 0.5 月 | 工程闭环 |
| 后处理 / bloom / 视觉炫技 | **C 拒绝** | — | 无产品驱动，bolt-on |
| 独立 WebGPU showcase 页 | **拒绝** | — | 与项目无关 |

总工作量：A 级合计 **~7.25 月**，B 级合计 **1 月**，9 月窗口可吃完 A 级 + 部分 B 级。M2 是工作量大头——文字渲染（SDF）子模块单独是个硬骨头，时间预算上要给它留出独立的 0.5–1 月。

---

## 四、各模块技术壁垒提升分析（核心章节）

本章是整份文档的重点。每个模块都按统一格式分析：
**当前壁垒 → 目标壁垒 → 提升路径 → 面试可讲点 → 业界对比 → 踩坑**

---

### M1：NPC 规模扩展 22 → 200

**当前壁垒**：N/A（未触及规模问题）

**目标壁垒**：触发整套 v2 演进的产品驱动力

#### 为什么这是 v2 的命根子

没有这一步，WebGPU/GPU compute/端侧推理全部失去合理性。面试官会问"22 个 sprite 为什么需要 WebGPU"——没有 scale 这一步你答不上来。**M1 不是为了 scale 本身，是为了让 M2/M3/M4 有产品逻辑支撑**。

**Stanford Generative Agents 原版只有 25 agent**。scale 到 200 不是"简单数量增加"，会触发三个真实瓶颈：
- O(N²) 感知计算崩盘 → M3 的合理性
- 渲染 draw call 暴涨 → M2 的合理性
- LLM 调用成本爆炸 → M4 的合理性

#### 面试可讲点
- 性能基线测量方法（如何确认 22→200 哪个环节先崩）
- 决定从哪个环节优先重写的决策
- 这是"性能驱动技术演进"的真实样本

#### 业界对比
- Stanford Generative Agents（论文版）：25 agent，纯展示
- 各类 agent 模拟 demo：通常 < 50 agent
- 商业级模拟（如 AI Dungeon、商业 NPC 系统）：闭源不可比
- **你的 200 agent**：属于"研究级 + 工程级"中间地带，公开项目里属于偏上水平

#### 踩坑警告
- 不能只是把 22 改成 200 而不真跑——**必须有压测数据**
- 必须真 spawn 200 个 agent 并跑仿真，看到实际瓶颈，否则 v2 故事是空中楼阁
- 数据库连接池、Redis 队列、LLM 并发都可能率先成瓶颈，要逐项排查

---

### M2：WebGPU 全量自有渲染层（Phaser 完全移除）

**当前壁垒**：L1–L2（Phaser 调包）—— 用 Phaser 提供的 sprite/tilemap/text API，对 GPU 没有控制权

**目标壁垒**：L3–L4（**自实现整个 2D 渲染管线**）—— 不仅 agent 层，连 tilemap、文字、输入、摄像机全部手写，渲染层 100% 自有，没有任何一行渲染外包给引擎

#### 范围（Phaser 完全移除后必须自实现）

| 子模块 | 工作量 | 难度 | 说明 |
|---|---|---|---|
| WebGPU device/管线初始化 | 0.25 月 | 中 | 标准流程 |
| Agent 实例化渲染（sprite atlas + instancing） | 0.75 月 | 中-高 | M2 的图形信号核心 |
| Tilemap 渲染（Tiled `.tmj` 解析 + 多图层 + chunk） | 0.75 月 | 中 | 路径清晰但量不小 |
| **文字渲染（SDF 字体图集 + 字形布局 + 中文集）** | **1 月** | **高** | WebGPU 文字渲染是独立硬题；中文字符集大、抗锯齿、动态 layout 都是坑 |
| 输入/命中测试（点击 NPC / tile） | 0.5 月 | 中 | 没有 Phaser input system 帮你；要自己做坐标变换 + 命中 |
| 摄像机 / viewport / 跟随 | 0.25 月 | 低-中 | 自己维护投影矩阵 + 平滑跟随 |
| Tween / 移动动画 | 0.25 月 | 中 | 插值 + 帧调度 |
| 场景切换 / portal 渲染 | 0.25 月 | 低 | 主要状态管理 |

**合计 ~4 月**，是 v2 的工作量大头。

#### 壁垒提升路径

| 等级 | 标志 | 你当前/目标 |
|---|---|---|
| L1 | 会用 Phaser 显示一个 sprite | v1 已达成 |
| L2 | 能讲 Phaser 内部 sprite batching 原理 | 建议过一遍 Phaser 源码作为对比基线 |
| L3 | 自写 WebGPU 渲染层覆盖完整 2D 管线 | **v2 目标** |
| L4 | 能讨论 instanced rendering / SDF 字体 / 命中测试 / 摄像机的设计取舍，能针对场景做优化 | **v2 上限** |

#### 面试可讲点
- WebGPU vs WebGL 的 API 设计差异（command buffer 模式 vs immediate 模式）
- Instanced rendering 的 draw call 优化原理
- **SDF 字体渲染原理**（signed distance field、glyph atlas、抗锯齿）—— 文字渲染最有讲点的部分
- Atlas + array texture 的取舍
- 命中测试的坐标系转换（屏幕 → world → tile）
- 摄像机投影矩阵的设计
- "为什么完全移除 Phaser 而不是分层共存"——核心回答：极致优化 + 100% 自有渲染层是图形专长信号最强的形式；分层共存仍欠引擎的债

#### 业界对比
- **Phaser 3 / PixiJS**：成熟 2D 引擎，提供高层 API，控制权低
- **Three.js**（含 WebGPU 后端）：3D 为主，2D 不够专精；调包仍是调包
- **Skia / Cairo**：原生 2D 渲染库，是 Chrome/Flutter 等的底层
- **你的全量 WebGPU 2D 渲染层**：覆盖范围接近 PixiJS 的核心，但是 WebGPU 实现、为本项目特调，每一处优化都是你的

#### Web vs Native
- Native 图形（Vulkan/Metal/DX12）：command buffer 模式、显式同步、显式资源生命周期
- WebGPU 是它们的子集 + Web 化封装：理念一致，受 Web 安全沙箱约束
- **文字渲染**在 native 也是难题（freetype + harfbuzz 是标准栈）；Web 端常退化为 canvas2d 兜底，自己用 SDF 在 WebGPU 里跑是非主流但有迹可循（troika-three-text / msdfgen 是参考）

#### 踩坑警告
- WebGPU 学习曲线陡（必须先把 spec 啃一遍）
- **文字渲染是隐形大坑**：中文字符集大（CJK ~20k 字形），不能全图集，必须做动态 glyph 缓存 + LRU；SDF 文字在小字号下抗锯齿要调参
- 命中测试要谨慎处理 retina / DPR、相机变换、世界坐标 ↔ 屏幕坐标
- **没有 Phaser 兜底意味着没有回退路径**——任何一个子模块卡住都会阻塞整个前端可用性；必须各子模块独立 feature flag
- 浏览器兼容性：Chrome/Edge 稳定，Safari 还在追赶（README 标明运行环境）
- 调试工具：WebGPU 在 DevTools 里支持差，建议用 Chrome 的 WebGPU Inspector 扩展

---

### M3：WebGPU compute perception 可视化

**当前壁垒**：感知逻辑在 Python 后端 CPU 上跑；前端零 compute 能力

**目标壁垒**：L3–L4（前端 GPU compute 能力 + 算法到 shader 的映射 + 架构权衡讨论能力）

#### 技术内容
- 选中 agent 时，前端用 WebGPU compute shader 计算它的可见 entity 集合
- Compute shader 实现：每个 thread 处理一个候选 entity，并行算距离 + 视线遮挡
- 结果以 fog-of-war 风格可视化叠加在地图上
- 与后端权威 perception 的关系：前端 GPU 算的是"可视化预览"，后端的是"权威态"（用户交互前端先响应、后端校验，类似游戏网络同步的客户端预测）

#### 壁垒提升路径

| 等级 | 标志 | 你当前/目标 |
|---|---|---|
| L1 | 不会写任何 shader | v1 状态 |
| L2 | 能写 fragment shader（颜色变化、滤镜） | M2 阶段会过 |
| L3 | 能写 compute shader 并设计 workgroup 大小、share memory 使用 | **v2 目标** |
| L4 | 能讨论 GPU/CPU 算 perception 的架构权衡、能优化 workgroup 调度 | **v2 上限** |

#### 面试可讲点
- Compute shader 的 workgroup / dispatch 模型
- 为什么 perception 适合 GPU 并行（数据并行性强、每个 thread 工作独立）
- 前端预测 + 后端校验的架构模式（与游戏网络同步同源）
- O(N²) 在 GPU 上的实际加速比（200 × 200 = 40000 次距离计算，CPU 串行 vs GPU 并行）
- 与"全做后端"或"全做前端 CPU"的对比取舍

#### 业界对比
- 没有直接的 Web 对手——大多数游戏 perception 在主循环 CPU 做
- 类似思路：**Frostbite/Unreal 的 GPU-driven culling**（剔除不在视野内的对象）
- 学术参考：**boids/flocking 的 GPU 实现**（GDC 演讲常见）
- **你的实现**：把这个工业级模式搬到 Web + 简化到能讲清的体量

#### Web vs Native
- Native 游戏引擎里 GPU compute 用于 culling、物理、粒子已是标准做法
- Web 端用 compute shader 还是较前沿（WebGPU 2023 正式发布），稀缺性强
- 这意味着同样的能力在 Web 上更稀缺、信号更强

#### 踩坑警告
- Compute shader debugging 几乎没工具（不像 fragment shader 能视觉 debug）—— 要靠 buffer readback + console
- 数据上传/下载的 PCIe 带宽容易成瓶颈（不要每帧都 readback）
- 必须有"为什么不放后端"的清晰答案——否则会被质疑越俎代庖。**正确回答**：前端做的是"交互响应级的可视化预览"，后端的是"权威仿真态"，是双轨而非替代

---

### M4：端侧小模型推理（WebGPU）

**当前壁垒**：L1（全部 LLM 调云端 API）

**目标壁垒**：L3（端云推理混合架构 + WebGPU 推理调用 + 性能/成本量化）

#### 技术内容
- 选一个高频低复杂度任务作为切入点：
  - **推荐**：意图分类——把 NPC 决策前的"该用哪个 tool"判断从 LLM 截下来
  - **备选**：情绪标签 / 短文本相似度（可复用记忆检索场景）
- 集成 `transformers.js` + WebGPU 后端，或 `onnxruntime-web` 的 WebGPU EP
- 模型选择：DistilBERT / MiniLM 量级（< 50MB），ONNX 或 safetensors 格式
- 设计端云路由：何时端侧、何时降级到 LLM
- 量化效果：截走多少 LLM 调用、延迟变化、成本节约

#### 壁垒提升路径

| 等级 | 标志 | 你当前/目标 |
|---|---|---|
| L1 | 只调 OpenAI/DashScope API | v1 状态 |
| L2 | 会用 transformers.js 调包跑模型 | 集成时就过 |
| L3 | 理解 WebGPU EP 推理流程，能设计端云路由策略 | **v2 目标** |
| L4 | 能优化推理算子或量化模型 | 远超 v2 范围，不强求 |

#### 面试可讲点
- 端侧推理的四大驱动力：**隐私、延迟、成本、离线可用**
- WebGPU EP vs WASM EP 的性能差异（GPU 加速度 vs CPU 兜底）
- 模型量化（INT8/INT4）的精度损失权衡
- 推理与渲染共用 GPU 的调度问题（v2 的硬 20%）
- 这是真实产品的真实决策（即梦 / 剪映 / 微信识物等都是混合架构）

#### 业界对比
- **`transformers.js`**：Hugging Face 出品，WebGPU 后端较新；解决"能跑"，没解决"调度协同"和"路由策略"
- **`onnxruntime-web`**：微软出品，WebGPU EP 在 2024 年稳定；同样只是 runtime
- **WebLLM**：跑大模型（Llama/Qwen），离你的场景太重
- **MediaPipe**：Google 的端侧 ML 框架，偏视觉
- **你的实现**：把端侧推理嵌入到完整的 agent 决策链中并量化收益，这是 runtime 库没解决的"应用层架构"

#### Web vs Native
- Native 端侧推理：CoreML（iOS）、NNAPI（Android）、Apple Neural Engine、Snapdragon NPU
- Web 端因为没有 NPU 直接访问权，只能走 GPU；性能不如 native 但跨平台
- 端侧推理是行业大趋势（不只 Web）：苹果 Apple Intelligence、Google Gemini Nano、华为盘古端侧版

#### 踩坑警告
- 首屏模型加载体积要控制（**不要拖几百 MB**，用户跑不起来）
- 模型选择优先看 WebGPU 后端支持度（不是所有算子都 WebGPU 实现了——比如某些自定义 attention）
- 必须有"端侧失败"的降级路径（路由回 LLM）
- 量化效果必须**真实测量**，不能拍脑袋说"降低 50% 调用"

---

### M5：v2 渲染层技术决策文档（ADR）

**当前壁垒**：方案文档锁了 Phaser，没有 v2 升级理由

**目标壁垒**：补一份 ADR（Architecture Decision Record）级别的决策文档

#### 为什么重要

这份文档本身就是简历材料。当前你的 v1 文档（25+ 篇）已经很丰富，但缺一份"为什么 v2 不再只用 Phaser"的解释。**架构决策能力是大厂 mid+ 的硬要求**，校招生有这个能力 = 明显加分。

#### 文档内容
- v1 为什么选 Phaser（已有内容、引用现有文档）
- v2 性能问题暴露（M1 的压测数据）
- WebGPU 引入的决策（评估过的方案：纯 PixiJS 升级 / 重写 / 共存 / Three.js）
- Phaser + WebGPU 共存设计（分层 canvas、事件路由——见第五章）
- 演进风险与回退方案

#### 面试讲法
直接拿这份文档面试讲，是"架构师思维"的硬证据。讲法模板：
> "v1 选 Phaser 的理由是 X；v2 因 [具体压测数据] 暴露 Y 瓶颈；评估了 ABC 方案，最终选 D 因为 E；分层共存的代价是 F，但保住了 G。"

---

### M6 / M7（加分项简述）

#### M6 昼夜/天气 shader
- 把世界时间映射到光照 shader，整体色温 + 阴影方向变化
- 属于产品打磨，能让 demo 演示分钟级别"哇"
- 壁垒：L2（fragment shader 应用）

#### M7 小地图 + GPU 实例化
- 缩略图视图展示所有 200 agent 位置
- 必须 instancing 才不卡
- 壁垒：M2 做完后顺手能做
- 工程闭环价值：让"200 agent"这件事在 UI 上可视化

---

## 五、WebGPU 全量自有渲染层架构

**核心决策**：Phaser 完全移除，WebGPU 接管整个 2D 渲染管线。

### 架构

```
┌─ WebGPU canvas（唯一渲染层）─────────────────────────────┐
│  • Tilemap 渲染（Tiled `.tmj` → GPU texture array）      │
│  • Agent 实例化渲染 + sprite 动画                         │
│  • 文字渲染（SDF 字体图集，对话气泡 + UI 文字）           │
│  • GPU compute（perception / fog-of-war）                │
│  • 摄像机 / viewport                                     │
│  • 输入命中测试（坐标系变换）                             │
│  • 昼夜/天气全屏 shader（M6）                            │
└─────────────────────────────────────────────────────────┘
┌─ React DOM（z-index: 10）────────────────────────────────┐
│  • 面板、表单、调试工具、观测平台                         │
└─────────────────────────────────────────────────────────┘
```

### 数据/事件桥接

- **状态来源**：WebSocket → `worldStore` 单一来源（v1 约束完全保留）
- **分发**：`worldStore` 订阅由 WebGPU 渲染器 + React 同时消费
- **鼠标事件路由**：透明 DOM 层捕获事件 → 按目标分发：
  - 命中 NPC / tile / world 元素 → 路由给 WebGPU 渲染器的命中测试
  - 命中 UI 元素 → React 处理
- **Camera 状态**：viewport 矩阵存 store，WebGPU 渲染器订阅

### 为什么彻底移除而不是共存

| 候选方案 | 评价 |
|---|---|
| **A：彻底移除 Phaser（采用）** | 极致优化 + 100% 自有渲染层 = 图形专长信号最强形式 |
| B：Phaser + WebGPU 分层共存（v2-draft-1 旧方案） | 半自有：tilemap/文字仍是 Phaser，"100% 自有"简历叙事不成立 |
| C：留 Phaser 做 tilemap + 文字，WebGPU 做 agent + compute | 比 B 更弱：核心难点（文字渲染）被 Phaser 接走，正是 M2 最值钱的子模块 |
| D：等 Phaser 上 WebGPU 后端 | 失去控制 GPU 的机会，依旧是 Phaser 用户而非图形工程师 |
| E：用 PixiJS 替换 Phaser | 还是高层封装，不证明图形深度 |

### 取舍（必须接受）

| 优点 | 缺点 |
|---|---|
| 100% 自有渲染层，每个优化都是你的可 claim 资本 | 渲染层工作量约翻倍，时间预算 4–6 月 → 6–9 月 |
| 单一 canvas，无需双层同步 | 没有回退安全网（任何子模块卡住会影响前端可用性） |
| 简历/面试叙事最干净："整个 2D 渲染管线我自己写的" | 文字渲染（SDF）是独立硬题 |
| 极致优化的舞台完整 | Phaser 的 Tiled 集成 / scene 管理 / tween 等便利能力都要重做 |

---

## 六、风险与缓解

| 风险 | 严重度 | 缓解 |
|---|---|---|
| WebGPU 学习曲线 + 全量渲染层 9 月做不完 | **高** | 第 1 周 hello triangle + 第 2 周 instancing demo + 第 3 周文字 SDF 试探——按这三个 milestone 验证上手速度，再决定是否要缩 scope |
| **文字渲染（SDF + 中文）是独立硬题** | **高** | 单独立 milestone，预算 1 月；先做 ASCII，再扩中文常用字 1k，再做动态 glyph 缓存。可参考 troika-three-text / msdfgen |
| NPC scale up 真瓶颈在后端决策吞吐而非渲染 | 中 | M1 阶段先做完整压测；按需扩 worker 副本 / concurrency / 引入分层认知（hero NPC 全 LLM + background 规则）|
| 端侧模型选错（WebGPU 后端不支持某算子） | 中 | M4 启动时先做模型可跑性验证（不要先调架构再发现跑不动） |
| **没有 Phaser 兜底 = 没有回退路径** | **高** | 各 WebGPU 子模块独立 feature flag，可独立开关；保留 v1 分支作为最坏情况回退 |
| 6–9 月做不完 A 级 | 中 | 砍法：M6/M7 → M2 tween/scene 简化 → M3 简化为静态展示 → M4 仅 demo 不路由 |
| Agent 系统演进停滞 6+ 月 | 中 | 每月留 20% 时间继续推 Agent 阶段；v2 核心叙事是"既扩规模又扩能力" |
| Safari WebGPU 兼容性 | 低 | 展示用 Chrome 即可，README 注明 |
| 模型加载体积影响首屏 | 低 | 模型 < 30 MB，延迟加载 |

---

## 七、时间规划（6–9 个月）

| 阶段 | 月份 | 主要交付 |
|---|---|---|
| **P1 准备 + WebGPU 上手** | 第 1 月 | hello triangle → instancing demo → 文字 SDF 试探；M1 压测基线 + 后端决策吞吐扩容评估 |
| **P2 WebGPU 渲染基础** | 第 2–3 月 | M2 子模块：WebGPU 管线 + agent 实例化 + tilemap 渲染；NPC scale 推到 100 |
| **P3 文字渲染攻坚** | 第 4 月 | M2 子模块：SDF 文字（ASCII → 中文）；这一月单独留给这一个子模块 |
| **P4 输入/摄像机/动画 + GPU 计算** | 第 5 月 | M2 收尾（输入、camera、tween）+ M3 perception compute 可视化 |
| **P5 端侧推理 + scale 推顶** | 第 6 月 | M4 端侧模型集成 + 路由；scale 推到目标值 |
| **P6 决策文档 + 收尾** | 第 7 月 | M5 ADR 文档；性能调优；E2E 演示 |
| **P7–P8 加分 + 缓冲** | 第 8–9 月 | M6/M7 加分项；技术博客；README 重写 |

每阶段入口/出口标准在执行时单独细化。

---

## 八、验收标准

### 硬验收（功能 + 性能）
- [ ] 真跑目标规模（100–200，取决于 scale 决策），FPS ≥ 30（理想 60）
- [ ] **WebGPU 全量渲染管线完整覆盖**：tilemap + agent + 文字 + 输入 + 摄像机，无任何渲染走 Phaser
- [ ] 文字渲染（SDF）支持中文常用字集，对话气泡 + UI 文字流畅
- [ ] perception 可视化能在选中 agent 时秒级响应
- [ ] 端侧模型实测截走至少 20% 玩家侧任务（注意：不缓解后端 NPC 决策瓶颈，见变更说明 §修正 4），端侧推理延迟 ≤ 200ms
- [ ] WebGPU 各子模块独立 feature flag 可开关（用于 A/B 对比、回退）
- [ ] M5 决策文档完成并归档
- [ ] 性能对比数据（v1 vs v2）写进 README

### 软验收（叙事 + 面试准备）
- [ ] README 第一段能用 3 句话讲清 v1 → v2 演进动机
- [ ] 能在 30 分钟内讲完整个 v2 技术栈不卡壳
- [ ] 能回答"为什么不用 PixiJS / Three.js / 完全重写"
- [ ] 能展示 perception compute shader 代码并讲清 workgroup 设计
- [ ] 能讲清端侧模型选型理由与失败降级路径
- [ ] 配套技术博客至少 1 篇（候选主题见决策记录文档）

---

## 九、最终壁垒地图（v2 完成后）

| 层 | v1 | v2 目标 | 提升幅度 |
|---|---|---|---|
| AI Agent 系统 | L3–L4 | L3–L4 | 维持（继续推阶段任务）|
| 后端基础设施 | L3–L4 | L3–L4 | 维持 |
| 前端业务 | L2–L3 | L2–L3 | 维持 |
| **图形渲染** | L1–L2 | **L4（100% 自有渲染管线）** | **+3 级** |
| **GPU compute** | 无 | **L3** | **从无到有** |
| **端云推理分工** | L1 | **L3（前端侧）** | **+2 级** |
| 架构决策能力 | 隐性 | 显性（M5 ADR） | 从无到有 |

**结论**：v2 完成后，项目同时具备 5 个 L3+ 维度（AI Agent / 后端基础设施 / 图形渲染 / GPU compute / 端云推理），且**图形渲染达到"100% 自有 2D 渲染管线"——这是图形专长能给出的最强形式信号**，在 2026 校招生项目里极稀缺。

---

## 十、本规划的置信度声明

| 项 | 置信度 |
|---|---|
| 三个 A 级 WebGPU 模块产品上是合理的（非 bolt-on） | 90% |
| **6–9 月吃完 A 级模块（含 Phaser 完全移除）** | **45%**（WebGPU 起点 + 文字 SDF 是最大变数；6 月很紧、9 月有把握） |
| **WebGPU 全量自有渲染层（无 Phaser 兜底）可行** | 70%（最大风险点：文字渲染 SDF） |
| 完成后能给腾讯前端校招提供完整深度信号 | 90%（100% 自有渲染层强化了信号） |
| M4 端侧模型实测截走 20% 玩家侧任务 | 60%（取决于任务定义与模型准确率） |
| M3 GPU compute 加速比 ≥ 10× | 70%（取决于规模和 workgroup 设计） |

---

## 附录：本版（v2-draft-2）相对 v2-draft-1 的关键变化

| 项 | v2-draft-1 | v2-draft-2（本版） |
|---|---|---|
| Phaser | 保留做 tilemap + UI，与 WebGPU 分层共存 | **完全移除** |
| WebGPU 渲染范围 | agent 实例化层 | **整个 2D 渲染管线**（tilemap/agent/文字/输入/camera） |
| M2 工作量 | 2 月 | **4 月** |
| 总时间 | 4–6 月 | **6–9 月** |
| 图形专长信号 | "我自己写了 agent 渲染层" | "我自己写了整个 2D 渲染管线" |
| 回退能力 | Phaser 兜底 | 各子模块 feature flag 独立开关；最坏回到 v1 分支 |
| 后端 | 不变（Python FastAPI） | 不变（Python FastAPI；Node 引入暂不考虑） |

更详细的 v1→v2 选型 diff 见 `AI小镇v2架构与技术选型变更说明.md`。
