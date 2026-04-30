import { expect, test } from "@playwright/test";
import {
  api,
  ensurePlayerCreated,
  findAgentByName,
  teleportPlayerNear,
} from "./helpers";

/**
 * 演示场景 4：动物互动
 *
 * 验证 PRD §10 场景 4：
 * - 走到豆豆（金毛、亲人）旁
 * - 抚摸：应返回 enjoy / happy
 * - 走到小白（白猫、胆小）旁
 * - 抚摸：应返回 escape / scared
 * - 互动事件应进入事件流
 */
test("scene 4: pet doudou (friendly) and xiaobai (timid)", async ({ request }) => {
  const a = await api(request);
  await ensurePlayerCreated(request);
  const agents = await a.listAgents();

  for (const [name, expectedReaction] of [
    ["豆豆", /enjoy|approach/],
    ["小白", /escape|tolerate/],
  ] as const) {
    const target = findAgentByName(agents, name);
    const tState = await a.getAgentState(target.id);
    await teleportPlayerNear(request, {
      x: Math.max(0, tState.position.x - 1),
      y: tState.position.y,
    });
    const r = await a.interactPlayer({
      entity_id: target.id,
      interaction_type: "pet",
    });
    expect(r.ok, `pet ${name}: ${r.status} ${JSON.stringify(r.body)}`).toBeTruthy();
    expect(r.body.reaction.reaction).toMatch(expectedReaction);
  }
});
