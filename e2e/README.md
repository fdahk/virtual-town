# AI 小镇 E2E 验收

覆盖 `产品需求/AI小镇产品需求文档PRD.md §10` 的 6 个演示场景。

## 前置

```bash
# 1. 起依赖 + 后端 + 前端
./scripts/dev.sh

# 2. 安装 Playwright 与浏览器（首次）
cd e2e
npm install
npx playwright install chromium
```

## 运行

```bash
cd e2e
npm test                  # 全部用例
npm test -- 03_npc_memory # 跑单个文件
npm run test:headed       # 看浏览器执行
npm run test:ui           # Playwright UI 模式
npm run report            # 查看 HTML 报告
```

环境变量：

| 变量 | 默认 | 说明 |
|------|------|------|
| `E2E_BASE_URL` | `http://localhost:5173` | 前端地址 |
| `E2E_API_URL`  | `http://localhost:8000` | 后端地址 |

## 测试矩阵

| 文件 | 对应 PRD 场景 |
|------|---------------|
| `01_enter_town.spec.ts` | 场景 1 玩家进入小镇 |
| `02_enter_building.spec.ts` | 场景 2 进入咖啡店内部 |
| `03_npc_memory_chat.spec.ts` | 场景 3 NPC 记忆对话 |
| `04_animal_interaction.spec.ts` | 场景 4 动物互动 |
| `05_hazard.spec.ts` | 场景 5 河流溺水 |
| `06_followup_memory.spec.ts` | 场景 6 反思与后续影响 |

## 注意事项

- 用例**单 worker 串行**执行，因为整套世界是单实例。
- 用例之间**不假设执行顺序**：每个用例自己负责把世界恢复到可比较状态（瞬移玩家、强制反思等）。
- 河流溺水用例为了避免引擎自动让玩家漂走，用例内会暂停仿真后再触发。
- 对话回复在没有 LLM 配置时走规则兜底：兜底回复总是引用第一条相关记忆中的关键字（"美式咖啡"），断言据此命中。
