import Phaser from "phaser";
import { eventBus } from "../eventBus";
import {
  loadManifests,
  resolveAnimalColor,
  resolveHumanSprite,
  type LoadedManifests,
} from "../assets/manifest";
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

const DISPLAY_TILE = 32;
const KENNEY_TILE_KEY = "kenney_tiles";

// 兜底色（当某 terrain 既不在 Kenney 索引也不在 override 中时使用的色矩形）
const FALLBACK_TERRAIN_COLORS: Record<string, number> = {
  grass: 0x6dae51,
  road: 0xb29a71,
  bridge: 0xc59357,
  river: 0x3a8bd5,
  floor: 0xd8cfbd,
  wall: 0x4a3f35,
};

const STATE_EMOJI: Record<string, string> = {
  MOVING: "",
  INTERACTING: "💡",
  CHATTING: "💬",
  SLEEPING: "💤",
  DROWNING: "🆘",
  BLOCKED: "🚧",
  PANIC: "⚠️",
};

interface AgentNode {
  sprite: Phaser.GameObjects.Sprite | Phaser.GameObjects.Rectangle;
  label: Phaser.GameObjects.Text;
  emoji: Phaser.GameObjects.Text;
  animKind: "human" | "cat" | "dog" | "rect";
  colorKey?: string; // 动物颜色
  lastFacing: string;
  lastMoving: boolean;
}

export class TownScene extends Phaser.Scene {
  private manifests: LoadedManifests | null = null;
  private tileLayer!: Phaser.GameObjects.Container;
  private objectLayer!: Phaser.GameObjects.Container;
  private locationLayer!: Phaser.GameObjects.Container;
  private debugLayer!: Phaser.GameObjects.Container;
  private agentLayer!: Phaser.GameObjects.Container;
  private agentNodes = new Map<string, AgentNode>();
  private currentSceneId: string | null = null;
  private datasetCache = new Map<string, SceneDataset>();
  private highlight: Phaser.GameObjects.Rectangle | null = null;
  private debugState = { collision: false, hazard: true, portal: true };
  private unsubscribeBus: (() => void) | null = null;
  private assetsReady = false;
  private pendingRender: SceneDataset | null = null;
  private lastMoveAt = 0;
  private createCallback: (() => void) | null = null;

  constructor() {
    super("TownScene");
  }

  /**
   * 由 PhaserGame 在 new 出 TownScene 后注入；在 `create()` 末尾触发。
   * 避免依赖 Phaser 的 READY/CREATE 事件链被 Strict Mode 下的双挂载卡住。
   */
  setCreateCallback(cb: () => void): void {
    if (this.assetsReady) {
      cb();
    } else {
      this.createCallback = cb;
    }
  }

  /**
   * 由 PhaserGame 在 `new TownScene()` 之后、`new Phaser.Game()` 之前调用。
   * 因为 `scene: [scene]` 形式会自动启动场景，没有机会走 init() 的 data 通道，
   * 所以改用在场景实例上直接赋值。
   */
  setManifests(m: LoadedManifests | null): void {
    this.manifests = m;
  }

  preload(): void {
    this.load.once("loaderror", (f: { url?: string }) =>
      console.warn("[phaser] asset load error", f?.url),
    );
    if (!this.manifests) return;
    const m = this.manifests;
    const tiny = m.tilesets.tilesets.kenney_tiny_town;
    this.load.spritesheet(KENNEY_TILE_KEY, tiny.url, {
      frameWidth: tiny.tile_width,
      frameHeight: tiny.tile_height,
    });
    const humans = m.sprites.humans;
    for (const [id, url] of Object.entries(humans.sheets)) {
      this.load.spritesheet(this.humanKey(id), url, {
        frameWidth: humans.frame_width,
        frameHeight: humans.frame_height,
      });
    }
    this.load.spritesheet("animals_cats", m.sprites.cats.sheet_url, {
      frameWidth: m.sprites.cats.frame_width,
      frameHeight: m.sprites.cats.frame_height,
    });
    this.load.spritesheet("animals_dogs", m.sprites.dogs.sheet_url, {
      frameWidth: m.sprites.dogs.frame_width,
      frameHeight: m.sprites.dogs.frame_height,
    });
  }

  create(): void {
    this.cameras.main.setBackgroundColor("#14191f");
    this.tileLayer = this.add.container(0, 0);
    this.locationLayer = this.add.container(0, 0);
    this.objectLayer = this.add.container(0, 0);
    this.debugLayer = this.add.container(0, 0);
    this.agentLayer = this.add.container(0, 0);

    if (this.manifests) {
      try {
        this.createHumanAnimations(this.manifests);
        this.createAnimalAnimations(this.manifests);
      } catch (e) {
        console.warn("[phaser] anim create failed", e);
      }
    }
    this.assetsReady = true;

    this.input.on("pointerdown", (p: Phaser.Input.Pointer) => this.onPointerDown(p));
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

    if (this.pendingRender) {
      this.renderDataset(this.pendingRender);
      this.pendingRender = null;
    }

    if (this.createCallback) {
      this.createCallback();
      this.createCallback = null;
    }
  }

  loadDataset(dataset: SceneDataset): void {
    this.datasetCache.set(dataset.scene.id, dataset);
    if (this.currentSceneId === dataset.scene.id) {
      this.scheduleRender(dataset);
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
    this.scheduleRender(dataset);
    eventBus.emit({ type: "scene.change", payload: { sceneId } });
  }

  currentScene(): string | null {
    return this.currentSceneId;
  }

  private scheduleRender(dataset: SceneDataset): void {
    if (!this.assetsReady) {
      this.pendingRender = dataset;
      return;
    }
    this.renderDataset(dataset);
  }

  private humanKey(agentId: string): string {
    return `human_${agentId}`;
  }

  private createHumanAnimations(m: LoadedManifests): void {
    const defs = m.sprites.humans.animations;
    for (const id of Object.keys(m.sprites.humans.sheets)) {
      for (const [name, def] of Object.entries(defs)) {
        const key = `${this.humanKey(id)}_${name}`;
        if (this.anims.exists(key)) continue;
        this.anims.create({
          key,
          frames: def.frames.map((frame) => ({ key: this.humanKey(id), frame })),
          frameRate: def.frameRate,
          repeat: name.startsWith("walk") ? -1 : 0,
        });
      }
    }
  }

  private createAnimalAnimations(m: LoadedManifests): void {
    for (const kind of ["cats", "dogs"] as const) {
      const manifest = m.sprites[kind];
      const sheetKey = kind === "cats" ? "animals_cats" : "animals_dogs";
      const rowsPerDir = 2; // 每方向 2 行 × 2 帧 = 4 帧 walk
      const cols = 16; // 4 颜色 × 4 列
      for (const [colorName, off] of Object.entries(manifest.colors)) {
        for (const [dir, row] of Object.entries(manifest.direction_rows)) {
          const key = `${sheetKey}_${colorName}_walk_${dir}`;
          if (this.anims.exists(key)) continue;
          const frames: number[] = [];
          // 取 row 这一行中 color 对应的 4 列
          for (let f = 0; f < manifest.walk_frame_count; f++) {
            const col = off.col_offset + f;
            frames.push(row * cols + col);
          }
          this.anims.create({
            key,
            frames: frames.map((frame) => ({ key: sheetKey, frame })),
            frameRate: 6,
            repeat: -1,
          });
          const idleKey = `${sheetKey}_${colorName}_idle_${dir}`;
          if (!this.anims.exists(idleKey)) {
            this.anims.create({
              key: idleKey,
              frames: [{ key: sheetKey, frame: row * cols + off.col_offset }],
              frameRate: 1,
              repeat: 0,
            });
          }
        }
      }
    }
  }

  // ---------------------------------------------------------------------
  // 渲染
  // ---------------------------------------------------------------------

  private renderDataset(dataset: SceneDataset): void {
    this.tileLayer.removeAll(true);
    this.objectLayer.removeAll(true);
    this.locationLayer.removeAll(true);
    this.debugLayer.removeAll(true);
    this.agentLayer.removeAll(true);
    this.agentNodes.clear();
    this.highlight = null;

    const mapW = dataset.scene.width * DISPLAY_TILE;
    const mapH = dataset.scene.height * DISPLAY_TILE;
    this.cameras.main.setBounds(0, 0, mapW, mapH);

    this.renderTiles(dataset);
    this.renderObjects(dataset);
    this.renderLocationLabels(dataset);
    this.renderDebug();
    this.renderAgents(dataset);
    const player = dataset.playerId ? dataset.agents[dataset.playerId] : null;
    if (player) {
      const node = this.agentNodes.get(player.id);
      if (node) this.cameras.main.startFollow(node.sprite, true, 0.18, 0.18);
    }
    eventBus.emit({ type: "world.dataset.loaded", payload: { sceneId: dataset.scene.id } });
  }

  private renderTiles(dataset: SceneDataset): void {
    const m = this.manifests;
    const terrainMap = m?.tilesets.terrain_map;
    const kenney = m?.tilesets.tilesets.kenney_tiny_town;
    for (const tile of dataset.tiles) {
      const cx = tile.x * DISPLAY_TILE;
      const cy = tile.y * DISPLAY_TILE;
      let placed = false;
      if (kenney && terrainMap && terrainMap[tile.terrain] !== undefined && this.textures.exists(KENNEY_TILE_KEY)) {
        const frame = terrainMap[tile.terrain];
        if (frame >= 0 && frame < kenney.total) {
          const img = this.add.image(cx, cy, KENNEY_TILE_KEY, frame);
          img.setOrigin(0, 0);
          img.setDisplaySize(DISPLAY_TILE, DISPLAY_TILE);
          this.tileLayer.add(img);
          placed = true;
        }
      }
      if (!placed) {
        const color = FALLBACK_TERRAIN_COLORS[tile.terrain] ?? 0x6dae51;
        const rect = this.add.rectangle(cx + DISPLAY_TILE / 2, cy + DISPLAY_TILE / 2, DISPLAY_TILE, DISPLAY_TILE, color);
        this.tileLayer.add(rect);
      }
      // hazard 叠水色蒙版（tileset 没有水瓦片，用半透明叠加补齐视觉）
      if (tile.hazard_type === "deep_water") {
        const water = this.add.rectangle(cx + DISPLAY_TILE / 2, cy + DISPLAY_TILE / 2, DISPLAY_TILE, DISPLAY_TILE, 0x3a8bd5, 0.7);
        this.tileLayer.add(water);
      }
    }
  }

  private renderObjects(dataset: SceneDataset): void {
    const kenney = this.manifests?.tilesets.tilesets.kenney_tiny_town;

    for (const obj of dataset.objects) {
      const objW = obj.size?.width ?? 1;
      const objH = obj.size?.height ?? 1;
      const w = objW * DISPLAY_TILE;
      const h = objH * DISPLAY_TILE;
      const cx = obj.position.x * DISPLAY_TILE;
      const cy = obj.position.y * DISPLAY_TILE;

      const tileFrame = typeof obj.state?.tile_frame === "number" ? (obj.state.tile_frame as number) : undefined;
      const stateColor = typeof obj.state?.color === "number" ? (obj.state.color as number) : undefined;

      const onClick = (_p: Phaser.Input.Pointer, _x: number, _y: number, event?: Phaser.Types.Input.EventData) => {
        event?.stopPropagation();
        eventBus.emit({ type: "player.click_object", payload: { objectId: obj.id } });
      };

      // 1×1 对象且有 tile_frame → 用 Kenney tileset 瓦片渲染
      if (tileFrame !== undefined && objW === 1 && objH === 1 && kenney && this.textures.exists(KENNEY_TILE_KEY) && tileFrame < kenney.total) {
        const img = this.add.image(cx, cy, KENNEY_TILE_KEY, tileFrame);
        img.setOrigin(0, 0);
        img.setDisplaySize(DISPLAY_TILE, DISPLAY_TILE);
        img.setData("objectId", obj.id);
        img.setInteractive({ useHandCursor: true });
        img.on("pointerdown", onClick);
        this.objectLayer.add(img);
      } else {
        // 彩色矩形：优先用 state.color，次选根据是否阻挡的默认色
        const defaultColor = obj.blocks_movement ? 0x3d3529 : 0xc9bfa4;
        const color = stateColor ?? defaultColor;
        const alpha = obj.blocks_movement ? 0.85 : 0.75;
        const rect = this.add.rectangle(cx + w / 2, cy + h / 2, w - 4, h - 4, color, alpha);
        rect.setStrokeStyle(1, 0x000000, 0.3);
        rect.setData("objectId", obj.id);
        rect.setInteractive({ useHandCursor: true });
        rect.on("pointerdown", onClick);
        this.objectLayer.add(rect);
      }
    }
  }

  private renderLocationLabels(dataset: SceneDataset): void {
    for (const loc of dataset.locations) {
      if (loc.location_type !== "building" && loc.location_type !== "outdoor_area") continue;
      const cx = (loc.bounds.x + loc.bounds.width / 2) * DISPLAY_TILE;
      const cy = (loc.bounds.y + loc.bounds.height / 2) * DISPLAY_TILE;
      const label = this.add.text(cx, cy, loc.name, {
        fontSize: "11px",
        color: "#ffffff",
        backgroundColor: "rgba(12,16,21,0.55)",
        padding: { left: 4, right: 4, top: 1, bottom: 1 },
      });
      label.setOrigin(0.5);
      this.locationLayer.add(label);
    }
  }

  private renderDebug(): void {
    this.debugLayer.removeAll(true);
    const dataset = this.datasetCache.get(this.currentSceneId ?? "");
    if (!dataset) return;
    if (this.debugState.hazard) {
      for (const tile of dataset.tiles) {
        if (!tile.hazard_type) continue;
        const rect = this.add.rectangle(
          tile.x * DISPLAY_TILE + DISPLAY_TILE / 2,
          tile.y * DISPLAY_TILE + DISPLAY_TILE / 2,
          DISPLAY_TILE,
          DISPLAY_TILE,
          0xff4d4d,
          0.12,
        );
        this.debugLayer.add(rect);
      }
    }
    if (this.debugState.portal) {
      for (const portal of dataset.portals) {
        const marker = this.add.rectangle(
          portal.from_tile.x * DISPLAY_TILE + DISPLAY_TILE / 2,
          portal.from_tile.y * DISPLAY_TILE + DISPLAY_TILE / 2,
          DISPLAY_TILE,
          DISPLAY_TILE,
          0xffe066,
          0.28,
        );
        marker.setStrokeStyle(1.5, 0xffe066);
        this.debugLayer.add(marker);
      }
    }
    if (this.debugState.collision) {
      for (const tile of dataset.tiles) {
        if (!tile.blocks_movement) continue;
        const rect = this.add.rectangle(
          tile.x * DISPLAY_TILE + DISPLAY_TILE / 2,
          tile.y * DISPLAY_TILE + DISPLAY_TILE / 2,
          DISPLAY_TILE,
          DISPLAY_TILE,
          0x8888ff,
          0.2,
        );
        this.debugLayer.add(rect);
      }
    }
  }

  private renderAgents(dataset: SceneDataset): void {
    for (const [id, profile] of Object.entries(dataset.agents)) {
      const rt = dataset.runtimes[id];
      if (!rt || rt.scene_id !== dataset.scene.id) continue;
      this.spawnAgent(profile, rt);
    }
  }

  private spawnAgent(profile: AgentProfile, rt: AgentRuntimeState): void {
    const m = this.manifests;
    const px = rt.position.x * DISPLAY_TILE + DISPLAY_TILE / 2;
    const py = rt.position.y * DISPLAY_TILE + DISPLAY_TILE / 2;

    let sprite: Phaser.GameObjects.Sprite | Phaser.GameObjects.Rectangle;
    let animKind: AgentNode["animKind"] = "rect";
    let colorKey: string | undefined;

    if (m && (profile.entity_type === "human" || profile.entity_type === "player")) {
      const key = this.humanKey(profile.id);
      if (this.textures.exists(key)) {
        const spr = this.add.sprite(px, py, key, 18); // 18 = idle_down
        spr.setScale(m.sprites.humans.display_scale);
        spr.setOrigin(0.5, 0.75);
        sprite = spr;
        animKind = "human";
      } else {
        sprite = this.placeholderRect(px, py, profile);
      }
    } else if (m && profile.entity_type === "animal") {
      const assignment = resolveAnimalColor(m, profile.id);
      if (assignment) {
        const sheetKey = assignment.kind === "cat" ? "animals_cats" : "animals_dogs";
        if (this.textures.exists(sheetKey)) {
          const spr = this.add.sprite(px, py, sheetKey, 0);
          const manifestBlock = assignment.kind === "cat" ? m.sprites.cats : m.sprites.dogs;
          spr.setScale(manifestBlock.display_scale);
          spr.setOrigin(0.5, 0.8);
          sprite = spr;
          animKind = assignment.kind;
          colorKey = assignment.color;
        } else {
          sprite = this.placeholderRect(px, py, profile);
        }
      } else {
        sprite = this.placeholderRect(px, py, profile);
      }
    } else {
      sprite = this.placeholderRect(px, py, profile);
    }

    sprite.setInteractive({ useHandCursor: true });
    sprite.setData("agentId", profile.id);
    sprite.on(
      "pointerdown",
      (_p: Phaser.Input.Pointer, _x: number, _y: number, event?: Phaser.Types.Input.EventData) => {
        event?.stopPropagation();
        eventBus.emit({ type: "player.click_agent", payload: { agentId: profile.id } });
      },
    );

    // 标签底部需高于 sprite 顶部（py - spriteTopOffset），再留 6px 间隙。
    // spriteTopOffset = frame_height × display_scale × origin_y
    //   = 64 × 0.75 × 0.75 = 36px  →  LABEL_ABOVE = 42px ≈ DISPLAY_TILE * 1.3
    const SPRITE_ORIGIN_Y = 0.75;
    const spriteTopOffset = m
      ? Math.ceil(m.sprites.humans.frame_height * m.sprites.humans.display_scale * SPRITE_ORIGIN_Y)
      : DISPLAY_TILE;
    const LABEL_ABOVE = spriteTopOffset + 6;
    const label = this.add.text(px, py - LABEL_ABOVE, profile.name, {
      fontSize: "11px",
      color: "#ffffff",
      backgroundColor: "rgba(0,0,0,0.55)",
      padding: { left: 3, right: 3 },
    });
    label.setOrigin(0.5, 1);
    // emoji 底部与标签底部同高，向右偏移，令其出现在名签右侧而非覆盖头部
    const emoji = this.add.text(
      px + DISPLAY_TILE / 2 - 3,
      py - LABEL_ABOVE,
      STATE_EMOJI[rt.state] ?? "",
      { fontSize: "12px" },
    );
    emoji.setOrigin(0, 1);

    this.agentLayer.add([sprite as Phaser.GameObjects.GameObject, label, emoji]);
    const node: AgentNode = {
      sprite,
      label,
      emoji,
      animKind,
      colorKey,
      lastFacing: rt.facing || "down",
      lastMoving: rt.state === "MOVING",
    };
    this.playAgentAnim(profile.id, node, rt.state, rt.facing || "down");
    this.agentNodes.set(profile.id, node);
  }

  private placeholderRect(x: number, y: number, profile: AgentProfile): Phaser.GameObjects.Rectangle {
    const color = Phaser.Display.Color.HexStringToColor(profile.appearance?.color ?? "#ffffff").color;
    const rect = this.add.rectangle(x, y, DISPLAY_TILE - 6, DISPLAY_TILE - 6, color, 1);
    rect.setStrokeStyle(2, 0x000000, 0.4);
    return rect;
  }

  private playAgentAnim(
    agentId: string,
    node: AgentNode,
    state: string,
    facing: string,
  ): void {
    if (!(node.sprite instanceof Phaser.GameObjects.Sprite)) return;
    const isMoving = state === "MOVING";
    const dir = normalizeFacing(facing);
    if (node.animKind === "human") {
      const base = this.humanKey(agentId);
      const key = `${base}_${isMoving ? "walk" : "idle"}_${dir}`;
      if (this.anims.exists(key)) {
        node.sprite.play(key, true);
      }
    } else if (node.animKind === "cat" || node.animKind === "dog") {
      const color = node.colorKey ?? "white";
      const kindKey = node.animKind === "cat" ? "animals_cats" : "animals_dogs";
      const key = `${kindKey}_${color}_${isMoving ? "walk" : "idle"}_${dir}`;
      if (this.anims.exists(key)) {
        node.sprite.play(key, true);
      }
    }
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
    if (payload.sceneId !== this.currentSceneId) {
      const node = this.agentNodes.get(payload.agentId);
      if (node) {
        node.sprite.destroy();
        node.label.destroy();
        node.emoji.destroy();
        this.agentNodes.delete(payload.agentId);
      }
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

    const targetX = payload.x * DISPLAY_TILE + DISPLAY_TILE / 2;
    const targetY = payload.y * DISPLAY_TILE + DISPLAY_TILE / 2;
    this.tweens.add({
      targets: node.sprite,
      x: targetX,
      y: targetY,
      duration: 260,
      ease: "Sine.easeInOut",
    });
    const m2 = this.manifests;
    const SPRITE_ORIGIN_Y2 = 0.75;
    const spriteTopOffset2 = m2
      ? Math.ceil(m2.sprites.humans.frame_height * m2.sprites.humans.display_scale * SPRITE_ORIGIN_Y2)
      : DISPLAY_TILE;
    const labelAbove2 = spriteTopOffset2 + 6;
    this.tweens.add({
      targets: node.label,
      x: targetX,
      y: targetY - labelAbove2,
      duration: 260,
      ease: "Sine.easeInOut",
    });
    this.tweens.add({
      targets: node.emoji,
      x: targetX + DISPLAY_TILE / 2 - 3,
      y: targetY - labelAbove2,
      duration: 260,
      ease: "Sine.easeInOut",
    });
    node.emoji.setText(STATE_EMOJI[payload.state] ?? "");
    const facing = payload.facing || node.lastFacing;
    this.playAgentAnim(payload.agentId, node, payload.state, facing);
    node.lastFacing = facing;
    node.lastMoving = payload.state === "MOVING";
  }

  // ---------------------------------------------------------------------
  // 输入
  // ---------------------------------------------------------------------

  private onPointerDown(pointer: Phaser.Input.Pointer): void {
    if (!this.currentSceneId) return;
    const worldX = this.cameras.main.worldView.x + pointer.x;
    const worldY = this.cameras.main.worldView.y + pointer.y;
    const tx = Math.floor(worldX / DISPLAY_TILE);
    const ty = Math.floor(worldY / DISPLAY_TILE);
    if (tx < 0 || ty < 0) return;
    this.showHighlight(tx, ty);
    eventBus.emit({ type: "player.click_tile", payload: { x: tx, y: ty, sceneId: this.currentSceneId } });
  }

  private onKeyDown(event: KeyboardEvent): void {
    const now = Date.now();
    if (now - this.lastMoveAt < 120) return;
    const map: Record<string, "up" | "down" | "left" | "right"> = {
      ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right",
      w: "up", s: "down", a: "left", d: "right",
      W: "up", S: "down", A: "left", D: "right",
    };
    const dir = map[event.key];
    if (!dir) return;
    this.lastMoveAt = now;
    if (!this.currentSceneId) return;
    const dataset = this.datasetCache.get(this.currentSceneId);
    if (!dataset?.playerId) return;
    const node = this.agentNodes.get(dataset.playerId);
    if (!node) return;
    const px = Math.floor(node.sprite.x / DISPLAY_TILE);
    const py = Math.floor(node.sprite.y / DISPLAY_TILE);
    const delta = { up: [0, -1], down: [0, 1], left: [-1, 0], right: [1, 0] }[dir]!;
    eventBus.emit({
      type: "player.click_tile",
      payload: { x: px + delta[0], y: py + delta[1], sceneId: this.currentSceneId },
    });
  }

  private showHighlight(x: number, y: number): void {
    if (this.highlight) this.highlight.destroy();
    this.highlight = this.add.rectangle(
      x * DISPLAY_TILE + DISPLAY_TILE / 2,
      y * DISPLAY_TILE + DISPLAY_TILE / 2,
      DISPLAY_TILE,
      DISPLAY_TILE,
      0xffffff,
      0.2,
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

function normalizeFacing(raw: string): "up" | "down" | "left" | "right" {
  if (raw === "up" || raw === "down" || raw === "left" || raw === "right") return raw;
  return "down";
}
