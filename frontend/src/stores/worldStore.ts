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
  playerSceneId: string | null;
  pendingDialogue: {
    targetId: string;
    targetName: string;
  } | null;

  // actions
  setPlayer(profile: AgentProfile | null): void;
  setAgents(profiles: AgentProfile[]): void;
  setScenes(scenes: MapScene[]): void;
  setSceneTiles(sceneId: string, tiles: MapTile[]): void;
  setSceneLocations(sceneId: string, locations: Location[]): void;
  setScenePortals(sceneId: string, portals: Portal[]): void;
  setSceneObjects(sceneId: string, objects: WorldObject[]): void;
  setSimulation(sim: Simulation | null): void;
  setRuntimeStates(states: AgentRuntimeState[]): void;
  upsertRuntime(state: AgentRuntimeState): void;
  setEvents(events: WorldEvent[]): void;
  pushEvents(events: WorldEvent[]): void;
  selectAgent(id: string | null): void;
  setPendingDialogue(data: WorldStore["pendingDialogue"]): void;
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
  playerSceneId: null,
  pendingDialogue: null,

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

  setSimulation: (sim) => set(() => ({ simulation: sim })),
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
  selectAgent: (id) => set(() => ({ selectedAgentId: id })),
  setPendingDialogue: (data) => set(() => ({ pendingDialogue: data })),
}));
