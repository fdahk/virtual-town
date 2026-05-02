import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PhaserGame } from "../game/PhaserGame";
import { TownScene } from "../game/scenes/TownScene";
import { eventBus, type NaturalEffectPayload } from "../game/eventBus";

/** 后端自然事件类型集合 —— 直接透传给 TownScene 渲染动效 */
const _NATURAL_EVENT_TYPES = new Set([
  "world.fire_started", "world.fire_spread", "world.fire_extinguished",
  "weather.condition_changed", "weather.thunder",
  "nature.fishing_spot_appeared", "nature.mushroom_appeared", "nature.mushroom_withered",
  "nature.flower_bloomed", "nature.flower_withered", "nature.firefly_appeared",
  "nature.bird_appeared", "nature.bird_flew_away",
  "nature.puddle_formed", "nature.puddle_dried",
  "nature.fruit_ripened", "world.storm_warning",
  "nature.mushroom_picked", "nature.fruit_picked", "nature.fish_caught",
]);

/** 物品生命周期事件类型集合 —— 触发 store 物品列表更新 + 场景重渲染 */
const _OBJECT_LIFECYCLE_TYPES = new Set([
  "world.object_state_changed",
  "world.object_spawned",
  "world.object_despawned",
  // 同时也是状态变化事件（带 patch）
  "nature.mushroom_picked",
  "nature.fruit_picked",
  "nature.fish_caught",
  "nature.mushroom_appeared",
  "nature.mushroom_withered",
  "nature.flower_bloomed",
  "nature.flower_withered",
  "nature.fruit_ripened",
  "nature.fishing_spot_appeared",
  "nature.puddle_formed",
  "nature.puddle_dried",
  "world.fire_started",
  "world.fire_extinguished",
]);
import { simulationSocket } from "../api/websocket";
import { api } from "../api";
import { useWorldStore } from "../stores/worldStore";
import type { SimulationDeltaPayload, WorldEvent, WorldObject } from "../types/domain";
import { AgentList } from "../components/AgentList";
import { AgentPanel } from "../components/AgentPanel";
import { ObjectPanel } from "../components/ObjectPanel";
import { EventLog } from "../components/EventLog";
import { ChatPanel } from "../components/ChatPanel";
import { TimeControl } from "../components/TimeControl";
import { DebugToggle } from "../components/DebugToggle";
import { MemoryViewer } from "../components/MemoryViewer";
import { SaveGameButton } from "../components/SaveGameButton";

function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, n));
}

/** 与后端 `WeatherState.condition` / agent_decision 中文描述对齐 */
const WEATHER_CONDITION_LABELS: Record<string, string> = {
  sunny: "晴天",
  cloudy: "多云",
  rainy: "下雨",
  stormy: "暴风雨",
  foggy: "有雾",
};

function weatherStatusFromEvents(events: WorldEvent[]): string {
  const evt = events.find((e) => e.event_type === "weather.condition_changed");
  if (!evt) return "天气：—";
  const p = evt.payload;
  const raw = typeof p.condition === "string" ? p.condition : "";
  const label = WEATHER_CONDITION_LABELS[raw] ?? (raw || "未知");
  const intensity = typeof p.intensity === "number" ? p.intensity : null;
  if (intensity !== null && intensity > 0.05) {
    return `天气：${label}（强度 ${Math.round(intensity * 100)}%）`;
  }
  return `天气：${label}`;
}

/** 进入游戏前的加载进度覆盖层。 */
function LoadingOverlay({
  phaserProgress,
  dataReady,
}: {
  phaserProgress: number;
  dataReady: boolean;
}) {
  // Phaser 资源占 75%，API 数据占 25%
  const pct = Math.round(phaserProgress * 75 + (dataReady ? 25 : 0));
  const tips = [
    "正在加载地图图块…",
    "正在准备角色精灵…",
    "正在连接世界数据…",
    "正在初始化自然事件系统…",
    "即将进入小镇…",
  ];
  const tipIdx = Math.min(Math.floor(pct / 22), tips.length - 1);

  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        background: "linear-gradient(160deg, #0c1118 0%, #0a1520 60%, #060e18 100%)",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 28,
        zIndex: 9999,
        userSelect: "none",
      }}
    >
      {/* 像素风格标题 */}
      <div style={{ textAlign: "center" }}>
        <div
          style={{
            fontSize: 32,
            fontWeight: 700,
            color: "#e8d4a0",
            letterSpacing: "0.15em",
            textShadow: "0 0 20px rgba(232,212,160,0.35)",
            fontFamily: "monospace",
          }}
        >
          AI 小镇
        </div>
        <div
          style={{
            fontSize: 13,
            color: "rgba(255,255,255,0.35)",
            marginTop: 6,
            letterSpacing: "0.1em",
          }}
        >
          Virtual Town Simulation
        </div>
      </div>

      {/* 进度条容器 */}
      <div style={{ width: 280, display: "flex", flexDirection: "column", gap: 10 }}>
        {/* 外框（像素风格：2px solid border） */}
        <div
          style={{
            position: "relative",
            height: 18,
            border: "2px solid rgba(180,150,80,0.5)",
            borderRadius: 2,
            background: "rgba(0,0,0,0.4)",
            overflow: "hidden",
          }}
        >
          {/* 填充条 */}
          <div
            style={{
              position: "absolute",
              left: 0,
              top: 0,
              bottom: 0,
              width: `${pct}%`,
              background: "linear-gradient(90deg, #b49640 0%, #e8d070 50%, #b49640 100%)",
              backgroundSize: "200% 100%",
              animation: "loadShimmer 1.5s linear infinite",
              transition: "width 0.3s ease",
              boxShadow: "0 0 8px rgba(232,208,112,0.5)",
            }}
          />
          {/* 像素点缀：竖向条纹覆盖 */}
          <div
            style={{
              position: "absolute",
              inset: 0,
              backgroundImage:
                "repeating-linear-gradient(90deg, transparent 0px, transparent 3px, rgba(0,0,0,0.15) 3px, rgba(0,0,0,0.15) 4px)",
              pointerEvents: "none",
            }}
          />
        </div>

        {/* 百分比 + 提示文字 */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <span style={{ fontSize: 11, color: "rgba(255,255,255,0.4)", letterSpacing: "0.05em" }}>
            {tips[tipIdx]}
          </span>
          <span
            style={{
              fontSize: 13,
              fontWeight: 600,
              color: "#d4b84a",
              fontFamily: "monospace",
              minWidth: 36,
              textAlign: "right",
            }}
          >
            {pct}%
          </span>
        </div>
      </div>

      {/* 动态加载小点 */}
      <div style={{ display: "flex", gap: 6 }}>
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            style={{
              width: 6,
              height: 6,
              borderRadius: 1,
              background: "#b49640",
              opacity: 0.3,
              animation: `loadDot 1.2s ${i * 0.2}s ease-in-out infinite`,
            }}
          />
        ))}
      </div>

      <style>{`
        @keyframes loadShimmer {
          0% { background-position: 200% 0; }
          100% { background-position: -200% 0; }
        }
        @keyframes loadDot {
          0%, 80%, 100% { opacity: 0.25; transform: scaleY(1); }
          40% { opacity: 1; transform: scaleY(1.6); }
        }
      `}</style>
    </div>
  );
}

/** 右侧面板：根据当前选中的是物品还是居民动态切换。 */
function RightPanel({ onOpenMemory }: { onOpenMemory: (id: string) => void }) {
  const selectedObjectId = useWorldStore((s) => s.selectedObjectId);
  if (selectedObjectId) return <ObjectPanel />;
  return <AgentPanel onOpenMemory={onOpenMemory} />;
}

const INTERACTION_RADIUS = 2; // 与后端 player_service.INTERACTION_RADIUS 保持一致

export function TownPage() {
  const sceneRef = useRef<TownScene | null>(null);
  const loadedScenesRef = useRef(new Set<string>());
  /** 正在进行中的场景预取（防止 Phaser ready 与 playerSceneId 变化同时触发重复请求） */
  const sceneFetchingRef = useRef(new Set<string>());
  const [sceneReady, setSceneReady] = useState(false);
  /** Phaser 资源加载进度 0-1（由 preload 事件上报） */
  const [phaserProgress, setPhaserProgress] = useState(0);
  /** 首个场景的 API 数据是否已取回 */
  const [dataReady, setDataReady] = useState(false);
  /** Phaser + API 数据全部就绪后置为 true，触发隐藏加载屏幕 */
  const gameFullyReady = sceneReady && dataReady;
  const [memoryViewerFor, setMemoryViewerFor] = useState<string | null>(null);

  // 追踪状态（用 ref 避免 stale closure；同步写入 store 用于 UI 提示）
  const trackingRef = useRef<string | null>(null);
  const lastApproachRef = useRef<number>(0);
  // 始终指向最新 store，用于在事件回调中读取
  const storeRef = useRef(useWorldStore.getState());

  const [viewport, setViewport] = useState(() => ({
    w: typeof window !== "undefined" ? window.innerWidth : 1200,
    h: typeof window !== "undefined" ? window.innerHeight : 800,
  }));
  const [leftPanelWidth, setLeftPanelWidth] = useState(240);
  const [footerHeight, setFooterHeight] = useState(150);

  const leftMax = useMemo(
    () => Math.min(480, Math.floor(viewport.w * (viewport.w < 640 ? 0.78 : 0.45))),
    [viewport.w],
  );
  const footerMax = useMemo(
    () => Math.min(420, Math.floor(viewport.h * 0.52)),
    [viewport.h],
  );

  useEffect(() => {
    const onResize = () =>
      setViewport({ w: window.innerWidth, h: window.innerHeight });
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    setLeftPanelWidth((w) => clamp(w, 0, leftMax));
  }, [leftMax]);
  useEffect(() => {
    setFooterHeight((h) => clamp(h, 0, footerMax));
  }, [footerMax]);

  type DragKind = "left" | "footer";
  const dragRef = useRef<{ kind: DragKind; start: number; initial: number } | null>(null);

  const onLeftResizePointerDown = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
      dragRef.current = { kind: "left", start: e.clientX, initial: leftPanelWidth };
    },
    [leftPanelWidth],
  );

  const onFooterResizePointerDown = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
      dragRef.current = { kind: "footer", start: e.clientY, initial: footerHeight };
    },
    [footerHeight],
  );

  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      const d = dragRef.current;
      if (!d) return;
      if (d.kind === "left") {
        const dx = e.clientX - d.start;
        setLeftPanelWidth(clamp(d.initial + dx, 0, leftMax));
      } else {
        const dy = e.clientY - d.start;
        setFooterHeight(clamp(d.initial - dy, 0, footerMax));
      }
    };
    const onUp = () => {
      dragRef.current = null;
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };
  }, [leftMax, footerMax]);

  const store = useWorldStore();

  /**
   * 任意 React overlay（对话框、记忆查看器等）处于打开状态时为 true。
   * 此时地图的鼠标点击与键盘移动均应被屏蔽，避免误触发玩家移动 / 交互。
   */
  const isOverlayOpen = !!store.pendingDialogue || !!memoryViewerFor;

  const weatherStatusText = useMemo(
    () => weatherStatusFromEvents(store.events),
    [store.events],
  );
  // 始终保持 storeRef 与最新状态同步
  storeRef.current = store;

  useEffect(() => {
    (async () => {
      const [agents, scenes, sim, me] = await Promise.all([
        api.listAgents(),
        api.listScenes(),
        api.getCurrentSimulation(),
        api.getMe().catch(() => null),
      ]);
      store.setAgents(agents);
      store.setScenes(scenes);
      store.setSimulation(sim);
      if (me) store.setPlayer(me);
      // 初始化昼夜光照（场景可能还未就绪，延迟 500ms 等待 create() 完成）
      setTimeout(() => sceneRef.current?.setWorldTime(sim.world_time), 500);
      const state = await api.getSimulationState(sim.id);
      store.setRuntimeStates(state.entities);
      const events = await api.listSimulationEvents(sim.id, 80);
      store.setEvents(events);
      // 断线重连后自动拉取最新快照替换 runtime（§14.5）
      simulationSocket.setReconnectHook(async () => {
        try {
          const [snapshot, latestSim] = await Promise.all([
            api.getSimulationState(sim.id),
            api.getCurrentSimulation(),
          ]);
          store.setRuntimeStates(snapshot.entities);
          store.setSimulation(latestSim);
        } catch (err) {
          console.warn("[town] snapshot refresh failed", err);
        }
      });
      simulationSocket.connect(sim.id);
    })();
    /**
     * 物品生命周期事件 → 更新本地 store + 通知 TownScene 局部重渲染。
     * 即使是同一个对象也只重画 objectLayer，不会清空 tile/agent 层。
     */
    const applyObjectLifecycleEvent = (
      eventType: string,
      raw: Record<string, unknown>,
    ): void => {
      const s = storeRef.current;
      const sceneId = (raw.scene_id as string | undefined) ?? s.playerSceneId;
      const objectId = raw.object_id as string | undefined;
      if (!sceneId || !objectId) return;

      if (eventType === "world.object_despawned") {
        s.removeSceneObject(sceneId, objectId);
      } else if (eventType === "world.object_spawned") {
        s.addSceneObject(sceneId, {
          id: objectId,
          scene_id: sceneId,
          name: (raw.name as string) ?? "未命名物品",
          object_type: (raw.object_type as WorldObject["object_type"]) ?? "item",
          position: (raw.position as { x: number; y: number }) ?? { x: 0, y: 0 },
          size: (raw.size as { width: number; height: number }) ?? { width: 1, height: 1 },
          blocks_movement: !!raw.blocks_movement,
          available_interactions: (raw.available_interactions as string[]) ?? [],
          state: (raw.state as Record<string, unknown>) ?? {},
          tags: (raw.tags as string[]) ?? [],
        });
      } else {
        // 其余视为状态变更：合并 patch 或全量 state
        const patch = (raw.patch as Record<string, unknown>) ?? null;
        const fullState = (raw.state as Record<string, unknown> | undefined) ?? null;
        const stateUpdate = fullState ?? patch;
        // 没有任何状态信息就不触发刷新（NPC 钓鱼等事件不改对象 state）
        if (!stateUpdate || Object.keys(stateUpdate).length === 0) return;
        s.patchSceneObject(sceneId, objectId, { state: stateUpdate });
      }
      // 通知 TownScene 重画 objectLayer
      const scene = sceneRef.current;
      if (scene && scene.currentScene() === sceneId) {
        const next = storeRef.current.objectsByScene[sceneId] ?? [];
        scene.refreshObjects(next);
      }
    };

    const unsub = simulationSocket.subscribe((type, payload) => {
      if (type === "simulation.delta") {
        const p = payload as SimulationDeltaPayload;
        store.patchSimulationClock(p.world_time, p.step);
        store.setRuntimeStates(p.entity_updates);
        sceneRef.current?.setWorldTime(p.world_time);
        if (p.events.length) {
          store.pushEvents(p.events);
          for (const evt of p.events) {
            // ① 物品生命周期：先更新 store，再触发场景重渲染
            if (_OBJECT_LIFECYCLE_TYPES.has(evt.event_type)) {
              applyObjectLifecycleEvent(evt.event_type, evt.payload);
            }
            // ② 自然事件视觉效果：透传到 TownScene 的 effectLayer
            if (_NATURAL_EVENT_TYPES.has(evt.event_type)) {
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              eventBus.emit({ type: evt.event_type as any, payload: evt.payload as NaturalEffectPayload });
            }
          }
        }
        for (const update of p.entity_updates) {
          eventBus.emit({
            type: "runtime.update",
            payload: {
              agentId: update.agent_id,
              sceneId: update.scene_id,
              x: update.position.x,
              y: update.position.y,
              state: update.state,
              facing: update.facing,
            },
          });
        }
      } else if (type === "world.scene_changed") {
        const p = payload as { entity_id: string; to_scene_id: string; position: { x: number; y: number } };
        eventBus.emit({
          type: "runtime.update",
          payload: {
            agentId: p.entity_id,
            sceneId: p.to_scene_id,
            x: p.position.x,
            y: p.position.y,
            state: "IDLE",
          },
        });
      } else if (type === "dialogue.message_created") {
        const msg = payload as {
          speaker_id: string;
          target_id: string | null;
          text: string;
          emotion?: string | null;
        };
        store.pushEvents([
          {
            id: `dlg-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
            simulation_id: store.simulation?.id ?? "",
            event_type: "dialogue.message_created",
            source: "conversation",
            actor_entity_id: msg.speaker_id,
            target_entity_id: msg.target_id,
            location_id: null,
            scene_id: null,
            description: msg.text,
            importance: 3,
            payload: msg as unknown as Record<string, unknown>,
            created_at: new Date().toISOString(),
          } as WorldEvent,
        ]);
      } else if (type === "dialogue.npc_to_npc_message") {
        // 阶段 19：NPC-NPC 自主对话台词，气泡显示在说话者头顶
        const msg = payload as {
          speaker_id: string;
          target_id: string;
          speaker_name: string;
          text: string;
          emotion?: string | null;
          turn_index: number;
        };
        sceneRef.current?.showSpeechBubble(msg.speaker_id, msg.text);
        store.pushEvents([
          {
            id: `npc-dlg-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
            simulation_id: store.simulation?.id ?? "",
            event_type: "dialogue.npc_to_npc_message",
            source: "npc_dialogue",
            actor_entity_id: msg.speaker_id,
            target_entity_id: msg.target_id,
            location_id: null,
            scene_id: null,
            description: `${msg.speaker_name}：${msg.text}`,
            importance: 2,
            payload: msg as unknown as Record<string, unknown>,
            created_at: new Date().toISOString(),
          } as WorldEvent,
        ]);
      } else if (type === "dialogue.npc_to_npc_ended") {
        const p = payload as {
          initiator_id: string;
          target_id: string;
          turns: number;
          ended_by: string;
        };
        store.pushEvents([
          {
            id: `npc-dlg-end-${Date.now()}`,
            simulation_id: store.simulation?.id ?? "",
            event_type: "dialogue.npc_to_npc_ended",
            source: "npc_dialogue",
            actor_entity_id: p.initiator_id,
            target_entity_id: p.target_id,
            location_id: null,
            scene_id: null,
            description: `对话结束（${p.turns} 轮，${p.ended_by}）`,
            importance: 1,
            payload: p as unknown as Record<string, unknown>,
            created_at: new Date().toISOString(),
          } as WorldEvent,
        ]);
      } else if (
        type === "interaction.request_pending" ||
        type === "interaction.accepted" ||
        type === "interaction.declined" ||
        type === "interaction.cancelled"
      ) {
        // 阶段 19：交互请求事件 → 推送到事件流；ChatPanel 通过 store 监听处理
        const p = payload as {
          request_id: string;
          requester_id: string;
          target_id: string;
          status: string;
          decline_kind?: string | null;
          npc_line?: string | null;
        };
        store.pushEvents([
          {
            id: `intr-${type}-${Date.now()}`,
            simulation_id: store.simulation?.id ?? "",
            event_type: type,
            source: "interaction",
            actor_entity_id: p.requester_id,
            target_entity_id: p.target_id,
            location_id: null,
            scene_id: null,
            description: p.npc_line ?? `${type}`,
            importance: 1,
            payload: p as unknown as Record<string, unknown>,
            created_at: new Date().toISOString(),
          } as WorldEvent,
        ]);
      }
    });
    return () => {
      unsub();
      simulationSocket.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 加载当前场景的 tile / location / portal / object
  // ─── 优化策略 ────────────────────────────────────────────────────────────
  // 去掉 !sceneRef.current 守卫，让 API 预取与 Phaser 资源加载并行进行：
  //   • playerSceneId 确定后立刻开始四个 API 并发请求
  //   • 若 Phaser 已就绪：预取完成后直接渲染
  //   • 若 Phaser 未就绪：预取完成后等 sceneReady 触发此 effect 再渲染（缓存命中，0 额外请求）
  // sceneFetchingRef 防止 sceneReady 和 playerSceneId 同时触发时的重复请求。
  useEffect(() => {
    const sceneId = store.playerSceneId;
    const player = store.player;
    if (!sceneId || !player) return;  // 注意：不再等 sceneRef.current

    (async () => {
      // ── 阶段 1：若数据未缓存则预取（与 Phaser 加载并行） ─────────────────
      if (!loadedScenesRef.current.has(sceneId)) {
        // 另一个并发调用已在取同一场景，本次跳过；Phaser ready 后该调用结果会渲染
        if (sceneFetchingRef.current.has(sceneId)) return;
        sceneFetchingRef.current.add(sceneId);
        try {
          const [tiles, locations, portals, objects] = await Promise.all([
            api.listTiles(sceneId),
            api.listLocations(sceneId),
            api.listPortals(sceneId),
            api.listObjects(sceneId),
          ]);
          store.setSceneTiles(sceneId, tiles);
          store.setSceneLocations(sceneId, locations);
          store.setScenePortals(sceneId, portals);
          store.setSceneObjects(sceneId, objects);
          loadedScenesRef.current.add(sceneId);
          onSceneDataReady(sceneId);
        } catch (err) {
          console.warn("[scene] fetch failed", err);
          sceneFetchingRef.current.delete(sceneId);
          return;
        }
        sceneFetchingRef.current.delete(sceneId);
      }

      // ── 阶段 2：渲染（仅当 Phaser 就绪时）───────────────────────────────
      const scene = sceneRef.current;
      if (!scene) return;  // Phaser 尚未就绪；等 sceneReady 触发 effect 再执行本段

      // useWorldStore.getState() 总是返回 Zustand 最新状态，绕过 React 渲染周期
      // （storeRef.current 在 await 后可能仍是旧快照，导致 tiles=[]）
      const s = useWorldStore.getState();
      scene.loadDataset({
        scene: s.scenes[sceneId]!,
        tiles: s.tilesByScene[sceneId] ?? [],
        locations: s.locationsByScene[sceneId] ?? [],
        portals: s.portalsByScene[sceneId] ?? [],
        objects: s.objectsByScene[sceneId] ?? [],
        agents: s.agents,
        runtimes: s.runtimeByAgent,
        playerId: player.id,
      });
      scene.switchScene(sceneId, player.id);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store.playerSceneId, sceneReady, store.player?.id]);

  /** 场景数据（tiles/objects）取回后调用，用于驱动加载进度 */
  const onSceneDataReady = useCallback((_sceneId: string) => {
    setDataReady(true);
  }, []);

  // overlay 开关时同步到 TownScene，屏蔽 / 恢复地图交互
  useEffect(() => {
    sceneRef.current?.setInputBlocked(isOverlayOpen);
  }, [isOverlayOpen]);

  // 追踪辅助：检查是否已在交互范围，决定是继续靠近还是开对话
  const checkTrackingProximity = useCallback(() => {
    const targetId = trackingRef.current;
    if (!targetId) return;
    const s = storeRef.current;
    const playerId = s.player?.id;
    if (!playerId) return;
    const playerRt = s.runtimeByAgent[playerId];
    const npcRt = s.runtimeByAgent[targetId];
    if (!playerRt || !npcRt || playerRt.scene_id !== npcRt.scene_id) return;

    const dist =
      Math.abs(playerRt.position.x - npcRt.position.x) +
      Math.abs(playerRt.position.y - npcRt.position.y);

    if (dist <= INTERACTION_RADIUS) {
      // 进入交互半径：停止追踪，自动开启对话（仅人类 NPC）
      trackingRef.current = null;
      s.setTrackingAgent(null);
      const npcProfile = s.agents[targetId];
      if (npcProfile && npcProfile.entity_type === "human") {
        s.setPendingDialogue({ targetId, targetName: npcProfile.name });
      } else if (npcProfile && npcProfile.entity_type === "animal") {
        // 动物：直接触发抚摸交互
        api
          .interactPlayer({ entity_id: targetId, interaction_type: "pet" })
          .catch(() => null);
      }
      return;
    }

    // 尚未进入范围：限流后重新发送靠近指令（每 1.5 秒最多一次）
    const now = Date.now();
    if (now - lastApproachRef.current < 1500) return;
    lastApproachRef.current = now;
    api.approachNpc({ npc_id: targetId }).catch(() => null);
  }, []); // 无依赖：只通过 ref 读取最新状态

  // 来自 Phaser 的事件桥接
  useEffect(() => {
    return eventBus.on(async (evt) => {
      // overlay 打开期间忽略所有地图交互事件（与 TownScene._inputBlocked 双重保障）
      const overlayOpen =
        !!useWorldStore.getState().pendingDialogue || !!sceneRef.current?.isInputBlocked();
      if (
        overlayOpen &&
        (evt.type === "player.click_tile" ||
          evt.type === "player.click_object" ||
          evt.type === "player.click_agent" ||
          evt.type === "player.track_agent" ||
          evt.type === "player.stop_tracking")
      ) {
        return;
      }
      if (evt.type === "player.track_agent") {
        // 开始追踪：立刻发送第一次靠近指令
        trackingRef.current = evt.payload.agentId;
        storeRef.current.setTrackingAgent(evt.payload.agentId);
        lastApproachRef.current = 0; // 重置冷却，允许立即执行
        checkTrackingProximity();
      } else if (evt.type === "player.stop_tracking") {
        trackingRef.current = null;
        storeRef.current.setTrackingAgent(null);
      } else if (evt.type === "player.click_tile") {
        const { x, y, sceneId } = evt.payload;
        if (sceneId !== store.playerSceneId) return;
        try {
          await api.movePlayer({ target: { x, y } });
        } catch (err) {
          console.warn("[move] failed", err);
        }
      } else if (evt.type === "player.click_agent") {
        store.selectAgent(evt.payload.agentId);
      } else if (evt.type === "player.click_object") {
        storeRef.current.selectObject(evt.payload.objectId);
      } else if (evt.type === "runtime.update") {
        // 每次收到位置更新时检查追踪状态（只有玩家或目标 NPC 移动时才检查）
        const targetId = trackingRef.current;
        if (
          targetId &&
          (evt.payload.agentId === targetId ||
            evt.payload.agentId === storeRef.current.player?.id)
        ) {
          checkTrackingProximity();
        }
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store.playerSceneId, checkTrackingProximity]);

  const showLeft = leftPanelWidth > 0;
  const showFooter = footerHeight > 0;
  const rightMin = viewport.w < 520 ? 168 : 200;
  const bodyGridColumns = `${leftPanelWidth}px 6px minmax(0, 1fr) clamp(${rightMin}px, 28vw, 320px)`;

  return (
    <div style={styles.root}>
      <header style={styles.header}>
        <div style={{ fontSize: 14, fontWeight: 600 }}>AI 小镇</div>
        <TimeControl />
        <div style={{ marginLeft: "auto", display: "flex", gap: 10, alignItems: "center" }}>
          <SaveGameButton />
          <DebugToggle />
          {store.trackingAgentId && (
            <span
              style={{
                fontSize: 12,
                color: "#ffd060",
                background: "rgba(255,208,96,0.1)",
                border: "1px solid rgba(255,208,96,0.3)",
                borderRadius: 4,
                padding: "2px 8px",
                cursor: "pointer",
              }}
              title="点击取消追踪"
              onClick={() => {
                trackingRef.current = null;
                store.setTrackingAgent(null);
              }}
            >
              ↗ 追踪：{store.agents[store.trackingAgentId]?.name ?? store.trackingAgentId}
            </span>
          )}
          <span style={{ fontSize: 12, opacity: 0.6 }}>
            玩家：{store.player?.name} · 场景：
            {store.playerSceneId ? store.scenes[store.playerSceneId]?.name : "-"} · {weatherStatusText}
          </span>
        </div>
      </header>
      <div style={styles.mainColumn}>
        <div style={{ ...styles.body, gridTemplateColumns: bodyGridColumns }}>
          <aside
            style={{
              ...styles.left,
              borderRight: showLeft ? "1px solid rgba(255,255,255,0.06)" : "none",
              visibility: showLeft ? "visible" : "hidden",
            }}
            aria-hidden={!showLeft}
          >
            <AgentList />
          </aside>
          <div
            role="separator"
            aria-orientation="vertical"
            aria-valuenow={leftPanelWidth}
            aria-valuemin={0}
            aria-valuemax={leftMax}
            title="拖动调整左侧面板宽度（拖至最小可隐藏）"
            onPointerDown={onLeftResizePointerDown}
            style={{
              ...styles.resizeCol,
              cursor: "col-resize",
              touchAction: "none",
            }}
          />
          <main style={styles.center}>
            <PhaserGame
              onReady={(scene) => {
                sceneRef.current = scene;
                setSceneReady(true);
              }}
              onProgress={setPhaserProgress}
            />
            {!gameFullyReady && (
              <LoadingOverlay
                phaserProgress={phaserProgress}
                dataReady={dataReady}
              />
            )}
            {/* CSS 层阻断：overlay 打开时覆盖 Phaser canvas，
                拦截所有指针事件，防止点击穿透到地图。
                与 TownScene._inputBlocked 形成双重保障。 */}
            {isOverlayOpen && (
              <div
                aria-hidden
                style={{
                  position: "absolute",
                  inset: 0,
                  zIndex: 10,
                  cursor: "default",
                }}
              />
            )}
          </main>
          <aside style={styles.right}>
            <RightPanel onOpenMemory={(id) => setMemoryViewerFor(id)} />
          </aside>
        </div>
        <div
          role="separator"
          aria-orientation="horizontal"
          aria-valuenow={footerHeight}
          aria-valuemin={0}
          aria-valuemax={footerMax}
          title="拖动调整底部事件面板高度（拖至最小可隐藏）"
          onPointerDown={onFooterResizePointerDown}
          style={{
            ...styles.resizeRow,
            height: showFooter ? 6 : 10,
            cursor: "row-resize",
            touchAction: "none",
            flexShrink: 0,
          }}
        />
        <footer
          style={{
            ...styles.footer,
            height: footerHeight,
            borderTop: showFooter ? "1px solid rgba(255,255,255,0.06)" : "none",
            visibility: showFooter ? "visible" : "hidden",
          }}
          aria-hidden={!showFooter}
        >
          <EventLog />
        </footer>
      </div>
      <ChatPanel />
      {memoryViewerFor ? (
        <MemoryViewer agentId={memoryViewerFor} onClose={() => setMemoryViewerFor(null)} />
      ) : null}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  root: {
    width: "100%",
    height: "100%",
    display: "grid",
    gridTemplateRows: "48px minmax(0, 1fr)",
    background: "#070a10",
    color: "#eef0f2",
    minWidth: 0,
    minHeight: 0,
  },
  mainColumn: {
    display: "flex",
    flexDirection: "column",
    minWidth: 0,
    minHeight: 0,
    overflow: "hidden",
  },
  header: {
    display: "flex",
    alignItems: "center",
    gap: 18,
    padding: "0 16px",
    background: "#11161e",
    borderBottom: "1px solid rgba(255,255,255,0.06)",
  },
  body: {
    flex: 1,
    display: "grid",
    gridTemplateColumns: "240px 6px minmax(0, 1fr) clamp(200px, 28vw, 320px)",
    overflow: "hidden",
    minHeight: 0,
    minWidth: 0,
  },
  left: {
    overflow: "hidden",
    display: "flex",
    flexDirection: "column",
    background: "#0c1118",
    minWidth: 0,
  },
  center: {
    position: "relative",
    overflow: "hidden",
    background: "#0b0e12",
  },
  right: {
    borderLeft: "1px solid rgba(255,255,255,0.06)",
    overflow: "hidden",
    display: "flex",
    flexDirection: "column",
    background: "#0c1118",
    minWidth: 0,
  },
  footer: {
    overflow: "hidden",
    background: "#0a0d13",
    flexShrink: 0,
    minHeight: 0,
  },
  resizeCol: {
    background: "rgba(255,255,255,0.03)",
    width: 6,
    flexShrink: 0,
    alignSelf: "stretch",
    borderLeft: "1px solid rgba(255,255,255,0.05)",
    borderRight: "1px solid rgba(255,255,255,0.05)",
  },
  resizeRow: {
    background: "rgba(255,255,255,0.03)",
    width: "100%",
    borderTop: "1px solid rgba(255,255,255,0.05)",
    borderBottom: "1px solid rgba(255,255,255,0.05)",
    boxSizing: "border-box",
  },
};
