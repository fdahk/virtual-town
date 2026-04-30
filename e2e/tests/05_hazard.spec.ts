import { expect, test } from "@playwright/test";
import { api, ensurePlayerCreated, teleportPlayerNear, waitFor } from "./helpers";

/**
 * 演示场景 5：环境后果（溺水）
 *
 * 验证 PRD §10 场景 5：
 * - 河流位于 y=4,5；x in [12,16] 是桥（安全），其他段是 deep_water；
 *   河岸 y=3 在桥外段被护栏（barrier）阻挡，但寻路若选择从 y=3 穿过会被拒绝。
 * - 让玩家走到 deep_water tile（例：x=10, y=5）触发 DROWNING 状态。
 */
test("scene 5: walking into unguarded river triggers DROWNING", async ({ request }) => {
  const a = await api(request);
  const me = await ensurePlayerCreated(request);

  // 先暂停仿真避免引擎自动让玩家从 hazard 中漂出
  const sim = await a.currentSimulation();
  await a.pauseSimulation(sim.id);

  try {
    // 直接寻路到 deep_water tile (10,5)
    const r = await a.movePlayer({ x: 10, y: 5 });
    expect(r.accepted, `move into water rejected: ${r.reason}`).toBeTruthy();

    await a.resumeSimulation(sim.id);

    // 等到状态机切到 DROWNING
    await waitFor(async () => {
      const st = await a.getAgentState(me.id);
      return st.state === "DROWNING" || st.status_effects.includes("drowning");
    }, 30_000);
  } finally {
    await a.resumeSimulation(sim.id).catch(() => undefined);
  }
});
