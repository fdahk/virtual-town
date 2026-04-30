// 前端本地类型。与 `docs/实施方案/数据模型与接口契约V1.md` 保持一致。
// 未来可被 `npm run gen:types` 生成的 `api.ts` 替换。

export type EntityType = "human" | "animal" | "player";
export type SceneType = "outdoor" | "indoor";
export type AgentState =
  | "IDLE"
  | "THINKING"
  | "PLANNING_PATH"
  | "MOVING"
  | "INTERACTING"
  | "CHATTING"
  | "WAITING"
  | "SLEEPING"
  | "BLOCKED"
  | "DROWNING"
  | "PANIC";

export interface TilePosition {
  x: number;
  y: number;
}

export interface Bounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface MapScene {
  id: string;
  name: string;
  scene_type: SceneType;
  width: number;
  height: number;
  tile_size: number;
  tiled_map_url: string | null;
  description: string | null;
}

export interface MapTile {
  scene_id: string;
  x: number;
  y: number;
  terrain: string;
  walkable: boolean;
  blocks_movement: boolean;
  blocks_vision: boolean;
  hazard_type: string | null;
  hazard_level: number | null;
  tags: string[];
}

export interface Location {
  id: string;
  scene_id: string;
  name: string;
  location_type: "building" | "room" | "outdoor_area" | "facility";
  bounds: Bounds;
  entry_tiles: TilePosition[];
  open_hours: { start: string; end: string } | null;
  tags: string[];
  description: string | null;
}

export interface Portal {
  id: string;
  from_scene_id: string;
  from_tile: TilePosition;
  to_scene_id: string;
  to_tile: TilePosition;
  interaction_type: "auto_enter" | "click_enter";
  requires_permission: boolean;
  name: string | null;
}

export interface WorldObject {
  id: string;
  scene_id: string;
  name: string;
  object_type:
    | "furniture"
    | "facility"
    | "barrier"
    | "plant"
    | "decoration"
    | "item";
  position: TilePosition;
  size: { width: number; height: number };
  blocks_movement: boolean;
  available_interactions: string[];
  state: Record<string, unknown>;
  tags: string[];
}

export interface AgentProfile {
  id: string;
  entity_type: EntityType;
  name: string;
  age: number | null;
  gender: string | null;
  species: string | null;
  appearance: {
    sprite_sheet?: string;
    color?: string;
    scale?: number;
    description?: string;
  };
  occupation: string | null;
  personality: string[];
  background: string;
  lifestyle: string | null;
  long_term_goals: string[];
  home_location_id: string | null;
}

export interface AgentRuntimeState {
  agent_id: string;
  scene_id: string;
  position: TilePosition;
  state: AgentState;
  emotion: string | null;
  energy: number;
  hunger: number;
  social_need: number | null;
  fear: number | null;
  status_effects: string[];
  current_action_id: string | null;
  current_goal: string | null;
  facing: "up" | "down" | "left" | "right" | string;
  updated_at: string;
}

export interface WorldEvent {
  id: string;
  simulation_id: string;
  event_type: string;
  source: string;
  actor_entity_id: string | null;
  target_entity_id: string | null;
  location_id: string | null;
  scene_id: string | null;
  description: string;
  importance: number;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface Simulation {
  id: string;
  status: "idle" | "running" | "paused" | "stopped";
  world_time: string;
  world_tick_hz: number;
  ai_tick_minutes: number;
  speed_multiplier: number;
  current_step: number;
}

export interface SimulationState {
  simulation: Simulation;
  entities: AgentRuntimeState[];
}

export interface SimulationDeltaPayload {
  step: number;
  world_time: string;
  /** 单调递增的序号（§14.5）：前端据此检测丢失与乱序。 */
  seq?: number | null;
  entity_updates: AgentRuntimeState[];
  events: WorldEvent[];
}

export interface Relationship {
  id: string;
  from_agent_id: string;
  to_entity_id: string;
  familiarity: number;
  trust: number;
  affection: number;
  fear: number;
  summary: string | null;
  updated_at: string;
}

export interface Memory {
  id: string;
  agent_id: string;
  memory_type: "event" | "thought" | "chat" | "summary";
  scope: "working" | "short_term" | "long_term";
  subject: string | null;
  predicate: string | null;
  object: string | null;
  description: string;
  importance: number;
  emotional_valence: number;
  keywords: string[];
  evidence_memory_ids: string[];
  created_at: string;
  last_accessed_at: string | null;
  ttl_expires_at: string | null;
}

export interface MemorySearchResult {
  memory: Memory;
  score: number;
  score_detail: {
    relevance: number;
    importance: number;
    recency: number;
    relationship_relevance?: number | null;
  };
}

export interface PlayerTalkResponse {
  conversation_id: string;
  reply: string;
  emotion: string | null;
  memory_ids: string[];
  citations: Array<{
    memory_id: string;
    description: string;
    score: number;
  }>;
}
