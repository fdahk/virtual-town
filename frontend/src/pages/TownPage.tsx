import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PhaserGame } from "../game/PhaserGame";
import { TownScene } from "../game/scenes/TownScene";
import { eventBus } from "../game/eventBus";
import { simulationSocket } from "../api/websocket";
import { api } from "../api";
import { useWorldStore } from "../stores/worldStore";
import type { SimulationDeltaPayload, WorldEvent } from "../types/domain";
import { AgentList } from "../components/AgentList";
import { AgentPanel } from "../components/AgentPanel";
import { EventLog } from "../components/EventLog";
import { ChatPanel } from "../components/ChatPanel";
import { TimeControl } from "../components/TimeControl";
import { DebugToggle } from "../components/DebugToggle";
import { MemoryViewer } from "../components/MemoryViewer";

function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, n));
}

const INTERACTION_RADIUS = 2; // 与后端 player_service.INTERACTION_RADIUS 保持一致

export function TownPage() {
  const sceneRef = useRef<TownScene | null>(null);
  const loadedScenesRef = useRef(new Set<string>());
  const [sceneReady, setSceneReady] = useState(false);
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
    const unsub = simulationSocket.subscribe((type, payload) => {
      if (type === "simulation.delta") {
        const p = payload as SimulationDeltaPayload;
        store.patchSimulationClock(p.world_time, p.step);
        store.setRuntimeStates(p.entity_updates);
        if (p.events.length) store.pushEvents(p.events);
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
      }
    });
    return () => {
      unsub();
      simulationSocket.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 加载当前场景的 tile / location / portal / object
  useEffect(() => {
    const sceneId = store.playerSceneId;
    const player = store.player;
    if (!sceneId || !player || !sceneRef.current) return;
    (async () => {
      if (!loadedScenesRef.current.has(sceneId)) {
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
      }
      const scene = sceneRef.current!;
      const tiles = store.tilesByScene[sceneId] ?? (await api.listTiles(sceneId));
      const locations = store.locationsByScene[sceneId] ?? [];
      const portals = store.portalsByScene[sceneId] ?? [];
      const objects = store.objectsByScene[sceneId] ?? [];
      scene.loadDataset({
        scene: store.scenes[sceneId]!,
        tiles,
        locations,
        portals,
        objects,
        agents: store.agents,
        runtimes: store.runtimeByAgent,
        playerId: player.id,
      });
      scene.switchScene(sceneId, player.id);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store.playerSceneId, sceneReady, store.player?.id]);

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
        // 简单默认交互：inspect
        try {
          await api.interactPlayer({
            object_id: evt.payload.objectId,
            interaction_type: "inspect",
          });
        } catch (err) {
          const code = (err as { code?: string }).code;
          if (code !== "OUT_OF_RANGE") console.warn("[interact] failed", err);
        }
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
            {store.playerSceneId ? store.scenes[store.playerSceneId]?.name : "-"}
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
            />
          </main>
          <aside style={styles.right}>
            <AgentPanel
              onOpenMemory={(id) => setMemoryViewerFor(id)}
            />
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
