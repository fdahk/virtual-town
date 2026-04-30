import { useQuery } from "@tanstack/react-query";
import { useWorldStore } from "../stores/worldStore";
import { api } from "../api";
import { usePortrait } from "../game/assets/usePortrait";
import type { AgentProfile } from "../types/domain";

function AvatarBadge({ agent }: { agent: AgentProfile }) {
  const portrait = usePortrait(agent.id);
  if (portrait) {
    return (
      <div
        style={{
          ...avatarStyles.frame,
          backgroundImage: `url(${portrait})`,
          backgroundSize: "cover",
          backgroundPosition: "center top",
        }}
      />
    );
  }
  return (
    <div
      style={{
        ...avatarStyles.frame,
        background: agent.appearance?.color ?? "#aaa",
      }}
    />
  );
}

const avatarStyles: Record<string, React.CSSProperties> = {
  frame: {
    width: 40,
    height: 40,
    borderRadius: 8,
    boxShadow: "0 0 0 2px rgba(255,255,255,0.12)",
    overflow: "hidden",
  },
};

const INTERACTION_LABEL: Record<string, string> = {
  pet: "抚摸",
  feed: "喂食",
  call: "呼唤",
  scare: "惊吓",
};

export function AgentPanel({ onOpenMemory }: { onOpenMemory(id: string): void }) {
  const id = useWorldStore((s) => s.selectedAgentId);
  const agent = useWorldStore((s) => (id ? s.agents[id] : null));
  const runtime = useWorldStore((s) => (id ? s.runtimeByAgent[id] : null));
  const setPendingDialogue = useWorldStore((s) => s.setPendingDialogue);

  const rels = useQuery({
    queryKey: ["agent", id, "rels"],
    queryFn: () => (id ? api.listAgentRelationships(id) : Promise.resolve([])),
    enabled: !!id,
  });
  const memories = useQuery({
    queryKey: ["agent", id, "mem"],
    queryFn: () => (id ? api.listAgentMemories(id, 8) : Promise.resolve([])),
    enabled: !!id,
  });

  if (!agent) {
    return (
      <div style={{ padding: 18, color: "#8791a1", fontSize: 13 }}>
        从左侧选择一个居民或动物，或在地图上点击对方，查看详情。
      </div>
    );
  }
  return (
    <div style={styles.wrap}>
      <div style={styles.header}>
        <AvatarBadge agent={agent} />
        <div style={{ display: "grid", gap: 2 }}>
          <div style={{ fontSize: 16, fontWeight: 700 }}>{agent.name}</div>
          <div style={{ fontSize: 12, opacity: 0.65 }}>
            {agent.occupation || labelType(agent.entity_type)}
            {agent.age ? ` · ${agent.age} 岁` : ""}
          </div>
        </div>
      </div>

      {runtime && (
        <Box title="当前状态">
          <KV label="状态" value={runtime.state} />
          {runtime.current_goal ? <KV label="目标" value={runtime.current_goal} /> : null}
          {runtime.emotion ? <KV label="情绪" value={runtime.emotion} /> : null}
          <KV
            label="坐标"
            value={`(${runtime.position.x}, ${runtime.position.y})`}
          />
          {runtime.status_effects.length ? (
            <KV label="效果" value={runtime.status_effects.join(" / ")} />
          ) : null}
        </Box>
      )}

      <Box title="人物档案">
        {agent.personality.length ? (
          <KV label="性格" value={agent.personality.join(" / ")} />
        ) : null}
        {agent.background ? (
          <div style={styles.paragraph}>{agent.background}</div>
        ) : null}
      </Box>

      <Box title="操作">
        <div style={styles.actions}>
          {agent.entity_type === "human" && (
            <button
              style={styles.actionBtn}
              onClick={() =>
                setPendingDialogue({ targetId: agent.id, targetName: agent.name })
              }
            >
              发起对话
            </button>
          )}
          {agent.entity_type === "animal" &&
            Object.entries(INTERACTION_LABEL).map(([k, l]) => (
              <button
                key={k}
                style={styles.actionBtn}
                onClick={() =>
                  api
                    .interactPlayer({
                      entity_id: agent.id,
                      interaction_type: k,
                    })
                    .catch(() => undefined)
                }
              >
                {l}
              </button>
            ))}
          <button style={styles.actionBtn} onClick={() => onOpenMemory(agent.id)}>
            查看记忆
          </button>
        </div>
      </Box>

      {rels.data && rels.data.length > 0 && (
        <Box title="关系网（TA 对他人）">
          {rels.data.slice(0, 6).map((r) => (
            <div key={r.id} style={styles.relRow}>
              <span style={{ opacity: 0.7 }}>
                → {r.summary ?? r.to_entity_id}
              </span>
              <span style={{ fontSize: 11, opacity: 0.5 }}>
                熟 {r.familiarity.toFixed(2)} 感 {r.affection.toFixed(2)} 怕 {r.fear.toFixed(2)}
              </span>
            </div>
          ))}
        </Box>
      )}

      {memories.data && memories.data.length > 0 && (
        <Box title="最近记忆">
          {memories.data.slice(0, 5).map((m) => (
            <div key={m.id} style={styles.memRow}>
              <div style={{ fontSize: 12 }}>{m.description}</div>
              <div style={{ fontSize: 10, opacity: 0.5 }}>
                {labelMemoryType(m.memory_type)} · 重要度 {m.importance} · {new Date(m.created_at).toLocaleString()}
              </div>
            </div>
          ))}
        </Box>
      )}
    </div>
  );
}

function Box({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "grid", gap: 4, marginTop: 14 }}>
      <div style={styles.sectionTitle}>{title}</div>
      <div style={styles.section}>{children}</div>
    </div>
  );
}

function KV({ label, value }: { label: string; value: string }) {
  return (
    <div style={styles.kv}>
      <span style={{ color: "#8791a1", width: 56 }}>{label}</span>
      <span>{value}</span>
    </div>
  );
}

function labelType(t: string): string {
  return { human: "居民", animal: "动物", player: "玩家" }[t] ?? t;
}

function labelMemoryType(t: string): string {
  return { event: "事件", thought: "想法", chat: "对话", summary: "总结" }[t] ?? t;
}

const styles: Record<string, React.CSSProperties> = {
  wrap: { padding: 14, overflow: "auto", fontSize: 13 },
  header: {
    display: "flex",
    alignItems: "center",
    gap: 12,
    paddingBottom: 12,
    borderBottom: "1px solid rgba(255,255,255,0.06)",
  },
  avatar: {
    width: 36,
    height: 36,
    borderRadius: 999,
    boxShadow: "0 0 0 2px rgba(255,255,255,0.08)",
  },
  sectionTitle: {
    fontSize: 11,
    color: "#8791a1",
    letterSpacing: 1,
    textTransform: "uppercase",
  },
  section: { display: "grid", gap: 6 },
  kv: { display: "flex", gap: 10, fontSize: 12 },
  paragraph: {
    fontSize: 12,
    color: "#cbd2db",
    lineHeight: 1.45,
    background: "rgba(255,255,255,0.03)",
    padding: 8,
    borderRadius: 6,
  },
  actions: { display: "flex", gap: 6, flexWrap: "wrap" },
  actionBtn: {
    padding: "6px 10px",
    fontSize: 12,
    border: "1px solid rgba(255,255,255,0.1)",
    background: "#151c26",
    color: "#eef0f2",
    borderRadius: 999,
    cursor: "pointer",
  },
  relRow: { display: "flex", justifyContent: "space-between", fontSize: 12 },
  memRow: {
    display: "grid",
    gap: 2,
    padding: 8,
    background: "rgba(255,255,255,0.03)",
    borderRadius: 6,
  },
};
