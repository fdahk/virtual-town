import Phaser from "phaser";
import { eventBus } from "../eventBus";
import type {
  AgentProfile,
  AgentRuntimeState,
  Location,
  MapScene,
  MapTile,
  Portal,
  WorldObject,
} from "../../types/domain";

interface SceneDataset {
  scene: MapScene;
  tiles: MapTile[];
  locations: Location[];
  portals: Portal[];
  objects: WorldObject[];
  agents: Record<string, AgentProfile>;
  runtimes: Record<string, AgentRuntimeState>;
  playerId: string | null;
}

// 瓦片颜色表。MVP 使用纯色矩形，避免依赖外部像素资源。
const TERRAIN_COLORS: Record<string, number> = {
  grass: 0x6dae51,
  road: 0xb29a71,
  bridge: 0xc59357,
  river: 0x3a8bd5,
  floor: 0xd8cfbd,
  wall: 0x4a3f35,
  cafe_ext: 0xa46a5a,
  school_ext: 0xb5a25c,
  grocery_ext: 0xa66e8a,
  flower_ext: 0x9a7fbd,
  cafe_door: 0xf4d06f,
  school_door: 0xf4d06f,
  grocery_door: 0xf4d06f,
  flower_door: 0xf4d06f,
};

const ENTITY_DEFAULT_COLOR: Record<string, number> = {
  human: 0xffc2c2,
  animal: 0xd9b26a,
  player: 0xffffff,
};

const STATE_TO_EMOJI: Record<string, string> = {
  IDLE: "",
  MOVING: "",
  INTERACTING: "💡",
  CHATTING: "💬",
  SLEEPING: "💤",
  DROWNING: "🆘",
  BLOCKED: "🚧",
  PANIC: "⚠️",
};

export class TownScene extends Phaser.Scene {
  private tileSize = 32;
  private mapWidth = 0;
  private mapHeight = 0;
  private tileLayer!: Phaser.GameObjects.Container;
  private objectLayer!: Phaser.GameObjects.Container;
  private locationLayer!: Phaser.GameObjects.Container;
  private debugLayer!: Phaser.GameObjects.Container;
  private agentLayer!: Phaser.GameObjects.Container;
  private agentNodes = new Map<
    string,
    {
      body: Phaser.GameObjects.Rectangle;
      label: Phaser.GameObjects.Text;
      emoji: Phaser.GameObjects.Text;
    }
  >();
  private currentSceneId: string | null = null;
  private datasetCache = new Map<string, SceneDataset>();
  private playerSprite: Phaser.GameObjects.Rectangle | null = null;
  private highlight: Phaser.GameObjects.Rectangle | null = null;
  private debugState = {
    collision: false,
    hazard: true,
    portal: true,
  };
  private unsubscribeBus: (() => void) | null = null;
  private cursorKeys?: Phaser.Types.Input.Keyboard.CursorKeys;
  private lastMoveAt = 0;
  private _onCreateCallback?: () => void;

  constructor() {
    super("TownScene");
  }

  /** Register a callback to be invoked at the end of create(). */
  setCreateCallback(cb: () => void): void {
    this._onCreateCallback = cb;
  }

  create(): void {
    this.cameras.main.setBackgroundColor("#0c1015");
    this.tileLayer = this.add.container(0, 0);
    this.locationLayer = this.add.container(0, 0);
    this.objectLayer = this.add.container(0, 0);
    this.debugLayer = this.add.container(0, 0);
    this.agentLayer = this.add.container(0, 0);

    this.input.on("pointerdown", (p: Phaser.Input.Pointer) => this.onPointerDown(p));
    this.cursorKeys = this.input.keyboard?.createCursorKeys();
    this.input.keyboard?.on("keydown", this.onKeyDown, this);

    this.unsubscribeBus = eventBus.on((evt) => {
      if (evt.type === "runtime.update") this.onRuntimeUpdate(evt.payload);
      if (evt.type === "debug.layer.toggle") {
        this.debugState[evt.payload.layer] = evt.payload.enabled;
        this.renderDebug();
      }
    });
    this.events.once(Phaser.Scenes.Events.SHUTDOWN, () => {
      this.unsubscribeBus?.();
    });

    // Notify PhaserGame that the scene is fully initialised and ready to
    // receive data (all containers exist at this point).
    this._onCreateCallback?.();
  }

  loadDataset(dataset: SceneDataset): void {
    this.datasetCache.set(dataset.scene.id, dataset);
    if (this.currentSceneId === dataset.scene.id) {
      this.renderDataset(dataset);
    } else if (this.currentSceneId === null) {
      this.switchScene(dataset.scene.id, dataset.playerId ?? null);
    }
  }

  switchScene(sceneId: string, playerId: string | null): void {
    if (this.currentSceneId === sceneId) return;
    const dataset = this.datasetCache.get(sceneId);
    if (!dataset) return;
    this.currentSceneId = sceneId;
    if (playerId) dataset.playerId = playerId;
    this.renderDataset(dataset);
    eventBus.emit({ type: "scene.change", payload: { sceneId } });
  }

  currentScene(): string | null {
    return this.currentSceneId;
  }

  private renderDataset(dataset: SceneDataset): void {
    this.tileLayer.removeAll(true);
    this.objectLayer.removeAll(true);
    this.locationLayer.removeAll(true);
    this.debugLayer.removeAll(true);
    this.agentLayer.removeAll(true);
    this.agentNodes.clear();
    this.playerSprite = null;
    this.highlight = null;

    this.tileSize = dataset.scene.tile_size || 32;
    this.mapWidth = dataset.scene.width * this.tileSize;
    this.mapHeight = dataset.scene.height * this.tileSize;

    this.cameras.main.setBounds(0, 0, this.mapWidth, this.mapHeight);

    // 1. tile 层
    for (const tile of dataset.tiles) {
      const color = TERRAIN_COLORS[tile.terrain] ?? 0x6dae51;
      const rect = this.add.rectangle(
        tile.x * this.tileSize + this.tileSize / 2,
        tile.y * this.tileSize + this.tileSize / 2,
        this.tileSize,
        this.tileSize,
        color,
      );
      rect.setStrokeStyle(1, 0x000000, 0.05);
      this.tileLayer.add(rect);
    }

    // 2. 地点名字（仅建筑/户外区域）
    for (const loc of dataset.locations) {
      if (loc.location_type !== "building" && loc.location_type !== "outdoor_area") continue;
      const cx = (loc.bounds.x + loc.bounds.width / 2) * this.tileSize;
      const cy = (loc.bounds.y + loc.bounds.height / 2) * this.tileSize;
      const label = this.add.text(cx, cy, loc.name, {
        fontSize: "11px",
        color: "#f5f5f5",
        backgroundColor: "rgba(12,16,21,0.45)",
        padding: { left: 4, right: 4, top: 1, bottom: 1 },
      });
      label.setOrigin(0.5);
      this.locationLayer.add(label);
    }

    // 3. 物体
    for (const obj of dataset.objects) {
      const w = (obj.size?.width ?? 1) * this.tileSize;
      const h = (obj.size?.height ?? 1) * this.tileSize;
      const color = obj.blocks_movement ? 0x3d3529 : 0xc9bfa4;
      const rect = this.add.rectangle(
        obj.position.x * this.tileSize + w / 2,
        obj.position.y * this.tileSize + h / 2,
        w - 4,
        h - 4,
        color,
        0.85,
      );
      rect.setStrokeStyle(1, 0x000000, 0.25);
      rect.setData("objectId", obj.id);
      rect.setInteractive({ useHandCursor: true });
      rect.on("pointerdown", (pointer: Phaser.Input.Pointer, _x: number, _y: number, event?: Phaser.Types.Input.EventData) => {
        event?.stopPropagation();
        eventBus.emit({ type: "player.click_object", payload: { objectId: obj.id } });
      });
      this.objectLayer.add(rect);
    }

    // 4. debug 层
    this.renderDebug();

    // 5. Agent 层
    for (const [id, profile] of Object.entries(dataset.agents)) {
      const rt = dataset.runtimes[id];
      if (!rt || rt.scene_id !== dataset.scene.id) continue;
      this.spawnAgent(profile, rt);
    }
    const player = dataset.playerId ? dataset.agents[dataset.playerId] : null;
    if (player) {
      const rt = dataset.runtimes[player.id];
      if (rt && rt.scene_id === dataset.scene.id) {
        const node = this.agentNodes.get(player.id);
        if (node) {
          this.playerSprite = node.body;
          this.cameras.main.startFollow(node.body, true, 0.15, 0.15);
        }
      }
    }
    eventBus.emit({ type: "world.dataset.loaded", payload: { sceneId: dataset.scene.id } });
  }

  private renderDebug(): void {
    this.debugLayer.removeAll(true);
    const dataset = this.datasetCache.get(this.currentSceneId ?? "");
    if (!dataset) return;
    if (this.debugState.hazard) {
      for (const tile of dataset.tiles) {
        if (!tile.hazard_type) continue;
        const rect = this.add.rectangle(
          tile.x * this.tileSize + this.tileSize / 2,
          tile.y * this.tileSize + this.tileSize / 2,
          this.tileSize,
          this.tileSize,
          0xff4d4d,
          0.15,
        );
        this.debugLayer.add(rect);
      }
    }
    if (this.debugState.portal) {
      for (const portal of dataset.portals) {
        const marker = this.add.rectangle(
          portal.from_tile.x * this.tileSize + this.tileSize / 2,
          portal.from_tile.y * this.tileSize + this.tileSize / 2,
          this.tileSize,
          this.tileSize,
          0xffe066,
          0.35,
        );
        marker.setStrokeStyle(2, 0xffe066);
        this.debugLayer.add(marker);
      }
    }
    if (this.debugState.collision) {
      for (const tile of dataset.tiles) {
        if (!tile.blocks_movement) continue;
        const rect = this.add.rectangle(
          tile.x * this.tileSize + this.tileSize / 2,
          tile.y * this.tileSize + this.tileSize / 2,
          this.tileSize,
          this.tileSize,
          0x8888ff,
          0.25,
        );
        this.debugLayer.add(rect);
      }
    }
  }

  private spawnAgent(profile: AgentProfile, rt: AgentRuntimeState): void {
    const size = this.tileSize - 6;
    const color = this.pickColor(profile);
    const x = rt.position.x * this.tileSize + this.tileSize / 2;
    const y = rt.position.y * this.tileSize + this.tileSize / 2;
    const body = this.add.rectangle(x, y, size, size, color, 1);
    body.setStrokeStyle(2, 0x000000, 0.4);
    body.setInteractive({ useHandCursor: true });
    body.setData("agentId", profile.id);
    body.on("pointerdown", (_p: Phaser.Input.Pointer, _x: number, _y: number, event?: Phaser.Types.Input.EventData) => {
      event?.stopPropagation();
      eventBus.emit({ type: "player.click_agent", payload: { agentId: profile.id } });
    });
    const label = this.add.text(x, y - this.tileSize / 2 - 2, profile.name, {
      fontSize: "11px",
      color: "#ffffff",
      backgroundColor: "rgba(0,0,0,0.4)",
      padding: { left: 3, right: 3 },
    });
    label.setOrigin(0.5, 1);
    const emoji = this.add.text(x + this.tileSize / 2 - 6, y - this.tileSize / 2, STATE_TO_EMOJI[rt.state] || "", {
      fontSize: "12px",
    });
    emoji.setOrigin(1, 0);
    this.agentLayer.add([body, label, emoji]);
    this.agentNodes.set(profile.id, { body, label, emoji });
  }

  private pickColor(profile: AgentProfile): number {
    const raw = profile.appearance?.color;
    if (raw && raw.startsWith("#")) {
      return Phaser.Display.Color.HexStringToColor(raw).color;
    }
    return ENTITY_DEFAULT_COLOR[profile.entity_type] ?? 0xffffff;
  }

  private onRuntimeUpdate(payload: {
    agentId: string;
    sceneId: string;
    x: number;
    y: number;
    state: string;
    facing?: string;
  }): void {
    if (!this.currentSceneId) return;
    const dataset = this.datasetCache.get(this.currentSceneId);
    if (!dataset) return;
    // 若 agent 切换了场景
    if (payload.sceneId !== this.currentSceneId) {
      const node = this.agentNodes.get(payload.agentId);
      if (node) {
        node.body.destroy();
        node.label.destroy();
        node.emoji.destroy();
        this.agentNodes.delete(payload.agentId);
      }
      // 若主角离开了本场景则切换到新场景
      if (dataset.playerId === payload.agentId) {
        this.switchScene(payload.sceneId, payload.agentId);
      }
      return;
    }
    let node = this.agentNodes.get(payload.agentId);
    if (!node) {
      const profile = dataset.agents[payload.agentId];
      if (!profile) return;
      const rt: AgentRuntimeState = {
        agent_id: payload.agentId,
        scene_id: payload.sceneId,
        position: { x: payload.x, y: payload.y },
        state: payload.state as AgentRuntimeState["state"],
        emotion: null,
        energy: 1,
        hunger: 0,
        social_need: null,
        fear: null,
        status_effects: [],
        current_action_id: null,
        current_goal: null,
        facing: payload.facing || "down",
        updated_at: new Date().toISOString(),
      };
      this.spawnAgent(profile, rt);
      node = this.agentNodes.get(payload.agentId);
      if (!node) return;
    }
    const targetX = payload.x * this.tileSize + this.tileSize / 2;
    const targetY = payload.y * this.tileSize + this.tileSize / 2;
    this.tweens.add({
      targets: node.body,
      x: targetX,
      y: targetY,
      duration: 250,
      ease: "Sine.easeInOut",
    });
    this.tweens.add({
      targets: node.label,
      x: targetX,
      y: targetY - this.tileSize / 2 - 2,
      duration: 250,
      ease: "Sine.easeInOut",
    });
    this.tweens.add({
      targets: node.emoji,
      x: targetX + this.tileSize / 2 - 6,
      y: targetY - this.tileSize / 2,
      duration: 250,
      ease: "Sine.easeInOut",
    });
    node.emoji.setText(STATE_TO_EMOJI[payload.state] || "");
    if (dataset.playerId && dataset.playerId === payload.agentId && this.playerSprite === null) {
      this.playerSprite = node.body;
      this.cameras.main.startFollow(node.body, true, 0.15, 0.15);
    }
  }

  private onPointerDown(pointer: Phaser.Input.Pointer): void {
    if (!this.currentSceneId) return;
    // 忽略 UI 元素点击（已 stopPropagation）
    const worldX = this.cameras.main.worldView.x + pointer.x;
    const worldY = this.cameras.main.worldView.y + pointer.y;
    const tx = Math.floor(worldX / this.tileSize);
    const ty = Math.floor(worldY / this.tileSize);
    if (tx < 0 || ty < 0) return;
    this.showHighlight(tx, ty);
    eventBus.emit({
      type: "player.click_tile",
      payload: { x: tx, y: ty, sceneId: this.currentSceneId },
    });
  }

  private onKeyDown(event: KeyboardEvent): void {
    const now = Date.now();
    if (now - this.lastMoveAt < 120) return;
    const map: Record<string, "up" | "down" | "left" | "right"> = {
      ArrowUp: "up",
      ArrowDown: "down",
      ArrowLeft: "left",
      ArrowRight: "right",
      w: "up",
      s: "down",
      a: "left",
      d: "right",
      W: "up",
      S: "down",
      A: "left",
      D: "right",
    };
    const dir = map[event.key];
    if (!dir) return;
    this.lastMoveAt = now;
    // emit as "click_tile" offset：更通用由上层处理
    const player = this.playerSprite;
    if (!player) return;
    const px = Math.floor(player.x / this.tileSize);
    const py = Math.floor(player.y / this.tileSize);
    const delta = { up: [0, -1], down: [0, 1], left: [-1, 0], right: [1, 0] }[dir]!;
    eventBus.emit({
      type: "player.click_tile",
      payload: { x: px + delta[0], y: py + delta[1], sceneId: this.currentSceneId ?? "" },
    });
  }

  private showHighlight(x: number, y: number): void {
    if (this.highlight) this.highlight.destroy();
    this.highlight = this.add.rectangle(
      x * this.tileSize + this.tileSize / 2,
      y * this.tileSize + this.tileSize / 2,
      this.tileSize,
      this.tileSize,
      0xffffff,
      0.15,
    );
    this.highlight.setStrokeStyle(2, 0xffffff, 0.7);
    this.tweens.add({
      targets: this.highlight,
      alpha: 0,
      duration: 600,
      onComplete: () => {
        this.highlight?.destroy();
        this.highlight = null;
      },
    });
  }
}
