import { create } from "zustand";
import type {
  AgentProfile,
  AgentRuntimeState,
  Location,
  MapScene,
  MapTile,
  Portal,
  Simulation,
  WorldEvent,
  WorldObject,
} from "../types/domain";

interface WorldStore {
  // 元数据
  player: AgentProfile | null;
  agents: Record<string, AgentProfile>;
  scenes: Record<string, MapScene>;
  tilesByScene: Record<string, MapTile[]>;
  locationsByScene: Record<string, Location[]>;
  portalsByScene: Record<string, Portal[]>;
  objectsByScene: Record<string, WorldObject[]>;

  // 运行时
  simulation: Simulation | null;
  runtimeByAgent: Record<string, AgentRuntimeState>;
  events: WorldEvent[];

  // UI
  selectedAgentId: string | null;
  selectedObjectId: string | null;
  playerSceneId: string | null;
  pendingDialogue: {
    targetId: string;
    targetName: string;
  } | null;
  /** 正在追踪（自动靠近）的 NPC id；null 表示未追踪 */
  trackingAgentId: string | null;

  // actions
  setPlayer(profile: AgentProfile | null): void;
  setAgents(profiles: AgentProfile[]): void;
  setScenes(scenes: MapScene[]): void;
  setSceneTiles(sceneId: string, tiles: MapTile[]): void;
  setSceneLocations(sceneId: string, locations: Location[]): void;
  setScenePortals(sceneId: string, portals: Portal[]): void;
  setSceneObjects(sceneId: string, objects: WorldObject[]): void;
  /** 局部更新场景中某个物品的字段（state/位置/可交互列表等） */
  patchSceneObject(sceneId: string, objectId: string, patch: Partial<WorldObject>): void;
  /** 向场景中添加一个新物品（运行时由后端 spawn 事件触发） */
  addSceneObject(sceneId: string, object: WorldObject): void;
  /** 从场景中移除一个物品（运行时由后端 despawn 事件触发） */
  removeSceneObject(sceneId: string, objectId: string): void;
  setSimulation(sim: Simulation | null): void;
  patchSimulationClock(worldTime: string, step: number): void;
  setRuntimeStates(states: AgentRuntimeState[]): void;
  upsertRuntime(state: AgentRuntimeState): void;
  setEvents(events: WorldEvent[]): void;
  pushEvents(events: WorldEvent[]): void;
  selectAgent(id: string | null): void;
  selectObject(id: string | null): void;
  setPendingDialogue(data: WorldStore["pendingDialogue"]): void;
  setTrackingAgent(id: string | null): void;
}

export const useWorldStore = create<WorldStore>((set) => ({
  player: null,
  agents: {},
  scenes: {},
  tilesByScene: {},
  locationsByScene: {},
  portalsByScene: {},
  objectsByScene: {},
  simulation: null,
  runtimeByAgent: {},
  events: [],
  selectedAgentId: null,
  selectedObjectId: null,
  playerSceneId: null,
  pendingDialogue: null,
  trackingAgentId: null,

  setPlayer: (profile) =>
    set(() => ({
      player: profile,
    })),
  setAgents: (profiles) =>
    set(() => ({
      agents: Object.fromEntries(profiles.map((p) => [p.id, p])),
    })),
  setScenes: (scenes) =>
    set(() => ({ scenes: Object.fromEntries(scenes.map((s) => [s.id, s])) })),
  setSceneTiles: (sceneId, tiles) =>
    set((s) => ({ tilesByScene: { ...s.tilesByScene, [sceneId]: tiles } })),
  setSceneLocations: (sceneId, locations) =>
    set((s) => ({
      locationsByScene: { ...s.locationsByScene, [sceneId]: locations },
    })),
  setScenePortals: (sceneId, portals) =>
    set((s) => ({ portalsByScene: { ...s.portalsByScene, [sceneId]: portals } })),
  setSceneObjects: (sceneId, objects) =>
    set((s) => ({ objectsByScene: { ...s.objectsByScene, [sceneId]: objects } })),

  patchSceneObject: (sceneId, objectId, patch) =>
    set((s) => {
      const list = s.objectsByScene[sceneId];
      if (!list) return {};
      const next = list.map((o) => {
        if (o.id !== objectId) return o;
        // state 是嵌套对象，需要单独 merge
        const merged: WorldObject = { ...o, ...patch };
        if (patch.state) merged.state = { ...o.state, ...patch.state };
        return merged;
      });
      return { objectsByScene: { ...s.objectsByScene, [sceneId]: next } };
    }),

  addSceneObject: (sceneId, object) =>
    set((s) => {
      const list = s.objectsByScene[sceneId] ?? [];
      // 同 id 去重
      if (list.some((o) => o.id === object.id)) return {};
      return { objectsByScene: { ...s.objectsByScene, [sceneId]: [...list, object] } };
    }),

  removeSceneObject: (sceneId, objectId) =>
    set((s) => {
      const list = s.objectsByScene[sceneId];
      if (!list) return {};
      const next = list.filter((o) => o.id !== objectId);
      // 同时清理选中态
      const cleared = s.selectedObjectId === objectId ? { selectedObjectId: null } : {};
      return { objectsByScene: { ...s.objectsByScene, [sceneId]: next }, ...cleared };
    }),

  setSimulation: (sim) => set(() => ({ simulation: sim })),
  patchSimulationClock: (worldTime, step) =>
    set((s) =>
      s.simulation
        ? { simulation: { ...s.simulation, world_time: worldTime, current_step: step } }
        : {}
    ),
  setRuntimeStates: (states) =>
    set((s) => {
      const map = { ...s.runtimeByAgent };
      for (const st of states) {
        map[st.agent_id] = st;
      }
      let playerSceneId = s.playerSceneId;
      const player = s.player;
      if (player && map[player.id]) {
        playerSceneId = map[player.id].scene_id;
      }
      return { runtimeByAgent: map, playerSceneId };
    }),
  upsertRuntime: (state) =>
    set((s) => {
      const map = { ...s.runtimeByAgent, [state.agent_id]: state };
      let playerSceneId = s.playerSceneId;
      if (s.player && s.player.id === state.agent_id) {
        playerSceneId = state.scene_id;
      }
      return { runtimeByAgent: map, playerSceneId };
    }),
  setEvents: (events) => set(() => ({ events })),
  pushEvents: (events) =>
    set((s) => ({
      events: [...events, ...s.events].slice(0, 200),
    })),
  selectAgent: (id) => set(() => ({ selectedAgentId: id, selectedObjectId: null })),
  selectObject: (id) => set(() => ({ selectedObjectId: id, selectedAgentId: null })),
  setPendingDialogue: (data) => set(() => ({ pendingDialogue: data })),
  setTrackingAgent: (id) => set(() => ({ trackingAgentId: id })),
}));
