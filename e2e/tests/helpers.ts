/**
 * 测试 fixture / 通用工具。
 *
 * 关键约束：
 * - 后端是权威，所有"把世界恢复到已知状态"都通过 REST API 完成。
 * - 不直接 reach into Phaser，仅通过用户可见的 DOM 与 API 验证产品行为。
 * - 由于 Phaser 渲染在 canvas 内，断言时优先检查 React 面板（事件日志、对话窗、状态面板等）的 DOM。
 */
import { type APIRequestContext, type Page, expect } from "@playwright/test";

const API = process.env.E2E_API_URL ?? "http://localhost:8000";

export interface AgentProfile {
  id: string;
  name: string;
  entity_type: "human" | "animal" | "player";
}

export interface AgentRuntimeState {
  agent_id: string;
  scene_id: string;
  position: { x: number; y: number };
  state: string;
  status_effects: string[];
}

export async function api(req: APIRequestContext) {
  return {
    listAgents: async (): Promise<AgentProfile[]> => {
      const r = await req.get(`${API}/api/agents`);
      expect(r.ok(), `GET /api/agents failed: ${r.status()}`).toBeTruthy();
      return r.json();
    },
    getAgentState: async (id: string): Promise<AgentRuntimeState> => {
      const r = await req.get(`${API}/api/agents/${id}/state`);
      expect(r.ok(), `GET state ${id}: ${r.status()}`).toBeTruthy();
      return r.json();
    },
    getMe: async (): Promise<AgentProfile | null> => {
      const r = await req.get(`${API}/api/players/me`);
      if (r.status() === 404) return null;
      expect(r.ok(), `GET /api/players/me: ${r.status()}`).toBeTruthy();
      return r.json();
    },
    movePlayer: async (target: { x: number; y: number }) => {
      const r = await req.post(`${API}/api/players/me/move`, { data: { target } });
      expect(r.ok(), `POST move: ${r.status()}`).toBeTruthy();
      return r.json();
    },
    interactPlayer: async (body: {
      object_id?: string;
      entity_id?: string;
      interaction_type: string;
    }) => {
      const r = await req.post(`${API}/api/players/me/interact`, { data: body });
      return { ok: r.ok(), status: r.status(), body: await r.json() };
    },
    talkPlayer: async (target_entity_id: string, text: string) => {
      const r = await req.post(`${API}/api/players/me/talk`, {
        data: { target_entity_id, text },
      });
      return { ok: r.ok(), status: r.status(), body: await r.json() };
    },
    forceReflect: async (agent_id: string) => {
      const r = await req.post(`${API}/api/agents/${agent_id}/reflect`);
      expect(r.ok(), `POST reflect: ${r.status()}`).toBeTruthy();
      return r.json();
    },
    listMemories: async (agent_id: string) => {
      const r = await req.get(`${API}/api/agents/${agent_id}/memories?limit=50`);
      expect(r.ok(), `GET memories: ${r.status()}`).toBeTruthy();
      return r.json();
    },
    searchMemories: async (agent_id: string, query: string) => {
      const r = await req.post(`${API}/api/agents/${agent_id}/memory/search`, {
        data: { query, limit: 8 },
      });
      expect(r.ok(), `POST mem search: ${r.status()}`).toBeTruthy();
      return r.json();
    },
    listEvents: async (sim_id: string) => {
      const r = await req.get(`${API}/api/simulations/${sim_id}/events?limit=80`);
      expect(r.ok(), `GET events: ${r.status()}`).toBeTruthy();
      return r.json();
    },
    currentSimulation: async () => {
      const r = await req.get(`${API}/api/simulations/current`);
      expect(r.ok(), `GET sim: ${r.status()}`).toBeTruthy();
      return r.json();
    },
    pauseSimulation: async (id: string) => {
      const r = await req.post(`${API}/api/simulations/${id}/pause`);
      expect(r.ok()).toBeTruthy();
      return r.json();
    },
    resumeSimulation: async (id: string) => {
      const r = await req.post(`${API}/api/simulations/${id}/resume`);
      expect(r.ok()).toBeTruthy();
      return r.json();
    },
  };
}

/**
 * 让玩家瞬移到指定位置：通过依次发送多次 API 移动请求实现。
 * 当无法寻路（被墙阻挡）时直接用 backend 的 move 接口可能拒绝；该 helper 仅在
 * 用例需要快速逼近某个目标时使用。
 */
export async function teleportPlayerNear(
  req: APIRequestContext,
  target: { x: number; y: number },
): Promise<void> {
  const a = await api(req);
  // 后端会自动 A* 寻路，单次 move 给目标点即可
  const r = await a.movePlayer(target);
  expect(r.accepted, `move target=(${target.x},${target.y}) rejected: ${r.reason}`).toBeTruthy();
  // 等待引擎跟进若干 tick 把玩家挪到目标
  await waitFor(async () => {
    const me = await a.getMe();
    if (!me) return false;
    const st = await a.getAgentState(me.id);
    return Math.abs(st.position.x - target.x) <= 1 && Math.abs(st.position.y - target.y) <= 1;
  }, 30_000);
}

export async function waitFor(
  predicate: () => Promise<boolean>,
  timeoutMs = 15_000,
  intervalMs = 250,
): Promise<void> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (await predicate()) return;
    await sleep(intervalMs);
  }
  throw new Error(`waitFor timed out after ${timeoutMs}ms`);
}

export function sleep(ms: number): Promise<void> {
  return new Promise((res) => setTimeout(res, ms));
}

export async function ensurePlayerCreated(req: APIRequestContext): Promise<AgentProfile> {
  const a = await api(req);
  const me = await a.getMe();
  if (me && me.name) return me;
  const r = await req.post(`${API}/api/players`, {
    data: { name: "测试旅人", personality: ["好奇"], background: "Playwright 自动用户" },
  });
  expect(r.ok()).toBeTruthy();
  return r.json();
}

export async function openTownPage(page: Page): Promise<void> {
  await page.goto("/");
  // 创建角色页可能出现：填名字进入小镇
  const nameInput = page.locator("input").first();
  if (await nameInput.isVisible({ timeout: 1500 }).catch(() => false)) {
    await nameInput.fill("E2E 旅人");
    await page.getByRole("button", { name: /进入小镇/ }).click();
  }
  // 等到 EventLog 标题渲染，证明已进入主页面
  await expect(page.getByText("事件日志", { exact: true })).toBeVisible({ timeout: 30_000 });
}

export function findAgentByName<T extends { name: string }>(
  list: T[],
  name: string,
): T {
  const r = list.find((a) => a.name === name);
  if (!r) throw new Error(`agent ${name} not found`);
  return r;
}
