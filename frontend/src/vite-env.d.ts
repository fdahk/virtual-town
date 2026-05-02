/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_WS_BASE_URL?: string;
  /** NPC 移动 tween 时长（毫秒），40–900，默认 145 */
  readonly VITE_AGENT_MOVE_TWEEN_MS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
