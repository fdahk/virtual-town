import { useMemo } from "react";
import { useWorldStore } from "../stores/worldStore";

export function AgentList() {
  const agents = useWorldStore((s) => s.agents);
  const runtime = useWorldStore((s) => s.runtimeByAgent);
  const selectedId = useWorldStore((s) => s.selectedAgentId);
  const selectAgent = useWorldStore((s) => s.selectAgent);
  const scenes = useWorldStore((s) => s.scenes);

  const list = useMemo(() => {
    return Object.values(agents)
      .filter((a) => a.entity_type !== "player")
      .sort((a, b) => {
        const order: Record<string, number> = { human: 0, animal: 1 };
        return (order[a.entity_type] ?? 9) - (order[b.entity_type] ?? 9);
      });
  }, [agents]);

  return (
    <div style={styles.wrap}>
      <div style={styles.title}>小镇成员</div>
      <div style={styles.list}>
        {list.map((a) => {
          const rt = runtime[a.id];
          const sceneName = rt ? scenes[rt.scene_id]?.name ?? "-" : "-";
          const selected = selectedId === a.id;
          return (
            <button
              key={a.id}
              style={{
                ...styles.item,
                background: selected ? "#1f2939" : "transparent",
              }}
              onClick={() => selectAgent(a.id)}
            >
              <span
                style={{
                  ...styles.dot,
                  background: a.appearance?.color ?? "#aaa",
                }}
              />
              <div style={{ display: "grid" }}>
                <span style={{ fontWeight: 600 }}>{a.name}</span>
                <span style={{ fontSize: 11, opacity: 0.65 }}>
                  {labelType(a.entity_type)} · {sceneName}
                  {rt?.state && rt.state !== "IDLE" ? ` · ${rt.state}` : ""}
                </span>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function labelType(t: string): string {
  switch (t) {
    case "human":
      return "居民";
    case "animal":
      return "动物";
    case "player":
      return "玩家";
    default:
      return t;
  }
}

const styles: Record<string, React.CSSProperties> = {
  wrap: { padding: 12, display: "grid", gridTemplateRows: "auto 1fr", height: "100%" },
  title: { fontSize: 12, color: "#8791a1", marginBottom: 10, letterSpacing: 1 },
  list: { display: "grid", gap: 4, overflow: "auto" },
  item: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    padding: "8px 10px",
    borderRadius: 8,
    border: "1px solid transparent",
    color: "#f2f4f7",
    textAlign: "left",
    cursor: "pointer",
    background: "transparent",
  },
  dot: {
    width: 10,
    height: 10,
    borderRadius: 999,
    boxShadow: "0 0 0 2px rgba(255,255,255,0.1)",
  },
};
