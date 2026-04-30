import { expect, test } from "@playwright/test";
import { api, ensurePlayerCreated, findAgentByName, openTownPage, teleportPlayerNear } from "./helpers";

/**
 * 演示场景 3：NPC 记忆对话
 *
 * 验证 PRD §10 场景 3：
 * - 走近小芳
 * - 问"小王喜欢喝什么？"
 * - 小芳回复中应能体现记忆中的"美式咖啡"线索
 *   （对话由 LLM 或规则兜底产生；规则兜底直接引用记忆描述里的关键词）
 * - 同时小芳应新增 chat 类型记忆
 */
test("scene 3: ask Xiaofang about Xiaowang's drink, memory recall", async ({ request }) => {
  const a = await api(request);
  const me = await ensurePlayerCreated(request);
  const agents = await a.listAgents();
  const xiaofang = findAgentByName(agents, "小芳");

  // 走近小芳，确保在交互半径内
  const xfState = await a.getAgentState(xiaofang.id);
  const stand = { x: Math.max(0, xfState.position.x - 1), y: xfState.position.y };
  await teleportPlayerNear(request, stand);

  const beforeMems = await a.listMemories(xiaofang.id);

  const reply = await a.talkPlayer(xiaofang.id, "小王喜欢喝什么？");
  expect(reply.ok, `talk failed: ${reply.status} ${JSON.stringify(reply.body)}`).toBeTruthy();
  const text: string = reply.body.reply;
  expect(text, "reply should mention coffee-related keyword").toMatch(/美式|咖啡|不加糖|清爽/);

  // 引用记忆应非空
  expect(reply.body.citations.length, "should cite at least one memory").toBeGreaterThan(0);

  // 小芳记忆应至少新增一条 chat 类型
  const afterMems: Array<{ id: string; memory_type: string }> = await a.listMemories(
    xiaofang.id,
  );
  const beforeIds = new Set(
    (beforeMems as Array<{ id: string }>).map((b) => b.id),
  );
  const newChatMems = afterMems.filter(
    (m) => m.memory_type === "chat" && !beforeIds.has(m.id),
  );
  expect(newChatMems.length).toBeGreaterThanOrEqual(1);
});
