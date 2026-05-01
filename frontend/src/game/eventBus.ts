// Phaser ↔ React 唯一通信管道。
// 严禁 Phaser 直接 import Zustand 写状态，或 React 直接触碰 Phaser 对象。

/** 自然事件效果通知载荷（来自后端 WorldEvent.payload） */
export interface NaturalEffectPayload {
  object_id?: string;
  x?: number;
  y?: number;
  name?: string;
  condition?: string;
  intensity?: number;
  from_id?: string;
  to_id?: string;
  patch?: Record<string, unknown>;
  state?: Record<string, unknown>;
}

/** 物品生命周期事件载荷 */
export interface ObjectLifecyclePayload {
  object_id: string;
  scene_id?: string;
  name?: string;
  object_type?: string;
  position?: { x: number; y: number };
  size?: { width: number; height: number };
  blocks_movement?: boolean;
  available_interactions?: string[];
  state?: Record<string, unknown>;
  tags?: string[];
  patch?: Record<string, unknown>;
}

export type GameEvent =
  | {
      type: "runtime.update";
      payload: { agentId: string; sceneId: string; x: number; y: number; state: string; facing?: string };
    }
  | { type: "agent.selected"; payload: { agentId: string } }
  | { type: "scene.change"; payload: { sceneId: string } }
  | { type: "player.click_tile"; payload: { x: number; y: number; sceneId: string } }
  | { type: "player.click_agent"; payload: { agentId: string } }
  | { type: "player.click_object"; payload: { objectId: string } }
  | { type: "world.dataset.loaded"; payload: { sceneId: string } }
  | { type: "debug.layer.toggle"; payload: { layer: "collision" | "hazard" | "portal"; enabled: boolean } }
  /** 玩家点击 NPC，开始自动追踪靠近 */
  | { type: "player.track_agent"; payload: { agentId: string } }
  /** 玩家点击地图空格或手动取消，停止追踪 */
  | { type: "player.stop_tracking"; payload: Record<string, never> }
  // ── 自然事件（后端 WorldEvent → 前端视觉效果）────────────────────────────
  | { type: "world.fire_started";        payload: NaturalEffectPayload }
  | { type: "world.fire_spread";         payload: NaturalEffectPayload }
  | { type: "world.fire_extinguished";   payload: NaturalEffectPayload }
  | { type: "weather.condition_changed"; payload: NaturalEffectPayload }
  | { type: "weather.thunder";           payload: NaturalEffectPayload }
  | { type: "nature.fishing_spot_appeared"; payload: NaturalEffectPayload }
  | { type: "nature.mushroom_appeared";  payload: NaturalEffectPayload }
  | { type: "nature.mushroom_withered";  payload: NaturalEffectPayload }
  | { type: "nature.flower_bloomed";     payload: NaturalEffectPayload }
  | { type: "nature.flower_withered";    payload: NaturalEffectPayload }
  | { type: "nature.firefly_appeared";   payload: NaturalEffectPayload }
  | { type: "nature.bird_appeared";      payload: NaturalEffectPayload }
  | { type: "nature.bird_flew_away";     payload: NaturalEffectPayload }
  | { type: "nature.puddle_formed";      payload: NaturalEffectPayload }
  | { type: "nature.puddle_dried";       payload: NaturalEffectPayload }
  | { type: "nature.fruit_ripened";      payload: NaturalEffectPayload }
  | { type: "world.storm_warning";       payload: NaturalEffectPayload }
  // ── 物品生命周期（玩家/事件/任务系统都会发这些）─────────────────────────
  | { type: "world.object_state_changed"; payload: ObjectLifecyclePayload }
  | { type: "world.object_spawned";       payload: ObjectLifecyclePayload }
  | { type: "world.object_despawned";     payload: ObjectLifecyclePayload }
  | { type: "nature.mushroom_picked";     payload: ObjectLifecyclePayload }
  | { type: "nature.fruit_picked";        payload: ObjectLifecyclePayload }
  | { type: "nature.fish_caught";         payload: ObjectLifecyclePayload };

type Handler = (event: GameEvent) => void;

class EventBus {
  private handlers = new Set<Handler>();

  emit(event: GameEvent): void {
    this.handlers.forEach((h) => h(event));
  }

  on(handler: Handler): () => void {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }
}

export const eventBus = new EventBus();
