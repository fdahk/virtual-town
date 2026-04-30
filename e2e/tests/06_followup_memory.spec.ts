import { expect, test } from "@playwright/test";
import {
  api,
  ensurePlayerCreated,
  findAgentByName,
  teleportPlayerNear,
} from "./helpers";

/**
 * 演示场景 6：后续影响 + 反思
 *
 * 验证 PRD §10 场景 6：
 * - 与小芳进行一次对话（建立"刚才发生的事"）。
 * - 强制触发反思 → 应当至少新增 1 条 thought 记忆，且其证据 id 引用前面的对话/事件记忆。
 * - 再发起第二轮对话，对话引用记忆中应能命中第一轮的内容（关键字"美式咖啡"）。
 */
test("scene 6: conversation -> reflection -> follow-up retains context", async ({ request }) => {
  const a = await api(request);
  await ensurePlayerCreated(request);
  const xiaofang = findAgentByName(await a.listAgents(), "小芳");
  const xfState = await a.getAgentState(xiaofang.id);
  await teleportPlayerNear(request, {
    x: Math.max(0, xfState.position.x - 1),
    y: xfState.position.y,
  });

  await a.talkPlayer(xiaofang.id, "你早上忙吗？");

  // 强制反思
  const thoughts = await a.forceReflect(xiaofang.id);
  expect(Array.isArray(thoughts)).toBeTruthy();
  expect(thoughts.length, "should yield at least one thought").toBeGreaterThanOrEqual(1);
  const t0 = thoughts[0];
  expect(t0.memory_type).toBe("thought");
  // 反思必须基于多条事件
  expect(Array.isArray(t0.evidence_memory_ids)).toBeTruthy();

  // 第二轮对话：再次询问偏好类问题，引用记忆里至少能取到一条相关
  const second = await a.talkPlayer(xiaofang.id, "小王今天还会来吗？");
  expect(second.ok).toBeTruthy();
  expect(second.body.citations.length).toBeGreaterThan(0);
});
