// Phaser ↔ React 唯一通信管道。
// 严禁 Phaser 直接 import Zustand 写状态，或 React 直接触碰 Phaser 对象。

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
  | { type: "player.stop_tracking"; payload: Record<string, never> };

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
