import { useEffect, useState } from "react";
import { obsApi } from "../../api";
import type { AgentRuntimeView } from "../../types/observability";

export function AgentRuntimeCard({ agentId }: { agentId: string }) {
  const [data, setData] = useState<AgentRuntimeView | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let running = true;
    const load = async () => {
      try {
        const d = await obsApi.getAgentRuntime(agentId);
        if (running) setData(d);
        if (running) setErr(null);
      } catch (e) {
        if (running)
          setErr(String((e as { message?: string }).message ?? e));
      }
    };
    load();
    const id = window.setInterval(load, 3000);
    return () => {
      running = false;
      window.clearInterval(id);
    };
  }, [agentId]);

  if (err) {
    return (
      <div style={{ padding: 16, color: "#ff7a59", fontSize: 12 }}>
        查询失败：{err}
      </div>
    );
  }
  if (!data) return <div style={{ padding: 16, opacity: 0.7 }}>加载中……</div>;

  return (
    <div style={styles.grid}>
      <div style={styles.card}>
        <div style={styles.title}>档案</div>
        <KV k="id" v={data.agent.id} />
        <KV k="name" v={data.agent.name} />
        <KV k="type" v={data.agent.entity_type} />
        <KV k="occupation" v={data.agent.occupation ?? "-"} />
        <KV k="personality" v={data.agent.personality.join(" / ")} />
      </div>
      <div style={styles.card}>
        <div style={styles.title}>状态</div>
        {data.state ? (
          <>
            <KV k="scene" v={data.state.scene_id} />
            <KV k="pos" v={`(${data.state.x}, ${data.state.y})`} />
            <KV k="state" v={data.state.state} />
            <KV k="emotion" v={data.state.emotion ?? "-"} />
            <KV k="energy" v={data.state.energy.toFixed(2)} />
            <KV k="hunger" v={data.state.hunger.toFixed(2)} />
            <KV k="goal" v={data.state.current_goal ?? "-"} />
            <KV k="facing" v={data.state.facing} />
          </>
        ) : (
          <div style={{ fontSize: 12, opacity: 0.6 }}>无运行态</div>
        )}
      </div>
      <div style={styles.card}>
        <div style={styles.title}>Redis runtime</div>
        <pre style={styles.pre}>
          {data.redis_runtime ? JSON.stringify(data.redis_runtime, null, 2) : "null"}
        </pre>
      </div>
      <div style={{ ...styles.card, gridColumn: "span 2" }}>
        <div style={styles.title}>最近事件（20）</div>
        <div style={styles.list}>
          {data.recent_events.length === 0 ? (
            <div style={{ fontSize: 12, opacity: 0.6 }}>暂无</div>
          ) : (
            data.recent_events.map((e) => (
              <div key={e.id} style={styles.eventRow}>
                <span style={{ color: "#8b949e" }}>
                  {e.created_at ? new Date(e.created_at).toLocaleTimeString() : ""}
                </span>
                <span style={{ color: "#c0caf5" }}>{e.event_type}</span>
                <span style={{ opacity: 0.85 }}>{e.title ?? ""}</span>
              </div>
            ))
          )}
        </div>
      </div>
      <div style={{ ...styles.card, gridColumn: "span 2" }}>
        <div style={styles.title}>最近记忆（10）</div>
        <div style={styles.list}>
          {data.recent_memories.length === 0 ? (
            <div style={{ fontSize: 12, opacity: 0.6 }}>暂无</div>
          ) : (
            data.recent_memories.map((m) => (
              <div key={m.id} style={styles.memoryRow}>
                <span style={{ color: "#8b949e" }}>
                  {m.created_at ? new Date(m.created_at).toLocaleTimeString() : ""}
                </span>
                <span
                  style={{
                    color: "#79c0ff",
                    fontFamily: "monospace",
                    fontSize: 11,
                  }}
                >
                  [{m.scope} imp={m.importance}]
                </span>
                <span>{m.description}</span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

function KV({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div style={kvStyle}>
      <span style={{ color: "#8b949e" }}>{k}</span>
      <span style={{ color: "#e6edf3", fontFamily: "monospace" }}>{v}</span>
    </div>
  );
}

const kvStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  fontSize: 12,
  padding: "2px 0",
};

const styles: Record<string, React.CSSProperties> = {
  grid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
    gap: 12,
  },
  card: {
    background: "#11161e",
    border: "1px solid rgba(255,255,255,0.06)",
    borderRadius: 6,
    padding: 12,
  },
  title: {
    fontSize: 12,
    fontWeight: 600,
    color: "#8b949e",
    marginBottom: 8,
    textTransform: "uppercase",
    letterSpacing: 1,
  },
  pre: {
    background: "#0c1118",
    padding: 8,
    borderRadius: 4,
    fontSize: 11,
    overflow: "auto",
    maxHeight: 200,
  },
  list: { display: "flex", flexDirection: "column", gap: 4, fontSize: 12 },
  eventRow: {
    display: "grid",
    gridTemplateColumns: "80px 200px 1fr",
    gap: 8,
  },
  memoryRow: {
    display: "grid",
    gridTemplateColumns: "80px 130px 1fr",
    gap: 8,
  },
};
