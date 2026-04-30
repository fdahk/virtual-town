import { expect, test } from "@playwright/test";
import { api, ensurePlayerCreated, openTownPage, teleportPlayerNear, waitFor } from "./helpers";

/**
 * 演示场景 2：进入建筑内部
 *
 * 验证 PRD §10 场景 2：
 * - 玩家走到咖啡店门口（室外 (6, 22)）
 * - 经过 Portal 自动进入室内 cafe scene
 * - 后端 agent_state 反映 scene_id 切换
 * - 前端 EventLog 出现 "切换" 事件
 */
test("scene 2: walk into cafe interior", async ({ page, request }) => {
  await openTownPage(page);
  const a = await api(request);
  const me = await ensurePlayerCreated(request);
  const before = await a.getAgentState(me.id);
  expect(before.scene_id).toBe("scene_town_outdoor");

  // 走到咖啡店门口的 portal tile
  await teleportPlayerNear(request, { x: 6, y: 22 });

  // 等待场景切换完成
  await waitFor(async () => {
    const st = await a.getAgentState(me.id);
    return st.scene_id === "scene_cafe_inside";
  }, 30_000);

  // EventLog 应能见到"切换"事件标签
  await expect(page.getByText("切换", { exact: false }).first()).toBeVisible({ timeout: 15_000 });
});
