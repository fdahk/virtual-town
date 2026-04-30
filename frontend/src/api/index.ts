import { apiGet, apiPatch, apiPost } from "./http";
import type {
  AgentProfile,
  AgentRuntimeState,
  Location,
  MapScene,
  MapTile,
  Memory,
  MemorySearchResult,
  PlayerTalkResponse,
  Portal,
  Relationship,
  Simulation,
  SimulationState,
  TilePosition,
  WorldEvent,
  WorldObject,
} from "../types/domain";
import type {
  AgentRuntimeView,
  DashboardPayload,
  HealthReport,
  LLMCallRecord,
  ObservabilityEvent,
  TaskRecord,
  ToolCallRecord,
  TraceBundle,
} from "../types/observability";

export const api = {
  // ---- world ----
  listScenes: () => apiGet<MapScene[]>("/api/world/scenes"),
  getScene: (id: string) => apiGet<MapScene>(`/api/world/scenes/${id}`),
  listTiles: (sceneId: string) =>
    apiGet<MapTile[]>(`/api/world/scenes/${sceneId}/tiles`),
  listLocations: (sceneId?: string) =>
    apiGet<Location[]>(
      sceneId ? `/api/world/locations?scene_id=${sceneId}` : "/api/world/locations",
    ),
  listPortals: (sceneId?: string) =>
    apiGet<Portal[]>(
      sceneId ? `/api/world/portals?scene_id=${sceneId}` : "/api/world/portals",
    ),
  listObjects: (sceneId?: string) =>
    apiGet<WorldObject[]>(
      sceneId ? `/api/world/objects?scene_id=${sceneId}` : "/api/world/objects",
    ),

  // ---- agents ----
  listAgents: () => apiGet<AgentProfile[]>("/api/agents"),
  getAgent: (id: string) => apiGet<AgentProfile>(`/api/agents/${id}`),
  getAgentState: (id: string) =>
    apiGet<AgentRuntimeState>(`/api/agents/${id}/state`),
  listAgentRelationships: (id: string) =>
    apiGet<Relationship[]>(`/api/agents/${id}/relationships`),
  listAgentMemories: (id: string, limit = 30) =>
    apiGet<Memory[]>(`/api/agents/${id}/memories?limit=${limit}`),
  searchAgentMemories: (id: string, query: string) =>
    apiPost<MemorySearchResult[]>(`/api/agents/${id}/memory/search`, { query }),

  // ---- player ----
  createPlayer: (body: {
    name: string;
    personality: string[];
    appearance_description?: string;
    background?: string;
    sprite_key?: string;
  }) => apiPost<AgentProfile>("/api/players", body),
  getMe: () => apiGet<AgentProfile>("/api/players/me"),
  movePlayer: (body: {
    target?: TilePosition;
    direction?: "up" | "down" | "left" | "right";
  }) =>
    apiPost<{ accepted: boolean; path: TilePosition[]; reason: string | null }>(
      "/api/players/me/move",
      body,
    ),
  interactPlayer: (body: {
    object_id?: string;
    entity_id?: string;
    interaction_type: string;
    extra?: Record<string, unknown>;
  }) =>
    apiPost<{
      success: boolean;
      events: string[];
      reaction: { reaction: string; emotion: string | null } | null;
      message: string | null;
    }>("/api/players/me/interact", body),
  talkPlayer: (body: { target_entity_id: string; text: string }) =>
    apiPost<PlayerTalkResponse>("/api/players/me/talk", body),

  // ---- simulation ----
  getCurrentSimulation: () => apiGet<Simulation>("/api/simulations/current"),
  getSimulationState: (id: string) =>
    apiGet<SimulationState>(`/api/simulations/${id}/state`),
  listSimulationEvents: (id: string, limit = 80) =>
    apiGet<WorldEvent[]>(`/api/simulations/${id}/events?limit=${limit}`),
  pauseSimulation: (id: string) =>
    apiPost<Simulation>(`/api/simulations/${id}/pause`, {}),
  resumeSimulation: (id: string) =>
    apiPost<Simulation>(`/api/simulations/${id}/resume`, {}),
  stepSimulation: (id: string) =>
    apiPost<{ simulation: Simulation }>(`/api/simulations/${id}/step`, {}),
  setSimulationSpeed: (id: string, speed_multiplier: number) =>
    apiPost<Simulation>(`/api/simulations/${id}/speed`, { speed_multiplier }),
};

// -----------------------------------------------------------------------------
// 可观测性（§14）
// -----------------------------------------------------------------------------

export const obsApi = {
  dashboard: () => apiGet<DashboardPayload>("/api/observability/dashboard"),
  listEvents: (params: {
    category?: string;
    trace_id?: string;
    level?: string;
    simulation_id?: string;
    entity_id?: string;
    limit?: number;
  } = {}) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
    });
    const qs = q.toString();
    return apiGet<ObservabilityEvent[]>(
      `/api/observability/events${qs ? `?${qs}` : ""}`,
    );
  },
  listWorldEvents: (params: {
    simulation_id?: string;
    event_type?: string;
    scene_id?: string;
    entity_id?: string;
    limit?: number;
  } = {}) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
    });
    const qs = q.toString();
    return apiGet<WorldEvent[]>(
      `/api/observability/world-events${qs ? `?${qs}` : ""}`,
    );
  },
  listLLMCalls: (params: { success?: boolean; model?: string; limit?: number } = {}) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
    });
    const qs = q.toString();
    return apiGet<LLMCallRecord[]>(
      `/api/observability/llm-calls${qs ? `?${qs}` : ""}`,
    );
  },
  listToolCalls: (params: {
    tool?: string;
    success?: boolean;
    agent_id?: string;
    limit?: number;
  } = {}) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
    });
    const qs = q.toString();
    return apiGet<ToolCallRecord[]>(
      `/api/observability/tool-calls${qs ? `?${qs}` : ""}`,
    );
  },
  listTasks: (params: { status?: string; task_type?: string; limit?: number } = {}) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
    });
    const qs = q.toString();
    return apiGet<TaskRecord[]>(
      `/api/observability/tasks${qs ? `?${qs}` : ""}`,
    );
  },
  getTrace: (traceId: string) =>
    apiGet<TraceBundle>(`/api/observability/traces/${traceId}`),
  getAgentRuntime: (agentId: string) =>
    apiGet<AgentRuntimeView>(`/api/observability/agents/${agentId}/runtime`),
  listErrors: (limit = 100) =>
    apiGet<ObservabilityEvent[]>(`/api/observability/errors?limit=${limit}`),
  health: {
    db: () => apiGet<HealthReport>("/api/health/db"),
    redis: () => apiGet<HealthReport>("/api/health/redis"),
    llm: () => apiGet<HealthReport>("/api/health/llm"),
  },
};

export { apiGet, apiPatch, apiPost };
