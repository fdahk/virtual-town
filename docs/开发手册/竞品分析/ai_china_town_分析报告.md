# AI中国小镇（ai_china_town）项目分析报告

> 原项目地址：基于 [Generative Agents](https://github.com/joonspk-research/generative_agents) 思路的中文本地化实现

---

## 一、项目概览

`ai_china_town` 是一个模仿斯坦福 Generative Agents 论文思路的**轻量级中文 AI 小镇模拟器**。它让多个 NPC（小明、小芳、小王）在一张固定地图上按时间步长运行，每个 NPC 由大语言模型（LLM）驱动自主决策——包括生成日程、决定去哪、与邻近角色自然对话，并将对话压缩为长期记忆。

项目提供三种运行模式：
- **Gradio Web UI**（`src/main.py`）：推荐，通过浏览器操作
- **命令行模式**（`src/cmd_game_easy.py`）：不再维护
- **Unity 驱动模式**（`src/unity_socket_main.py`）：通过 TCP Socket 驱动 Unity 3D 场景

---

## 二、整体架构

```
ai_china_town/
├── agents/              # NPC 人物档案（纯文本，可热修改）
│   ├── 小明/1.txt
│   ├── 小芳/1.txt
│   └── 小王/1.txt
├── src/
│   ├── main.py          # Gradio 主程序（推荐入口）
│   ├── unity_socket_main.py  # Unity TCP 桥接版
│   └── cmd_game_easy.py      # 命令行版（不维护）
├── tools/
│   ├── LLM/
│   │   ├── run_gpt_prompt.py      # 所有 LLM 调用封装
│   │   ├── ollama_agent.py        # Ollama 本地推理后端（默认）
│   │   ├── deepseek_agent.py      # DeepSeek API 后端
│   │   ├── qwen_turbo_agent.py    # 通义千问 API 后端
│   │   ├── modelscope_agent.py    # ModelScope 本地 Qwen2.5-3B 后端
│   │   └── prompt_template/       # 7 个提示词模板
│   └── Database/
│       └── Pgvector_op.py         # pgvector 向量记忆（预留，未集成）
├── test/                # Socket 测试脚本
└── requirements.txt
```

---

## 三、核心模块详解

### 3.1 NPC 档案系统（agents/）

每个 NPC 对应一个 `agents/<名字>/1.txt` 文件，包含 8 行纯文本：

```
姓名：小明
年龄：18
性别：男
职业：学生
特点：腼腆，学习好，喜欢周末看电影
经历：获得很多学校的表彰，是三好学生
生活习惯：作为一个中国高中生，工作日每天坚持六点半左右起床...
个人反思：不能太晚起床容易上课迟到
```

代码中取 `ziliao[6]`（第 7 行，0-indexed）作为"生活习惯"字段，传入 LLM 提示词，作为生成日程和起床时间的主要依据。

**优点**：零代码修改人物，Gradio 界面可在线编辑保存。

---

### 3.2 地图系统（MAP）

地图是一个 8×13 的二维字符串数组，`#` 表示空地，其余字符串为可交互地点：

```
医院  咖啡店  #  蜜雪冰城  学校  ...  小芳家  ...  火锅店
...
小明家  ...  小王家
...
肯德基  乡村基  ...  健身房
电影院  ...  商场
...
海边
```

`can_go_place` 列表维护 15 个 NPC 可前往的地点。NPC 位置以 `(row, col)` 元组表示，移动通过 `goto_scene()` 直接定位，**不做路径规划**（A* 等算法未实现）。

---

### 3.3 Agent 类（`agent_v`）

每个 NPC 实例持有以下状态：

| 字段 | 说明 |
|------|------|
| `name` | NPC 名字 |
| `home` | 家庭住所（初始位置） |
| `curr_place` | 当前所在地点 |
| `position` | 地图坐标 `(row, col)` |
| `schedule` | LLM 生成的粗日程（活动+时长列表） |
| `schedule_time` | 细化后带时间戳的日程 |
| `wake` | 今日起床时间（`HH-MM` 格式） |
| `curr_action` | 当前正在执行的活动 |
| `curr_action_pronunciatio` | 活动对应的 emoji 表情 |
| `last_action` | 上一步的活动（用于判断是否需要重新决策地点） |
| `memory` | 对话摘要（长期记忆字符串） |
| `talk_arr` | 今日聊天记录（累计字符串） |
| `ziliao` | 人物档案文件内容（行列表） |

---

### 3.4 主循环（`simulate_town_simulation`）

主循环是整个模拟的核心，以"步"（step）为时间单位推进：

```
每 1 步 = min_per_step 分钟（用户可配置，默认 30 分钟）
每 1440/min_per_step 步 = 1 天
```

**每天开始时（`step % (1440/min_per_step) == 0`）：**
1. 对上一天的聊天记录调用 `summarize()` → 写入 `memory`
2. 所有 NPC 回家
3. 调用 `run_gpt_prompt_generate_hourly_schedule()` 生成粗日程（活动+时长）
4. 调用 `run_gpt_prompt_wake_up_hour()` 决定起床时间
5. 将粗日程+起床时间转换为带时间戳的精细日程（`update_schedule`）
6. 调用 `modify_schedule()` 结合记忆进一步细化日程

**普通步内：**
1. 若当前时间早于起床时间 → 状态标记为"睡觉"
2. 否则 → 用 `find_current_activity()` 查找当前时间对应日程活动
3. 若活动发生变化 → 调用 `go_map()` 决定去哪个地点，更新位置
4. 调用 `run_gpt_prompt_pronunciatio()` 将活动转换为 emoji
5. 调用 `DBSCAN_chat()` 检测附近 NPC → 随机触发对话（75% 概率）

---

### 3.5 LLM 调用层（`tools/LLM/`）

#### 后端抽象

项目支持 4 种 LLM 后端，通过注释切换：

| 后端 | 类 | 说明 |
|------|-----|------|
| **Ollama**（默认） | `OllamaAgent` | 本地 HTTP API，`qwen2.5:14b` |
| DeepSeek | `DeepSeekAgent` | OpenAI 兼容接口，需 `API_KEY` 环境变量 |
| 通义千问 | `QwenTurboAgent` | DashScope API |
| ModelScope | `ModaAgent` | 本地 Qwen2.5-3B，需 torch |

#### 安全生成机制（`ollama_safe_generate_response`）

```python
def ollama_safe_generate_response(prompt, example_output, special_instruction,
                                   repeat=3, func_validate, func_clean_up, fail_safe):
    # 拼接 JSON 格式要求到 prompt
    # 最多重试 repeat 次
    # 每次用 func_validate 验证输出格式
    # 验证通过才返回，否则返回 fail_safe 默认值
```

所有 LLM 调用都包在这个重试机制里，保证输出格式可靠。

#### 提示词模板系统

7 个 `.txt` 模板文件，用 `!<INPUT N>!` 占位符注入动态内容：

| 模板文件 | 功能 | 主要输入 |
|---------|------|---------|
| `生成日程安排时间表.txt` | 生成全天粗日程（活动+分钟数列表） | 生活习惯、今天日期 |
| `起床时间.txt` | 推断今日起床时间 | 生活习惯、日期、日程 |
| `细化每日安排时间表.txt` | 结合记忆细化日程时间戳 | 日程、记忆、起床时间、角色特征 |
| `行动需要去的地方.txt` | Few-shot 推理：该活动应去哪个地点 | 角色、当前地点、可用地点、任务 |
| `聊天.txt` | 生成两个 NPC 的对话内容 | 地点、两人记忆、聊天上下文、时间 |
| `行为转为图标显示.txt` | 将活动文字转为 emoji | 活动描述 |
| `总结经历交谈为记忆.txt` | 压缩今日聊天为记忆摘要 | 聊天记录、日期、角色名 |

`generate_prompt()` 静态方法读取模板并替换所有 `!<INPUT N>!` 占位符，`<commentblockmarker>###</commentblockmarker>` 之前的内容（变量说明）自动被裁剪掉不传给模型。

---

### 3.6 社交感知（DBSCAN 聚类）

```python
def DBSCAN_chat(agents):
    # 收集所有 NPC 的坐标
    # 用 DBSCAN(eps=1.5, min_samples=1) 聚类
    # 找出至少有 2 个 NPC 的聚类（即"相遇"）
    # 以 75% 概率随机选一对触发对话
```

用 DBSCAN 空间聚类代替简单距离判断，能自然处理多人群聚的场景。

---

### 3.7 记忆系统

当前实现为**永久字符串记忆**（无遗忘曲线）：
- `talk_arr`：当天聊天记录累积字符串
- `memory`：跨天摘要（调用 `summarize()` 生成）

`Pgvector_op.py` 预留了向量数据库接口（pgvector + PostgreSQL），但**尚未集成**到主流程。

---

### 3.8 Unity 桥接（`unity_socket_main.py`）

通过 Python TCP Socket Server 向 Unity 发送指令：

```
MOVE:id,x,y;id,x,y;...   → 移动角色到世界坐标
SPEAK:角色名,对话内容       → 触发对话气泡
UPDATE_UI:name,action,place → 更新 UI 显示
```

地图改用 `MAP_plus`（含世界坐标 + 随机扰动），DBSCAN eps 调整为 4.5 适应更大坐标范围。

---

## 四、数据流图

```
用户配置（星期、步数、分钟/步）
          │
          ▼
   simulate_town_simulation()
          │
    ┌─────┴──────────────────┐
    │  每天开始               │
    │  ┌────────────────┐    │
    │  │ NPC.ziliao[6]  │    │
    │  │ （生活习惯）     │    │
    │  └───────┬────────┘    │
    │          │ LLM          │
    │          ▼              │
    │  粗日程（活动+时长）      │
    │          │ LLM          │
    │          ▼              │
    │  起床时间（HH-MM）        │
    │          │              │
    │          ▼              │
    │  update_schedule()      │
    │  → 带时间戳日程           │
    │          │ LLM          │
    │          ▼              │
    │  modify_schedule()      │
    │  → 结合记忆细化日程        │
    └─────────────────────────┘
          │
    ┌─────┴──────────────────┐
    │  每步                  │
    │  find_current_activity() │
    │  → 当前活动             │
    │          │ LLM（变化时）│
    │          ▼              │
    │  go_map() → 目标地点    │
    │          │              │
    │  DBSCAN_chat()          │
    │  → 相遇触发对话          │
    │          │ LLM          │
    │          ▼              │
    │  double_agents_chat()   │
    │  → 对话内容 → talk_arr  │
    └─────────────────────────┘
          │
    每天结束: summarize() → memory
```

---

## 五、技术要点总结

| 技术点 | 实现方式 |
|--------|---------|
| NPC 自主决策 | LLM 生成日程，时间索引查找当前活动 |
| 地点导航 | LLM Few-shot 推理目标地点，直接跳转（无路径规划） |
| 社交感知 | DBSCAN 空间聚类检测相邻 NPC |
| 对话生成 | LLM 结合双方记忆和上下文生成对话 |
| 记忆管理 | 当天对话 → LLM 摘要 → 跨天记忆字符串 |
| 鲁棒性 | `ollama_safe_generate_response` 多次重试 + fail_safe 兜底 |
| 模型兼容 | 支持 Ollama / DeepSeek / 通义千问 / ModelScope，注释切换 |
| 前端 | Gradio Blocks，流式输出模拟结果，Tab 页在线编辑人物档案 |

---

## 六、与斯坦福 Generative Agents 的对比

| 维度 | 斯坦福原版 | ai_china_town |
|------|-----------|--------------|
| 规模 | 25 个 Agent | 3 个 Agent |
| 地图 | 完整 2D 沙盒（Phaser.js） | 二维数组 + 坐标 |
| 路径规划 | A* 算法 | 直接跳转 |
| 记忆 | 向量记忆+检索+反思 | 字符串摘要（向量预留） |
| 感知 | 视野范围内物品/人 | DBSCAN 聚类 |
| LLM | GPT-4 | 本地 Ollama/开源模型 |
| 前端 | Web 沙盒游戏 | Gradio / Unity |
| 部署 | 需 OpenAI API | 完全本地可运行 |

---

## 七、运行前提条件

1. **Python ≥ 3.10**
2. **Ollama 已安装并运行**，且拉取了 `qwen2.5:14b` 模型：
   ```bash
   ollama pull qwen2.5:14b
   ollama serve   # 默认监听 127.0.0.1:11434
   ```
3. 依赖安装：
   ```bash
   pip install -r requirements.txt
   ```
4. **运行目录须在 `src/`**（或项目根目录），因为 `run_gpt_prompt.py` 中有 `os.chdir('../')` 使相对路径指向项目根。

---

## 八、已知局限与可改进点

1. **路径规划缺失**：NPC 瞬移到目的地，无中间过渡动画/路径
2. **记忆无遗忘**：所有记忆永久保留，长时间运行后 prompt 越来越长
3. **向量记忆未集成**：`Pgvector_op.py` 已有实现但未接入主流程
4. **固定 3 个 NPC**：增加 NPC 需修改源码
5. **提示词对弱模型不够鲁棒**：3B 模型会生成时间交错的日程（README 已注明）
6. **无真实地图渲染**：Gradio 版只有文字输出，无可视化地图
7. **世界规则为空**：`world_rule = ""` 尚未利用
