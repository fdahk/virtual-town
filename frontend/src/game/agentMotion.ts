/**
 * NPC / 玩家精灵步进 tween：时长与环境变量（仓库根 ``.env`` 中的 ``VITE_AGENT_MOVE_TWEEN_MS``）。
 * Vite ``envDir`` 指向仓库根目录时可与后端共用一份配置。
 */
export function getAgentMoveTweenMs(): number {
  const raw = Number(import.meta.env.VITE_AGENT_MOVE_TWEEN_MS);
  if (Number.isFinite(raw) && raw >= 40 && raw <= 900) {
    return raw;
  }
  return 145;
}

/** 与较快步行匹配：收尾略减速，避免叠 tween 时发飘 */
export const AGENT_MOVE_TWEEN_EASE = "Cubic.easeOut" as const;
