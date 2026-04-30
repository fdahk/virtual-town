import { expect, test } from "@playwright/test";
import { openTownPage } from "./helpers";

/**
 * 演示场景 1：玩家进入小镇
 *
 * 验证 PRD §10 的"场景 1"：
 * - 打开网页
 * - 创建角色（如果还没创建）
 * - 进入室外地图
 * - 顶部时间、左侧 NPC 列表、底部事件日志可见
 */
test("scene 1: enter town and see baseline UI", async ({ page }) => {
  await openTownPage(page);

  // 顶部时间控件（"⏸ 暂停" 或 "▶ 继续"）
  const playPause = page.locator("button", { hasText: /暂停|继续/ });
  await expect(playPause.first()).toBeVisible();

  // 左侧"小镇成员"列表 + 至少 6 个名字之一
  await expect(page.getByText("小镇成员")).toBeVisible();
  await expect(page.getByText("小芳", { exact: false }).first()).toBeVisible();
  await expect(page.getByText("豆豆", { exact: false }).first()).toBeVisible();

  // 底部事件日志
  await expect(page.getByText("事件日志")).toBeVisible();

  // 中央 Phaser 画布存在
  const canvas = page.locator("canvas").first();
  await expect(canvas).toBeVisible();
});
