# Prompt 工程与模型评测方案

> 模块定位：管理 prompt 模板、模型配置、结构化输出样例、评测用例和模型切换风险。

---

## 1. 设计目标

AI 小镇大量能力依赖 LLM。如果 prompt 散落在代码中，后续会出现：

- 难以调试。
- 不同模型输出格式不一致。
- tool calling schema 和 prompt 不同步。
- 模型升级后行为漂移。

因此必须建立 prompt 和模型评测规范。

---

## 2. Prompt 文件组织

```text
backend/app/prompts/
├── agent_decision/
│   ├── v1.jinja2
│   └── examples.json
├── dialogue_reply/
│   ├── v1.jinja2
│   └── examples.json
├── query_rewrite/
│   ├── v1.jinja2
│   └── examples.json
├── memory_reflection/
│   ├── v1.jinja2
│   └── examples.json
└── daily_plan/
    ├── v1.jinja2
    └── examples.json
```

---

## 3. Prompt 元数据

每个 prompt 需要元数据：

```json
{
  "id": "agent_decision",
  "version": "v1",
  "expected_schema": "AgentDecisionOutput",
  "supported_models": ["qwen-plus", "qwen-max"],
  "last_updated": "2026-04-30"
}
```

---

## 4. 输出样例

每个 prompt 必须维护合法输出样例和异常输出样例。

```json
{
  "case_name": "xiaofang_go_to_work",
  "input": {
    "agent": "小芳",
    "time": "08:00",
    "context": "小芳今天需要去咖啡店上班"
  },
  "expected_tools": ["move_to_location"],
  "expected_schema_valid": true
}
```

---

## 5. 模型配置

模型不能写死在业务代码中：

```env
LLM_CHAT_MODEL=qwen-plus
LLM_REASONING_MODEL=qwen-max
LLM_EMBEDDING_MODEL=text-embedding-v2
```

不同任务可以配置不同模型：

| 任务 | 默认模型 |
|------|----------|
| 普通对话 | qwen-plus |
| Agent 决策 | qwen-plus |
| 日总结/反思 | qwen-max |
| Query 改写 | qwen-plus |
| Embedding | text-embedding-v2 |

---

## 6. 评测维度

| 维度 | 说明 |
|------|------|
| Schema 合法率 | 输出是否能通过 Pydantic |
| 工具选择正确率 | 是否选择合理工具 |
| 角色一致性 | 是否符合 NPC 性格 |
| 记忆利用率 | 是否使用相关记忆 |
| 幻觉率 | 是否编造不存在事实 |
| 兜底触发率 | 是否频繁失败 |

---

## 7. 最小评测集

MVP 至少准备：

1. 小芳去咖啡店上班。
2. 小王点美式咖啡。
3. 玩家问“小王喜欢喝什么”。
4. 玩家说“他昨天来了吗”。
5. 狗被熟人抚摸。
6. 猫被陌生人靠近。
7. NPC 靠近河流危险区。
8. LLM 输出非 JSON 的异常样例。

---

## 8. 验收标准

1. Prompt 不散落在业务代码。
2. 每个 prompt 有版本和输出 schema。
3. 每个结构化输出有 Pydantic 校验。
4. 模型切换只改配置。
5. 最小评测集能自动运行。
