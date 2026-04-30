import { useEffect, useRef, useState } from "react";
import { PhaserGame } from "../game/PhaserGame";
import { TownScene } from "../game/scenes/TownScene";
import { eventBus } from "../game/eventBus";
import { simulationSocket } from "../api/websocket";
import { api } from "../api";
import { useWorldStore } from "../stores/worldStore";
import type {
  AgentRuntimeState,
  SimulationDeltaPayload,
  WorldEvent,
} from "../types/domain";
import { AgentList } from "../components/AgentList";
import { AgentPanel } from "../components/AgentPanel";
import { EventLog } from "../components/EventLog";
import { ChatPanel } from "../components/ChatPanel";
import { TimeControl } from "../components/TimeControl";
import { DebugToggle } from "../components/DebugToggle";
import { MemoryViewer } from "../components/MemoryViewer";

export function TownPage() {
  const sceneRef = useRef<TownScene | null>(null);
  const loadedScenesRef = useRef(new Set<string>());
  const [sceneReady, setSceneReady] = useState(false);
  const [memoryViewerFor, setMemoryViewerFor] = useState<string | null>(null);

  const store = useWorldStore();

  useEffect(() => {
    (async () => {
      const [agents, scenes, sim] = await Promise.all([
        api.listAgents(),
        api.listScenes(),
        api.getCurrentSimulation(),
      ]);
      store.setAgents(agents);
      store.setScenes(scenes);
      store.setSimulation(sim);
      const state = await api.getSimulationState(sim.id);
      store.setRuntimeStates(state.entities);
      const events = await api.listSimulationEvents(sim.id, 80);
      store.setEvents(events);
      simulationSocket.connect(sim.id);
    })();
    const unsub = simulationSocket.subscribe((type, payload) => {
      if (type === "simulation.delta") {
        const p = payload as SimulationDeltaPayload;
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

  // 来自 Phaser 的事件桥接
  useEffect(() => {
    return eventBus.on(async (evt) => {
      if (evt.type === "player.click_tile") {
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
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store.playerSceneId]);

  return (
    <div style={styles.root}>
      <header style={styles.header}>
        <div style={{ fontSize: 14, fontWeight: 600 }}>AI 小镇</div>
        <TimeControl />
        <div style={{ marginLeft: "auto", display: "flex", gap: 10, alignItems: "center" }}>
          <DebugToggle />
          <span style={{ fontSize: 12, opacity: 0.6 }}>
            玩家：{store.player?.name} · 场景：
            {store.playerSceneId ? store.scenes[store.playerSceneId]?.name : "-"}
          </span>
        </div>
      </header>
      <div style={styles.body}>
        <aside style={styles.left}>
          <AgentList />
        </aside>
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
      <footer style={styles.footer}>
        <EventLog />
      </footer>
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
    gridTemplateRows: "48px 1fr 150px",
    background: "#070a10",
    color: "#eef0f2",
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
    display: "grid",
    gridTemplateColumns: "240px 1fr 320px",
    overflow: "hidden",
  },
  left: {
    borderRight: "1px solid rgba(255,255,255,0.06)",
    overflow: "hidden",
    display: "flex",
    flexDirection: "column",
    background: "#0c1118",
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
  },
  footer: {
    borderTop: "1px solid rgba(255,255,255,0.06)",
    overflow: "hidden",
    background: "#0a0d13",
  },
};
