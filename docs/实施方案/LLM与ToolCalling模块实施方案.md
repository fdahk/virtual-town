# LLM 与 Tool Calling 模块实施方案

> 模块定位：统一管理大模型调用、Embedding、结构化输出、tool calling、权限校验和失败兜底。

---

## 1. 设计目标

所有 AI 能力必须通过统一 LLM 模块进入系统，不能在各业务模块里散落调用模型。

本模块负责：

- 统一接入通义千问、DeepSeek、OpenAI 等 OpenAI 兼容接口。
- 统一管理 Chat、Embedding、JSON Schema 输出。
- 定义 tool calling 契约，让 LLM 只能选择合法工具。
- 校验工具参数、权限和世界状态。
- 处理超时、重试、降级和兜底。

---

## 2. 工具选型

| 能力 | 工具 | 选择理由 |
|------|------|----------|
| LLM SDK | OpenAI Python SDK / 兼容客户端 | 通义千问、DeepSeek、OpenAI 都可适配 |
| Schema 校验 | Pydantic v2 | FastAPI 原生支持，错误信息清晰 |
| Prompt 模板 | Jinja2 | 易维护，支持变量注入 |
| 异步任务 | Celery 或 RQ + Redis | LLM 调用耗时，不应阻塞世界 tick |
| 配置 | `.env` + pydantic-settings | 多环境模型配置清晰 |

---

## 3. 配置模型

```env
LLM_PROVIDER=dashscope
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_API_KEY=your_key
LLM_CHAT_MODEL=qwen-plus
LLM_REASONING_MODEL=qwen-max
LLM_EMBEDDING_MODEL=text-embedding-v2
LLM_TIMEOUT_SECONDS=15
LLM_MAX_RETRIES=2
```

---

## 4. 核心服务接口

```python
class LLMService:
    async def chat_text(self, messages: list[dict], model: str | None = None) -> str:
        ...

    async def chat_json(
        self,
        messages: list[dict],
        response_model: type[BaseModel],
        model: str | None = None,
    ) -> BaseModel:
        ...

    async def embed(self, text: str) -> list[float]:
        ...
```

`chat_json` 必须执行：

1. 注入 JSON 输出要求。
2. 调用模型。
3. 解析 JSON。
4. Pydantic 校验。
5. 失败重试。
6. 失败后返回业务定义的 fallback。

---

## 5. Tool Calling 模型

### 5.1 ToolDefinition

```json
{
  "name": "move_to_location",
  "description": "让 Agent 移动到指定地点",
  "owner_module": "world",
  "permission": "agent.action.move",
  "parameters_schema": {
    "type": "object",
    "properties": {
      "location_id": { "type": "string" },
      "reason": { "type": "string" }
    },
    "required": ["location_id", "reason"]
  }
}
```

### 5.2 ToolCall

```json
{
  "tool": "move_to_location",
  "arguments": {
    "location_id": "loc_hobbs_cafe",
    "reason": "准备上班"
  },
  "confidence": 0.82
}
```

### 5.3 ToolResult

```json
{
  "tool": "move_to_location",
  "success": true,
  "result": {
    "action_id": "action_001",
    "path": [{ "x": 12, "y": 18 }]
  },
  "error": null
}
```

---

## 6. 工具执行流程

```text
LLM 输出 tool_calls
  ↓
ToolRegistry 查找工具
  ↓
Pydantic 校验参数
  ↓
权限校验：Agent 是否允许执行
  ↓
世界状态校验：目标是否存在、是否可达、距离是否合理
  ↓
调用业务模块
  ↓
返回 ToolResult
  ↓
写入事件和记忆
```

---

## 7. 初始工具列表

| 工具 | 所属模块 | 说明 |
|------|----------|------|
| `move_to_location` | world | 移动到地点 |
| `move_to_entity` | world | 移动到某个实体附近 |
| `interact_with_object` | world | 使用物品/设施 |
| `talk_to_entity` | dialogue | 发起对话 |
| `wait` | simulation | 等待 |
| `update_emotion` | agent | 更新情绪 |
| `write_memory` | memory | 写入记忆 |
| `search_memory` | memory | 检索记忆 |
| `react_to_pet` | animal | 动物被抚摸后的反应 |
| `avoid_danger` | world | 躲避危险区域 |

---

## 8. 失败兜底

| 失败场景 | 处理 |
|----------|------|
| LLM 超时 | 返回规则行为，如等待、继续当前行动 |
| JSON 解析失败 | 自动重试，仍失败则 fallback |
| 工具不存在 | 拒绝执行并记录错误 |
| 参数非法 | 要求模型重试或使用默认工具 |
| 目标不可达 | 返回 blocked，触发重新规划 |
| Embedding 失败 | 先写结构化记忆，稍后补 embedding |

---

## 9. 验收标准

1. 所有 LLM 调用都经过 `LLMService`。
2. 所有工具都注册在 `ToolRegistry`。
3. LLM 输出不合规不会导致仿真崩溃。
4. 至少支持通义千问 chat 和 embedding。
5. 每个工具都有参数 schema、权限校验和失败返回。
