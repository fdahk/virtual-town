import { useEffect, useState } from "react";
import { loadManifests, resolvePortrait } from "./manifest";

/**
 * 返回 Agent 对应的对话立绘 URL。
 * - 若 manifest 尚未加载，返回 null；
 * - 若 Agent 没有登记头像，返回 null（调用方用默认色块占位）；
 * - 若 URL 指向的 PNG 未就绪（404），返回 null，而非抛错。
 */
export function usePortrait(agentId: string | null | undefined): string | null {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setUrl(null);
    if (!agentId) return;
    (async () => {
      try {
        const m = await loadManifests();
        const candidate = resolvePortrait(m, agentId);
        if (!candidate) return;
        // 预检 404：避免 AgentPanel 渲染出现 alt 文案闪烁
        const head = await fetch(candidate, { method: "HEAD" });
        if (!cancelled && head.ok) setUrl(candidate);
      } catch {
        // ignore
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [agentId]);

  return url;
}
