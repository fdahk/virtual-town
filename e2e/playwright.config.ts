import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright 配置。
 *
 * 使用方式：
 *   1. 启动后端 + 数据库：./scripts/dev.sh（或单独 docker compose up + alembic upgrade + seed）。
 *   2. 启动前端：cd frontend && npm run dev（监听 5173）。
 *   3. 在 e2e/ 目录：npm install && npm test。
 *
 * 配置约束：
 * - 全部用例在同一会话中跑，依赖 seed 出来的演示世界；
 * - 每个用例自己负责把世界恢复到可比较的状态（不互相依赖顺序）。
 */
export default defineConfig({
  testDir: "./tests",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173",
    actionTimeout: 8_000,
    navigationTimeout: 20_000,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    viewport: { width: 1440, height: 900 },
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
