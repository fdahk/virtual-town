import Phaser from "phaser";
import { eventBus, type NaturalEffectPayload } from "../eventBus";
import {
  loadManifests,
  resolveAnimalColor,
  resolveHumanSprite,
  type LoadedManifests,
} from "../assets/manifest";
import { BubbleManager, mapStateToBubble } from "../components/BubbleManager";
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
const KENNEY_DUNGEON_KEY = "kenney_dungeon";

// 兜底色（当某 terrain 既不在 Kenney 索引也不在 override 中时使用的色矩形）
const FALLBACK_TERRAIN_COLORS: Record<string, number> = {
  grass: 0x6dae51,
  road: 0xb29a71,
  bridge: 0xc59357,
  river: 0x3a8bd5,
  floor: 0xd8cfbd,
  wall: 0x4a3f35,
};

interface AgentNode {
  sprite: Phaser.GameObjects.Sprite | Phaser.GameObjects.Rectangle;
  label: Phaser.GameObjects.Text;
  animKind: "human" | "cat" | "dog" | "rect";
  colorKey?: string; // 动物颜色
  lastFacing: string;
  lastMoving: boolean;
}

/** 单个自然效果节点（程序化绘制，可 tween 动画）。 */
interface EffectNode {
  kind: string;
  gfx: Phaser.GameObjects.Graphics | Phaser.GameObjects.Rectangle;
  tween?: Phaser.Tweens.Tween;
}

export class TownScene extends Phaser.Scene {
  private manifests: LoadedManifests | null = null;
  private tileLayer!: Phaser.GameObjects.Container;
  private objectLayer!: Phaser.GameObjects.Container;
  private locationLayer!: Phaser.GameObjects.Container;
  private debugLayer!: Phaser.GameObjects.Container;
  private agentLayer!: Phaser.GameObjects.Container;
  /** 阶段 19：富气泡层（位于 agentLayer 之上） */
  private bubbleLayer!: Phaser.GameObjects.Container;
  private bubbles!: BubbleManager;
  /** 自然事件视觉效果层（绘制在 agentLayer 之上） */
  private effectLayer!: Phaser.GameObjects.Container;
  /** 天气叠加层（覆盖整个视窗的半透明色彩） */
  private weatherOverlay!: Phaser.GameObjects.Rectangle;
  private agentNodes = new Map<string, AgentNode>();
  /** objectId → 效果节点（火焰/萤火虫/涟漪等） */
  private effectNodes = new Map<string, EffectNode>();
  private currentSceneId: string | null = null;
  private datasetCache = new Map<string, SceneDataset>();
  private highlight: Phaser.GameObjects.Rectangle | null = null;
  private debugState = { collision: false, hazard: true, portal: true };
  private unsubscribeBus: (() => void) | null = null;
  private assetsReady = false;
  private pendingRender: SceneDataset | null = null;
  private lastMoveAt = 0;
  private createCallback: (() => void) | null = null;
  private progressCallback: ((value: number) => void) | null = null;
  /** 当 React 层有 overlay（对话框、记忆查看器等）时置为 true，屏蔽所有地图交互。 */
  private _inputBlocked = false;

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

  setProgressCallback(cb: (value: number) => void): void {
    this.progressCallback = cb;
  }

  /**
   * 当 React 侧出现 overlay（对话框、记忆查看器等）时调用 setInputBlocked(true)，
   * overlay 关闭后调用 setInputBlocked(false)。
   *
   * 双重屏蔽策略：
   *  1. Phaser 的 InputPlugin / KeyboardPlugin 关闭 → pointerdown / keydown 事件不派发
   *  2. _inputBlocked 内部标志 → onPointerDown / onKeyDown 开头的守卫（防御性兜底）
   *
   * CSS 层的 pointer-events 阻断在 TownPage 的 Phaser 容器上额外叠加。
   */
  setInputBlocked(blocked: boolean): void {
    this._inputBlocked = blocked;
    this.input.enabled = !blocked;
    if (this.input.keyboard) {
      this.input.keyboard.enabled = !blocked;
    }
  }

  isInputBlocked(): boolean {
    return this._inputBlocked;
  }

  preload(): void {
    this.load.once("loaderror", (f: { url?: string }) =>
      console.warn("[phaser] asset load error", f?.url),
    );
    // 将 Phaser 内置的 0-1 加载进度上报给 React（用于进度条显示）
    this.load.on("progress", (value: number) => {
      this.progressCallback?.(value);
    });
    if (!this.manifests) return;
    const m = this.manifests;
    const tiny = m.tilesets.tilesets.kenney_tiny_town;
    this.load.spritesheet(KENNEY_TILE_KEY, tiny.url, {
      frameWidth: tiny.tile_width,
      frameHeight: tiny.tile_height,
    });
    const dungeon = m.tilesets.tilesets.kenney_tiny_dungeon;
    if (dungeon) {
      this.load.spritesheet(KENNEY_DUNGEON_KEY, dungeon.url, {
        frameWidth: dungeon.tile_width,
        frameHeight: dungeon.tile_height,
      });
    }
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
    this.bubbleLayer = this.add.container(0, 0);
    this.bubbleLayer.setDepth(900);
    this.bubbles = new BubbleManager(this, this.bubbleLayer);
    this.effectLayer = this.add.container(0, 0);
    // 全屏天气叠加层（默认完全透明）
    this.weatherOverlay = this.add.rectangle(0, 0, 4096, 4096, 0x000000, 0).setOrigin(0, 0);
    this.weatherOverlay.setDepth(1000); // 始终置顶

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
      // 自然事件视觉效果
      if (evt.type === "world.fire_started" || evt.type === "world.fire_spread") {
        this.addEffect("fire", evt.payload);
      } else if (evt.type === "world.fire_extinguished") {
        this.removeEffect(evt.payload.object_id ?? "");
      } else if (evt.type === "weather.condition_changed") {
        this.applyWeatherOverlay(evt.payload.condition ?? "sunny", evt.payload.intensity ?? 0);
      } else if (evt.type === "weather.thunder") {
        this.flashThunder();
      } else if (evt.type === "nature.fishing_spot_appeared") {
        this.addEffect("fishing", evt.payload);
      } else if (evt.type === "nature.mushroom_appeared") {
        this.addEffect("mushroom", evt.payload);
      } else if (evt.type === "nature.mushroom_withered") {
        this.removeEffect(evt.payload.object_id ?? "");
      } else if (evt.type === "nature.flower_bloomed") {
        this.addEffect("flower", evt.payload);
      } else if (evt.type === "nature.flower_withered") {
        this.removeEffect(evt.payload.object_id ?? "");
      } else if (evt.type === "nature.firefly_appeared") {
        this.addEffect("firefly", evt.payload);
      } else if (evt.type === "nature.bird_appeared") {
        this.addEffect("bird", evt.payload);
      } else if (evt.type === "nature.bird_flew_away") {
        this.removeEffect(evt.payload.object_id ?? "");
      } else if (evt.type === "nature.puddle_formed") {
        this.addEffect("puddle", evt.payload);
      } else if (evt.type === "nature.puddle_dried") {
        this.removeEffect(evt.payload.object_id ?? "");
      } else if (evt.type === "nature.fruit_ripened") {
        this.addEffect("fruit", evt.payload);
      } else if (evt.type === "nature.mushroom_picked"
              || evt.type === "nature.fruit_picked"
              || evt.type === "nature.fish_caught"
              || evt.type === "world.object_despawned") {
        // 物品被采摘/钓走/移除：清理对应的视觉叠加层
        this.removeEffect(evt.payload.object_id ?? "");
      } else if (evt.type === "world.storm_warning") {
        this.applyWeatherOverlay("stormy", 1.0);
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

  /**
   * 局部刷新 objectLayer，不重建 tile/agent 层。
   * 由 TownPage 在收到 world.object_state_changed / spawned / despawned 时调用。
   */
  refreshObjects(objects: WorldObject[]): void {
    const sceneId = this.currentSceneId;
    if (!sceneId) return;
    const dataset = this.datasetCache.get(sceneId);
    if (!dataset) return;
    dataset.objects = objects;
    if (!this.assetsReady) return;
    this.objectLayer.removeAll(true);
    this.renderObjects(dataset);
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
    const dungeon = this.manifests?.tilesets.tilesets.kenney_tiny_dungeon;

    for (const obj of dataset.objects) {
      const objW = obj.size?.width ?? 1;
      const objH = obj.size?.height ?? 1;
      const w = objW * DISPLAY_TILE;
      const h = objH * DISPLAY_TILE;
      const px = obj.position.x * DISPLAY_TILE;
      const py = obj.position.y * DISPLAY_TILE;

      const tileFrame = typeof obj.state?.tile_frame === "number" ? (obj.state.tile_frame as number) : undefined;
      const tilesetKey = (obj.state?.tileset as string | undefined) ?? "kenney_tiny_town";
      const stateColor = typeof obj.state?.color === "number" ? (obj.state.color as number) : undefined;
      const tags: string[] = Array.isArray(obj.tags) ? (obj.tags as string[]) : [];

      const onClick = (_p: Phaser.Input.Pointer, _x: number, _y: number, event?: Phaser.Types.Input.EventData) => {
        event?.stopPropagation();
        eventBus.emit({ type: "player.click_object", payload: { objectId: obj.id } });
      };

      // Image/Rectangle 有自动命中区；Graphics 没有，需显式传 Rectangle
      const makeImageInteractive = (go: Phaser.GameObjects.Image | Phaser.GameObjects.Rectangle) => {
        go.setData("objectId", obj.id);
        go.setInteractive({ useHandCursor: true });
        go.on("pointerdown", onClick);
      };
      const makeGfxInteractive = (go: Phaser.GameObjects.Graphics) => {
        go.setData("objectId", obj.id);
        go.setInteractive(
          new Phaser.Geom.Rectangle(px, py, w, h),
          Phaser.Geom.Rectangle.Contains,
        );
        go.input!.cursor = "pointer";
        go.on("pointerdown", onClick);
      };

      // ── tile_frame → 贴图渲染（支持 Kenney Tiny Town 和 Tiny Dungeon，支持任意尺寸缩放）──
      if (tileFrame !== undefined) {
        const isDungeon = tilesetKey === "kenney_tiny_dungeon";
        const textureKey = isDungeon ? KENNEY_DUNGEON_KEY : KENNEY_TILE_KEY;
        const meta = isDungeon ? dungeon : kenney;
        if (meta && this.textures.exists(textureKey) && tileFrame < meta.total) {
          const img = this.add.image(px, py, textureKey, tileFrame);
          img.setOrigin(0, 0);
          img.setDisplaySize(w, h);
          makeImageInteractive(img);
          this.objectLayer.add(img);
          continue;
        }
      }

      // ── 无 tile_frame → 根据标签绘制专用家具图形或彩色矩形 ──
      const gfx = this.add.graphics();
      makeGfxInteractive(gfx);
      this.objectLayer.add(gfx);

      if (tags.includes("bed")) {
        this._drawBed(gfx, px, py, w, h, stateColor ?? 0x8899BB);
      } else if (tags.includes("blackboard")) {
        this._drawBlackboard(gfx, px, py, w, h);
      } else if (tags.includes("desk") || tags.includes("table")) {
        this._drawDesk(gfx, px, py, w, h, stateColor ?? 0xBB8855);
      } else if (tags.includes("bookshelf")) {
        this._drawBookshelf(gfx, px, py, w, h, stateColor ?? 0x663311);
      } else if (tags.includes("plant")) {
        this._drawPlant(gfx, px, py, w, h, stateColor ?? 0x4A7C59);
      } else if (tags.includes("pet")) {
        this._drawPetBed(gfx, px, py, w, h, stateColor ?? 0xE8C4A0);
      } else {
        // 通用彩色矩形（计数台、货架、展台等）
        const defaultColor = obj.blocks_movement ? 0x3d3529 : 0xc9bfa4;
        const color = stateColor ?? defaultColor;
        const alpha = obj.blocks_movement ? 0.85 : 0.75;
        gfx.fillStyle(color, alpha);
        gfx.fillRect(px + 2, py + 2, w - 4, h - 4);
        gfx.lineStyle(1, 0x000000, 0.3);
        gfx.strokeRect(px + 2, py + 2, w - 4, h - 4);
        // 若尺寸较大，加内部十字线表示大型家具
        if (objW >= 2 || objH >= 2) {
          gfx.lineStyle(1, 0x000000, 0.15);
          gfx.lineBetween(px + w / 2, py + 2, px + w / 2, py + h - 2);
          gfx.lineBetween(px + 2, py + h / 2, px + w - 2, py + h / 2);
        }
      }
    }
  }

  /** 绘制床：床框 + 床单 + 枕头 */
  private _drawBed(
    gfx: Phaser.GameObjects.Graphics,
    px: number, py: number, w: number, h: number,
    color: number,
  ): void {
    // 床框（深木色）
    gfx.fillStyle(0x7A5C3A, 1);
    gfx.fillRect(px + 1, py + 1, w - 2, h - 2);
    // 床单（主色调，略小）
    gfx.fillStyle(color, 0.9);
    gfx.fillRect(px + 3, py + 6, w - 6, h - 8);
    // 水平床单折叠线
    gfx.lineStyle(1, 0xFFFFFF, 0.3);
    gfx.lineBetween(px + 4, py + h - 8, px + w - 4, py + h - 8);
    // 枕头（白色，头部方向，顶部）
    gfx.fillStyle(0xF0EEE8, 0.95);
    const pillowW = Math.max(w - 12, 8);
    const pillowH = Math.min(8, h / 3);
    gfx.fillRoundedRect(px + (w - pillowW) / 2, py + 4, pillowW, pillowH, 2);
    // 枕头轮廓
    gfx.lineStyle(1, 0xCCCCCC, 0.5);
    gfx.strokeRoundedRect(px + (w - pillowW) / 2, py + 4, pillowW, pillowH, 2);
  }

  /** 绘制黑板：黑板面 + 粉笔边框 */
  private _drawBlackboard(
    gfx: Phaser.GameObjects.Graphics,
    px: number, py: number, w: number, h: number,
  ): void {
    // 木框
    gfx.fillStyle(0x8B5E3C, 1);
    gfx.fillRect(px, py, w, h);
    // 黑板面
    gfx.fillStyle(0x2D5A27, 1);
    gfx.fillRect(px + 3, py + 3, w - 6, h - 6);
    // 白色粉笔线条（模拟书写）
    gfx.lineStyle(1, 0xFFFFFF, 0.5);
    const lineY1 = py + h * 0.35;
    const lineY2 = py + h * 0.65;
    gfx.lineBetween(px + 6, lineY1, px + w - 8, lineY1);
    gfx.lineBetween(px + 6, lineY2, px + w * 0.6, lineY2);
  }

  /** 绘制桌子/书桌：桌面 + 腿 */
  private _drawDesk(
    gfx: Phaser.GameObjects.Graphics,
    px: number, py: number, w: number, h: number,
    color: number,
  ): void {
    // 桌腿（深色）
    gfx.fillStyle(0x7A5230, 1);
    const legW = 3;
    gfx.fillRect(px + 2, py + 2, legW, h - 4);
    gfx.fillRect(px + w - legW - 2, py + 2, legW, h - 4);
    // 桌面
    gfx.fillStyle(color, 0.95);
    gfx.fillRect(px + legW + 2, py + 2, w - (legW + 2) * 2, h - 4);
    // 桌面高光
    gfx.lineStyle(1, 0xFFFFFF, 0.25);
    gfx.lineBetween(px + legW + 4, py + 4, px + w - legW - 4, py + 4);
  }

  /** 绘制书架：竖格 + 书本 */
  private _drawBookshelf(
    gfx: Phaser.GameObjects.Graphics,
    px: number, py: number, w: number, h: number,
    color: number,
  ): void {
    // 书架框
    gfx.fillStyle(color, 1);
    gfx.fillRect(px + 1, py + 1, w - 2, h - 2);
    // 隔板
    const shelfColor = Phaser.Display.Color.ValueToColor(color);
    const darkerColor = Phaser.Display.Color.RGBToString(
      Math.max(0, shelfColor.red - 40),
      Math.max(0, shelfColor.green - 40),
      Math.max(0, shelfColor.blue - 40),
    );
    gfx.lineStyle(1, parseInt(darkerColor.replace('#', ''), 16), 0.8);
    const shelfCount = Math.max(1, Math.floor(h / DISPLAY_TILE));
    for (let s = 1; s < shelfCount; s++) {
      const sy = py + (h / shelfCount) * s;
      gfx.lineBetween(px + 2, sy, px + w - 2, sy);
    }
    // 书本竖线（模拟书脊）
    const BOOK_COLORS = [0xC0392B, 0x2980B9, 0x27AE60, 0xE67E22, 0x8E44AD];
    const bookW = 4;
    let bx = px + 3;
    let bi = 0;
    while (bx + bookW < px + w - 2) {
      gfx.fillStyle(BOOK_COLORS[bi % BOOK_COLORS.length], 0.9);
      gfx.fillRect(bx, py + 3, bookW - 1, h - 6);
      bx += bookW;
      bi++;
    }
  }

  /** 绘制花架/花盆：绿色叶片 + 花盆 */
  private _drawPlant(
    gfx: Phaser.GameObjects.Graphics,
    px: number, py: number, w: number, h: number,
    color: number,
  ): void {
    // 花盆底部
    gfx.fillStyle(0xC47A3C, 0.9);
    const potH = Math.max(6, h / 3);
    gfx.fillRect(px + w / 4, py + h - potH - 1, w / 2, potH);
    // 绿叶（椭圆）
    gfx.fillStyle(color, 0.9);
    gfx.fillEllipse(px + w / 2, py + h / 2 - 2, w - 4, h - potH - 2);
    // 花朵点缀
    gfx.fillStyle(0xFFAA44, 0.8);
    gfx.fillCircle(px + w / 2, py + h / 2 - 4, 3);
  }

  /** 绘制宠物窝：椭圆形软垫 + 边缘 */
  private _drawPetBed(
    gfx: Phaser.GameObjects.Graphics,
    px: number, py: number, w: number, h: number,
    color: number,
  ): void {
    // 边缘（深色圆环）
    gfx.fillStyle(Phaser.Display.Color.ValueToColor(color).darken(30).color, 0.9);
    gfx.fillEllipse(px + w / 2, py + h / 2, w - 2, h - 2);
    // 内部软垫
    gfx.fillStyle(color, 0.85);
    gfx.fillEllipse(px + w / 2, py + h / 2 + 1, w - 8, h - 8);
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
        // 选中 agent（用于右侧面板展示）
        eventBus.emit({ type: "player.click_agent", payload: { agentId: profile.id } });
        // 非玩家 NPC：启动自动追踪靠近
        if (profile.entity_type !== "player") {
          eventBus.emit({ type: "player.track_agent", payload: { agentId: profile.id } });
        }
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

    this.agentLayer.add([sprite as Phaser.GameObjects.GameObject, label]);
    const node: AgentNode = {
      sprite,
      label,
      animKind,
      colorKey,
      lastFacing: rt.facing || "down",
      lastMoving: rt.state === "MOVING",
    };
    this.playAgentAnim(profile.id, node, rt.state, rt.facing || "down");
    this.agentNodes.set(profile.id, node);

    // 阶段 19：初始化气泡（贴在名签上方一点）
    const bubbleY = py - LABEL_ABOVE - 6;
    this.bubbles.ensure(profile.id, px, bubbleY);
    const variant = mapStateToBubble(rt.state);
    if (variant) {
      this.bubbles.show(profile.id, variant);
    }
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
        this.agentNodes.delete(payload.agentId);
      }
      this.bubbles?.destroyAgent(payload.agentId);
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
    // 阶段 19：气泡跟随移动 + 状态映射
    this.bubbles?.tweenTo(payload.agentId, targetX, targetY - labelAbove2 - 6, 260);
    const variant = mapStateToBubble(payload.state);
    if (variant) {
      this.bubbles?.show(payload.agentId, variant);
    } else {
      this.bubbles?.fadeOut(payload.agentId);
    }
    const facing = payload.facing || node.lastFacing;
    this.playAgentAnim(payload.agentId, node, payload.state, facing);
    node.lastFacing = facing;
    node.lastMoving = payload.state === "MOVING";
  }

  /** 阶段 19：在指定 agent 头顶显示一段台词气泡（用于 NPC-NPC 对话）。 */
  showSpeechBubble(agentId: string, text: string): void {
    this.bubbles?.showSpeech(agentId, text);
  }

  // ---------------------------------------------------------------------
  // 自然事件视觉效果（程序化绘制，无需外部精灵图）
  // ---------------------------------------------------------------------

  /**
   * 在地图坐标 (x, y) 添加某类自然效果节点。
   * 若 object_id 对应的效果已存在则先移除再添加（防重复）。
   */
  private addEffect(kind: string, p: NaturalEffectPayload): void {
    const id = p.object_id ?? `${kind}_${p.x}_${p.y}`;
    this.removeEffect(id);  // 清理旧节点

    const cx = (p.x ?? 0) * DISPLAY_TILE + DISPLAY_TILE / 2;
    const cy = (p.y ?? 0) * DISPLAY_TILE + DISPLAY_TILE / 2;
    const r = DISPLAY_TILE / 2 - 2;
    const effects = this.manifests?.tilesets.effects ?? {};

    if (kind === "fire") {
      const gfx = this.add.graphics();
      const coreColor = effects.fire?.color_core ?? 0xFFCC00;
      const outerColor = effects.fire?.color_outer ?? 0xFF4400;
      const drawFire = (alpha: number) => {
        gfx.clear();
        gfx.fillStyle(outerColor, alpha * 0.8);
        gfx.fillEllipse(cx, cy + 4, r * 2, r * 1.5);
        gfx.fillStyle(coreColor, alpha);
        gfx.fillEllipse(cx, cy, r * 1.2, r * 1.8);
      };
      drawFire(0.9);
      this.effectLayer.add(gfx);
      const tween = this.tweens.addCounter({
        from: 70,
        to: 100,
        duration: 400,
        yoyo: true,
        repeat: -1,
        onUpdate: (t) => drawFire((t?.getValue() ?? 90) / 100),
      });
      this.effectNodes.set(id, { kind, gfx, tween });

    } else if (kind === "fishing") {
      const gfx = this.add.graphics();
      const color = effects.fishing_spot?.color ?? 0x3377FF;
      const drawRipple = (scale: number) => {
        gfx.clear();
        gfx.lineStyle(2, color, 0.7 * (1 - scale));
        gfx.strokeEllipse(cx, cy, r * 2 * scale, r * scale);
      };
      drawRipple(0.5);
      this.effectLayer.add(gfx);
      const tween = this.tweens.addCounter({
        from: 30,
        to: 100,
        duration: 1200,
        repeat: -1,
        onUpdate: (t) => drawRipple((t?.getValue() ?? 50) / 100),
      });
      this.effectNodes.set(id, { kind, gfx, tween });

    } else if (kind === "mushroom") {
      const gfx = this.add.graphics();
      const capColor = effects.mushroom?.color_cap ?? 0xFF2200;
      const stemColor = effects.mushroom?.color_stem ?? 0xFFFFDD;
      gfx.fillStyle(capColor, 0.85);
      gfx.fillEllipse(cx, cy - 4, r * 1.6, r * 1.2);
      gfx.fillStyle(stemColor, 0.85);
      gfx.fillRect(cx - r * 0.3, cy - 2, r * 0.6, r * 0.8);
      this.effectLayer.add(gfx);
      this.effectNodes.set(id, { kind, gfx });

    } else if (kind === "flower") {
      const gfx = this.add.graphics();
      const petalColors = [0xFF99BB, 0xFFBB33, 0xFF66AA, 0xFF3388];
      const pc = petalColors[Math.floor(Math.random() * petalColors.length)];
      gfx.fillStyle(pc, 0.8);
      for (let i = 0; i < 5; i++) {
        const angle = (i / 5) * Math.PI * 2;
        gfx.fillEllipse(cx + Math.cos(angle) * r * 0.6, cy + Math.sin(angle) * r * 0.6, r * 0.7, r * 0.5);
      }
      gfx.fillStyle(0xFFFF00, 1.0);
      gfx.fillCircle(cx, cy, r * 0.25);
      this.effectLayer.add(gfx);
      this.effectNodes.set(id, { kind, gfx });

    } else if (kind === "firefly") {
      const gfx = this.add.graphics();
      const color = effects.firefly?.color ?? 0xFFFF88;
      const [aMin, aMax] = effects.firefly?.alpha_range ?? [0.3, 0.9];
      const dots = Array.from({ length: 5 }, (_, i) => ({
        x: cx + (Math.random() - 0.5) * DISPLAY_TILE * 2,
        y: cy + (Math.random() - 0.5) * DISPLAY_TILE * 2,
        phase: i * 0.4,
      }));
      const drawFF = (t: number) => {
        gfx.clear();
        for (const d of dots) {
          const a = aMin + (aMax - aMin) * (0.5 + 0.5 * Math.sin(t + d.phase));
          gfx.fillStyle(color, a);
          gfx.fillCircle(d.x, d.y, 2);
        }
      };
      drawFF(0);
      this.effectLayer.add(gfx);
      const tween = this.tweens.addCounter({
        from: 0,
        to: Math.PI * 2,
        duration: 2000,
        repeat: -1,
        onUpdate: (t) => drawFF(t?.getValue() ?? 0),
      });
      this.effectNodes.set(id, { kind, gfx, tween });

    } else if (kind === "bird") {
      const gfx = this.add.graphics();
      const color = effects.bird?.color ?? 0x224422;
      gfx.fillStyle(color, 0.8);
      gfx.fillTriangle(cx - 6, cy, cx, cy - 4, cx + 6, cy);
      this.effectLayer.add(gfx);
      const tween = this.tweens.add({
        targets: gfx,
        y: `-=${DISPLAY_TILE * 0.3}`,
        duration: 800,
        yoyo: true,
        repeat: -1,
        ease: "Sine.easeInOut",
      });
      this.effectNodes.set(id, { kind, gfx, tween });

    } else if (kind === "puddle") {
      const color = effects.puddle?.color ?? 0x7FBFFF;
      const rect = this.add.rectangle(cx, cy, DISPLAY_TILE - 4, DISPLAY_TILE / 3, color, 0.45);
      this.effectLayer.add(rect);
      this.effectNodes.set(id, { kind, gfx: rect });

    } else if (kind === "fruit") {
      const gfx = this.add.graphics();
      const color = effects.fruit?.color ?? 0xFF8800;
      gfx.fillStyle(color, 0.9);
      gfx.fillCircle(cx, cy, r * 0.55);
      gfx.fillStyle(0x33AA33, 0.8);
      gfx.fillRect(cx - 1, cy - r * 0.55 - 4, 2, 5);
      this.effectLayer.add(gfx);
      this.effectNodes.set(id, { kind, gfx });
    }
  }

  /** 移除并销毁某 objectId 对应的效果节点。 */
  private removeEffect(objectId: string): void {
    const node = this.effectNodes.get(objectId);
    if (!node) return;
    node.tween?.stop();
    node.gfx.destroy();
    this.effectNodes.delete(objectId);
  }

  /** 根据天气条件更新全屏叠加层颜色和透明度（保证最低可见度）。 */
  private applyWeatherOverlay(condition: string, intensity: number): void {
    // baseAlpha = 该天气最低可感知透明度；scale = 强度对透明度的放大系数
    const cfg: Record<string, { color: number; baseAlpha: number; scale: number }> = {
      sunny:  { color: 0xFFFFCC, baseAlpha: 0,    scale: 0    },
      cloudy: { color: 0x8899AA, baseAlpha: 0.18, scale: 0.05 },
      rainy:  { color: 0x2244AA, baseAlpha: 0.22, scale: 0.18 },
      stormy: { color: 0x111133, baseAlpha: 0.40, scale: 0.20 },
      foggy:  { color: 0xCCDDEE, baseAlpha: 0.30, scale: 0.20 },
    };
    const { color, baseAlpha, scale } = cfg[condition] ?? { color: 0, baseAlpha: 0, scale: 0 };
    const alpha = baseAlpha + scale * Math.max(0, Math.min(1, intensity));
    this.weatherOverlay.setFillStyle(color, alpha);
  }

  /** 雷声时短暂白闪。 */
  private flashThunder(): void {
    this.weatherOverlay.setFillStyle(0xFFFFFF, 0.5);
    this.time.delayedCall(80, () => {
      this.weatherOverlay.setFillStyle(0x111133, 0.28);
    });
  }

  // ---------------------------------------------------------------------
  // 输入
  // ---------------------------------------------------------------------

  private onPointerDown(pointer: Phaser.Input.Pointer): void {
    if (this._inputBlocked || !this.currentSceneId) return;
    const worldX = this.cameras.main.worldView.x + pointer.x;
    const worldY = this.cameras.main.worldView.y + pointer.y;
    const tx = Math.floor(worldX / DISPLAY_TILE);
    const ty = Math.floor(worldY / DISPLAY_TILE);
    if (tx < 0 || ty < 0) return;
    this.showHighlight(tx, ty);
    // 点击地图空格时取消任何正在进行的 NPC 追踪
    eventBus.emit({ type: "player.stop_tracking", payload: {} });
    eventBus.emit({ type: "player.click_tile", payload: { x: tx, y: ty, sceneId: this.currentSceneId } });
  }

  private onKeyDown(event: KeyboardEvent): void {
    if (this._inputBlocked) return;
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
