# Stanford Generative Agents 原版项目架构分析

> 原项目路径：`/Users/mac/virtual-town/generative_agents`  
> 论文：*Generative Agents: Interactive Simulacra of Human Behavior*  
> 分析日期：2026-04-29

---

## 目录

1. [项目概览](#1-项目概览)
2. [目录结构](#2-目录结构)
3. [整体架构](#3-整体架构)
4. [仿真主循环](#4-仿真主循环)
5. [Agent 认知管线](#5-agent-认知管线)
6. [记忆系统](#6-记忆系统)
7. [行动规划](#7-行动规划)
8. [执行与移动](#8-执行与移动)
9. [世界地图与环境](#9-世界地图与环境)
10. [前端（Django + Phaser）](#10-前端django--phaser)
11. [LLM 提示策略](#11-llm-提示策略)
12. [数据存储结构](#12-数据存储结构)
13. [关键设计模式](#13-关键设计模式)
14. [技术栈总结](#14-技术栈总结)
15. [对本项目的参考价值](#15-对本项目的参考价值)

---

## 1. 项目概览

斯坦福 Generative Agents 项目（又称 **Reverie**）实现了一个 2D 小镇模拟器，其中多个 AI NPC（"生成式智能体"）具备人类行为的拟真性：自主规划每日行程、形成记忆、相互交谈、做出反应。小镇名为 **Smallville / the Ville**，地图大小 140×100 格，每格 32px。

**核心创新**：
- **记忆流（Memory Stream）**：以语义节点存储所有经历，含重要度分（poignancy）和嵌入向量
- **检索机制**：基于时近性 + 重要度 + 相关性的加权评分
- **反思（Reflection）**：当积累重要度超过阈值时，GPT 自动生成高阶洞见
- **层次化规划**：每日计划 → 小时计划 → 任务分解 → 具体行动地点

---

## 2. 目录结构

```
generative_agents/
├── README.md
├── requirements.txt                  # Django 2.2, openai 0.27, selenium, numpy
├── cover.png
│
├── reverie/                          # 仿真后端（Python）
│   ├── global_methods.py             # IO/文件工具
│   ├── compress_sim_storage.py       # 压缩历史仿真用于 Demo 回放
│   └── backend_server/
│       ├── reverie.py                # ★ 主仿真服务器 ReverieServer
│       ├── maze.py                   # ★ 世界地图 Maze 类 
│       ├── path_finder.py            # 网格寻路（BFS 波动传播）
│       ├── global_methods.py
│       ├── utils.py                  # （需自建）OpenAI API Key、路径配置
│       └── persona/
│           ├── persona.py            # ★ Agent 主类 Persona
│           ├── cognitive_modules/  -- 如何探索世界并获取数据
│           │   ├── perceive.py       # 感知模块
│           │   ├── retrieve.py       # 记忆检索
│           │   ├── plan.py           # 行动规划
│           │   ├── execute.py        # 执行/移动
│           │   ├── reflect.py        # 反思模块
│           │   └── converse.py       # 对话生成
│           ├── memory_structures/  -- 每个npc在探索过程中获取的信息，做拟人
│           │   ├── associative_memory.py  # ★ 记忆流（ConceptNode）
│           │   ├── spatial_memory.py      # 空间记忆树
│           │   └── scratch.py             # 短期工作记忆（当前状态）
│           └── prompt_template/ -- 提示词拼接模块
│               ├── run_gpt_prompt.py      # ★ 所有 LLM 调用入口
│               ├── gpt_structure.py       # Prompt 组装工具
│               ├── v3_ChatGPT/*.txt       # 当前使用的 Prompt 模板文件
│               └── v2/*.txt               # 历史模板
│
└── environment/                      # Django 前端服务器
    └── frontend_server/
        ├── manage.py
        ├── frontend_server/
        │   └── urls.py               # 路由配置
        ├── translator/
        │   └── views.py              # ★ 前后端桥接视图
        ├── templates/                # HTML 模板（Phaser 地图渲染）
        ├── static_dirs/assets/the_ville/
        │   └── matrix/               # ★ 地图数据（CSV + JSON）
        ├── storage/                  # 运行时仿真存档
        ├── compressed_storage/       # Demo 用压缩存档
        └── temp_storage/             # 当前步骤状态文件
```

---

## 3. 整体架构

项目分为**三层**，通过**文件系统**作为消息总线耦合：

```
┌─────────────────────────────────────────────────────┐
│              浏览器（Phaser.js）                      │
│  渲染地图、NPC、移动动画；每步轮询后端状态               │
└────────────────────┬────────────────────────────────┘
                     │ HTTP（AJAX POST/GET）
┌────────────────────▼────────────────────────────────┐
│        Django 前端服务器（environment/）              │
│  process_environment → 写 environment/{step}.json    │
│  update_environment  → 读 movement/{step}.json       │
└────────────────────┬────────────────────────────────┘
                     │ 共享文件系统（storage/）
┌────────────────────▼────────────────────────────────┐
│        Reverie 仿真后端（reverie/）                   │
│  读 environment/{step}.json → 执行 Agent 认知管线     │
│  写 movement/{step}.json   → 包含每个 NPC 的移动指令  │
└─────────────────────────────────────────────────────┘
                     │ OpenAI API
┌────────────────────▼────────────────────────────────┐
│        LLM 层（OpenAI GPT / Embeddings）             │
│  提供推理、计划生成、重要度打分、对话生成等能力           │
└─────────────────────────────────────────────────────┘
```

**核心耦合机制**：前后端之间**没有 HTTP 直连**，完全通过磁盘文件按步骤编号同步。每个仿真步骤（默认对应游戏内 10 秒）生成一对 `environment/N.json`（NPC 位置输入）和 `movement/N.json`（NPC 动作输出）。

---

## 4. 仿真主循环

**文件**：`reverie/backend_server/reverie.py` — `ReverieServer`

### 初始化流程

```
1. 从 fork 基础仿真 → 复制到新 sim_code 目录
2. 读取 reverie/meta.json（时钟、地图名、NPC 名单）
3. 构建 Maze（从 CSV 矩阵文件）
4. 加载每个 Persona（从 personas/<name>/ 目录）
5. 写入 temp_storage/curr_sim_code.json 和 curr_step.json
   → Django 首页据此知道当前仿真是哪个
```

### 主循环（`start_server`）

```python
while True:
    # 1. 等待前端 POST environment/{step}.json
    wait_until_file_exists(f"environment/{step}.json")

    # 2. 清理上一步的对象事件
    game_obj_cleanup()

    # 3. 同步 NPC 在 Maze 中的位置
    for persona in personas:
        maze.update_persona_tile(persona, new_xy)
        maze.update_tile_events(...)

    # 4. 每个 NPC 执行完整认知管线
    for persona in personas:
        next_tile, pronunciatio, description = persona.move(
            maze, personas, curr_tile, curr_time
        )

    # 5. 写出 movement/{step}.json
    write_movement_file(step, all_persona_moves)

    # 6. 推进时钟
    step += 1
    curr_time += timedelta(seconds=sec_per_step)  # 默认 10 秒
```

---

## 5. Agent 认知管线

**文件**：`reverie/backend_server/persona/persona.py`

每个 NPC 每步执行固定顺序的认知管线：

```
感知 perceive(maze)
    ↓
检索 retrieve(perceived_events)
    ↓
规划 plan(maze, personas, new_day, retrieved)
    ↓
反思 reflect()
    ↓
执行 execute(maze, personas, plan)
    ↓
返回：(next_tile, pronunciatio表情符号, action_description)
```

| 模块 | 文件 | 职责 |
|------|------|------|
| perceive | `cognitive_modules/perceive.py` | 视野范围内发现事件，写入记忆流 |
| retrieve | `cognitive_modules/retrieve.py` | 按关键词+向量评分检索相关记忆 |
| plan | `cognitive_modules/plan.py` | 决定当天计划 / 当前行动 / 社交反应 |
| reflect | `cognitive_modules/reflect.py` | 高层反思，生成洞见节点 |
| execute | `cognitive_modules/execute.py` | 将行动地址解析为下一步格子 |
| converse | `cognitive_modules/converse.py` | 两 NPC 之间的迭代对话生成 |

---

## 6. 记忆系统

### 6.1 记忆流（Associative Memory）

**文件**：`memory_structures/associative_memory.py`

核心数据结构 `ConceptNode`：

| 字段 | 说明 |
|------|------|
| `node_type` | `"event"` / `"thought"` / `"chat"` |
| `subject/predicate/object` | 三元组（如 `("Isabella", "is", "eating breakfast")`） |
| `description` | 自然语言描述 |
| `embedding_key` | 用于余弦相似度检索的嵌入键 |
| `poignancy` | 重要度分（1-10，由 GPT 打分） |
| `keywords` | 关键词集合（用于快速索引） |
| `last_accessed` | 最近访问时间（用于时近性衰减） |
| `filling` | 证据节点 ID 列表（思想/对话节点用） |

存储文件：
- `bootstrap_memory/associative_memory/nodes.json`
- `bootstrap_memory/associative_memory/embeddings.json`
- `bootstrap_memory/associative_memory/kw_strength.json`

### 6.2 空间记忆（Spatial Memory）

**文件**：`memory_structures/spatial_memory.py`

`MemoryTree`：嵌套字典，表示 NPC 已知的地理信息：

```
world
  └── sector（区域，如 "the Ville"）
        └── arena（房间，如 "kitchen"）
              └── [game objects]（如 "coffee maker"）
```

在感知模块中增量更新，供规划时枚举"我能去哪里做什么"。

### 6.3 工作记忆（Scratch）

**文件**：`memory_structures/scratch.py`

存储 NPC 当前状态和规划参数：

- 感知参数：`vision_r`（视野半径）、`att_bandwidth`（注意力带宽）、`retention`（短期保留数量）
- 当前行动：`act_address`、`act_start_time`、`act_duration`、`act_description`、`act_pronunciatio`（emoji）
- 路径：`planned_path`（格子坐标列表）
- 日程：`f_daily_schedule`、`f_daily_schedule_hourly_org`
- 对话缓冲：`chatting_with`、`chat`、`chatting_end_time`

### 6.4 记忆检索评分（`new_retrieve`）

```python
score = (
    recency_weight   * recency_score    +   # 指数衰减：位置越近访问时间越近
    importance_weight * importance_score +   # poignancy 归一化
    relevance_weight  * relevance_score      # 与焦点事件的余弦相似度
)
# 默认权重 gw = [0.5, 3, 2]
```

### 6.5 反思触发机制

- 每次感知到新事件后，将其 poignancy 从 `importance_trigger_curr` 中扣除
- 当 `importance_trigger_curr <= 0` 时触发反思
- 反思流程：GPT 生成焦点话题 → 检索相关记忆 → GPT 生成洞见 → 存为 thought 节点

---

## 7. 行动规划

**文件**：`cognitive_modules/plan.py`

### 7.1 长期规划（每天一次）

```
generate_wake_up_hour()          → 起床时间
generate_first_daily_plan()      → 第一天：生成今日需求列表 daily_req
revise_identity()                → 之后的天：检索记忆，GPT 更新 currently/daily_plan_req
generate_hourly_schedule()       → 生成 24 小时日程（含 sleep 前缀补全）
→ 存入 scratch.f_daily_schedule
```

### 7.2 短期规划（行动完成时触发）

```
act_check_finished()             → 检查当前行动是否到时间
generate_task_decomp()           → 将多小时块分解为细粒度任务
generate_action_sector()         → 选择目标区域（sector）
generate_action_arena()          → 选择目标房间（arena）
generate_action_game_object()    → 选择目标物品
generate_action_pronunciatio()   → 生成表情符号
generate_action_event_triple()   → 生成行动三元组（主谓宾）
add_new_action()                 → 更新 scratch，重置路径规划
```

### 7.3 社交反应

```
_choose_retrieved()              → 优先选择其他 NPC 的事件
_should_react()                  → generate_decide_to_talk() / generate_decide_to_react()
                                   返回 "chat with X" / "wait: <timestamp>" / False

若决定交谈:
  _chat_react()                  → agent_chat_v2()（多轮迭代对话）
                                 → generate_new_decomp_schedule()（更新日程）

若决定等待:
  _wait_react()                  → 在当前格停留到指定时间
```

---

## 8. 执行与移动

**文件**：`cognitive_modules/execute.py`

将 `act_address`（字符串地址）解析为下一步格子：

| act_address 类型 | 处理方式 |
|-----------------|---------|
| `"<persona> Name"` | 向目标 NPC 当前位置寻路（中途格子用于接近） |
| `"<waiting> x y"` | 原地不动 |
| `"<random>"` | 在父级地址范围内随机选格 |
| 普通地址字符串 | 通过 `maze.address_tiles[plan]` 查找目标格集合 |

**寻路**（`path_finder.py`）：
- 输入：碰撞矩阵（从 Tiled 地图导出的 CSV）
- 算法：BFS 波动传播（`path_finder_v2`）
- 特殊处理：坐标系转换补丁（backend (x,y) → 内部 row/col）

每步只移动**一格**，`planned_path` 列表逐步弹出。

---

## 9. 世界地图与环境

**文件**：`reverie/backend_server/maze.py`

**文件**：`environment/frontend_server/static_dirs/assets/the_ville/matrix/`

### 地图数据文件

| 文件 | 内容 |
|------|------|
| `maze_meta_info.json` | 地图元数据（宽 140、高 100、tile 32px） |
| `matrix/maze/*.csv` | 碰撞层、世界/区域/房间/对象层 |
| `matrix/special_blocks/*.csv` | 特殊标记（如出生点） |

### Maze 类核心结构

```python
tiles[row][col] = {
    "world": "the Ville",
    "sector": "Isabella's apartment",
    "arena": "kitchen",
    "game_object": "coffee maker",
    "spawning_location": "sp-X",
    "collision": True/False,
    "events": set()  # 该格子当前的事件三元组集合
}

address_tiles = {
    "the Ville:Isabella's apartment:kitchen:coffee maker": {(x1,y1), (x2,y2), ...}
}
```

**事件三元组格式**：`(object_full_name, predicate, object_state, description)`

---

## 10. 前端（Django + Phaser）

**文件**：`environment/frontend_server/`

### 10.0 技术组合说明：Django + Phaser vs React

这两个词分别来自两个不同维度，和你熟悉的 React 有本质区别，下面逐一解释。

#### Django 是什么角色？

Django 是 **Python Web 后端框架**，在原版项目里扮演的是"胶水层"而非真正的前端：

- 负责**路由**和**提供 HTML 页面**（Server-Side Rendering，服务端渲染）
- 用 Python 的 Django 模板语法（`{{ variable }}`、`{% for %}`）直接在服务端把数据拼进 HTML 返回给浏览器
- 在原版里还同时充当**前后端桥接**：接收浏览器 AJAX 请求、读写 JSON 文件、把仿真结果传回浏览器

类比到你熟悉的技术栈，Django 在原版里相当于 **Express / Next.js（服务端部分）**，不是前端框架。

#### Phaser.js 是什么角色？

Phaser.js 是一个**浏览器端 2D 游戏引擎**，在原版里负责：

- 加载并渲染 **Tiled 格式的瓦片地图**（.tmj / .json + tileset 图集）
- 管理**精灵（Sprite）**：NPC 角色的行走动画帧
- 驱动**游戏循环**（Game Loop）：每帧更新角色位置、播放动画
- 处理摄像机跟随、键盘输入等游戏级交互

Phaser 不是 UI 框架，它操作的是 **Canvas / WebGL**，而不是 DOM，也没有组件化、状态管理这些概念。

#### 和 React 的核心区别

| 维度 | React | Phaser.js |
|------|-------|-----------|
| 渲染目标 | **DOM**（HTML 元素树） | **Canvas / WebGL**（像素级绘制） |
| 编程模型 | 声明式（描述 UI 状态，框架决定怎么更新 DOM） | 命令式游戏循环（每帧手动更新对象位置） |
| 擅长场景 | 数据驱动的 UI 界面、表单、列表、后台管理 | 2D/3D 游戏、实时动画、地图渲染 |
| 状态管理 | useState / Redux / Zustand 等 | 场景（Scene）内的 JavaScript 变量 |
| 组件化 | 核心能力，可复用 | 无原生组件概念，靠类继承或对象组合 |
| 布局系统 | CSS Flexbox / Grid | 手动设置 x/y 坐标 |

#### 原版选这个组合的原因

原版是**研究原型**，不是产品，所以选择了最简单能跑的方案：
- Django 已经是 Reverie 后端的语言（Python），顺手再开一个 Django 服务器省事
- Phaser.js 专门为 Tiled 地图和精灵动画优化，渲染小镇地图只需几行配置

#### 你用 React 完全可以替代

**React 完全能实现同样效果**，有两条路：

**方案 A（推荐）：React + Phaser 混合**
- React 负责 UI 层（聊天面板、NPC 信息卡、控制按钮、状态看板）
- Phaser 挂载在 React 的一个 `<div>` 容器里，负责地图和角色渲染
- 两者通过事件（EventEmitter）或 React ref 通信
- 这是目前游戏化 Web 应用的主流方案

**方案 B：纯 React（不用 Phaser）**
- 用 CSS Grid / Canvas API / react-konva / PixiJS（React 封装）渲染地图
- 每个 NPC 是一个绝对定位的 `<div>` 或 SVG，用 CSS transition 实现移动动画
- 更熟悉、调试方便，但性能上限低于 Canvas 方案
- 对于这种格子小镇（140×100 格）完全够用

**后端**方面，Django 可以直接替换成你更熟悉的任何框架（FastAPI、Express 等），核心逻辑在 Python 仿真后端，Django 只是一层很薄的桥接。

---

### URL 路由

| URL | 视图功能 |
|-----|---------|
| `/` | 落地页 |
| `/simulator_home` | 主仿真页面（读取当前 sim_code、step） |
| `/process_environment` | POST：浏览器 → 写 `environment/{step}.json` |
| `/update_environment` | POST：浏览器 ← 读 `movement/{step}.json` |
| `/demo/<sim>/` | 非交互演示回放（读 compressed_storage） |
| `/replay/<sim>/` | 完整存档回放 |
| `/replay_persona_state/<sim>/<persona>/<step>` | 调试用：查看某步骤的 NPC 完整状态 |

### 同步机制

```
浏览器每步:
1. POST /process_environment → 发送所有 NPC 当前坐标
2. 等待 Reverie 处理（Reverie 检测到文件后运行认知管线）
3. POST /update_environment → 获取 movement JSON
4. 根据 movement 数据驱动 Phaser 精灵动画
```

---

## 11. LLM 提示策略

**文件**：`persona/prompt_template/run_gpt_prompt.py`  
**模板**：`persona/prompt_template/v3_ChatGPT/*.txt`

### 提示分解原则

每个决策拆解为**独立的单目标 Prompt**，避免单次复杂推理失败：

| 提示函数 | 用途 |
|---------|------|
| `run_gpt_prompt_wake_up_hour` | 生成起床时间 |
| `run_gpt_prompt_daily_plan` | 生成今日需求列表 |
| `run_gpt_prompt_generate_hourly_schedule` | 生成逐小时日程 |
| `run_gpt_prompt_task_decomp` | 将多小时任务分解 |
| `run_gpt_prompt_action_sector/arena/object` | 分层选择行动地点 |
| `run_gpt_prompt_pronunciatio` | 生成表情符号 |
| `run_gpt_prompt_event_triple` | 生成行动三元组 |
| `run_gpt_prompt_decide_to_talk` | 决定是否与他人交谈 |
| `run_gpt_prompt_create_conversation` | 生成对话（多轮迭代） |
| `run_gpt_prompt_poignancy_event/thought` | 为记忆打重要度分 |
| `run_gpt_prompt_focal_pt` | 反思时生成焦点话题 |
| `run_gpt_prompt_insight_and_guidance` | 生成洞见和证据链 |

### 容错机制

每个 `run_gpt_prompt_*` 函数都有 **fail-safe 默认值**，当 LLM 输出格式错误时回退到合理默认。

---

## 12. 数据存储结构

### 运行时存储（`storage/<sim_code>/`）

```
storage/<sim_code>/
├── reverie/
│   └── meta.json              # 全局时钟：step, curr_time, sec_per_step
├── environment/
│   ├── 0.json                 # {"Isabella": {"x": 72, "y": 14}, ...}
│   ├── 1.json
│   └── ...
├── movement/
│   ├── 0.json                 # {"Isabella": {"movement": [73,14], "pronunciatio": "🍳", ...}}
│   └── ...
└── personas/<Agent Name>/
    └── bootstrap_memory/
        ├── scratch.json       # 工作记忆快照
        ├── spatial_memory.json
        └── associative_memory/
            ├── nodes.json
            ├── embeddings.json
            └── kw_strength.json
```

### 压缩回放存储（`compressed_storage/<sim_code>/`）

```
compressed_storage/<sim_code>/
├── master_movement.json       # 全部步骤合并（用于 Demo 一次性加载）
├── meta.json
└── personas/
    └── <Agent Name>/          # 同上结构的记忆快照
```

---

## 13. 关键设计模式

| 模式 | 位置 | 说明 |
|------|------|------|
| 文件系统作消息总线 | `environment/{step}.json` ↔ `movement/{step}.json` | 前后端解耦，无需直连 |
| 模板方法认知管线 | `Persona.move()` | 固定顺序：感知→检索→规划→反思→执行 |
| 记忆流 + 三维检索 | `AssociativeMemory` + `new_retrieve` | 时近性+重要度+相关性加权 |
| 层次化空间寻址 | `maze.address_tiles` | 世界/区域/房间/物品四级地址 → 格子坐标集 |
| Fork-on-copy 仿真 | `copyanything()` | 从基础仿真 fork 新实验，不破坏原始数据 |
| 分解式 LLM 编排 | `run_gpt_prompt.py` | 多个小型单目标 Prompt，各自有 fail-safe |
| BFS 网格寻路 | `path_finder.py` | 基于碰撞矩阵的波动传播算法 |

---

## 14. 技术栈总结

| 层次 | 技术 |
|------|------|
| 地图渲染 | Phaser.js（浏览器端，具体版本见 templates） |
| Web 框架 | Django 2.2 |
| 仿真后端 | Python 3（纯脚本，无框架） |
| LLM | OpenAI GPT（text-davinci 系列 + ChatGPT API） |
| 向量嵌入 | OpenAI text-embedding-ada-002 |
| 地图编辑 | Tiled Map Editor（CSV 导出） |
| 数据存储 | 纯 JSON 文件（无数据库） |
| 前后端通信 | 文件系统同步 + Django AJAX |

---

## 15. 对本项目的参考价值

本项目需要实现的 Web 版小镇与斯坦福原版高度相似，以下是关键参考点：

### 必须复用的核心思路

1. **记忆流架构**：ConceptNode 的三元组 + poignancy + 嵌入向量是实现 NPC 自主性的基础
2. **三维检索评分**：时近性/重要度/相关性加权是检索质量的关键
3. **层次化规划**：日计划 → 时计划 → 任务分解 → 地点选择的四层结构
4. **反思机制**：积累重要度触发高层洞见，使 NPC 行为不断演化

### 可以改进的地方

1. **文件系统消息总线 → WebSocket**：原版用文件同步前后端，延迟高；Web 版应改用 WebSocket 实时推送
2. **Django 2.2 → 现代框架**：可用 FastAPI / Django 4.x，支持异步
3. **OpenAI 直连 → 可配置模型**：支持接入国内大模型（文心、通义等）
4. **无数据库 → 关系型/文档型 DB**：JSON 文件难以扩展，建议用 PostgreSQL 或 MongoDB
5. **单进程仿真 → 异步并行**：多 NPC 的 LLM 调用可并行化，降低每步延迟
6. **纯 CSV 地图 → 可视化地图编辑器**：提供 Web 端地图编辑界面

### 地图资产

原版地图文件（CSV 矩阵 + Tiled 资源）可直接复用或作为参考，路径：
```
environment/frontend_server/static_dirs/assets/the_ville/
```
base_the_ville_isabella_maria_klaus
test-simulation