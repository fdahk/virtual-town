import { useWorldStore } from "../stores/worldStore";
import { api } from "../api";

export function TimeControl() {
  const sim = useWorldStore((s) => s.simulation);
  const setSim = useWorldStore((s) => s.setSimulation);
  if (!sim) return null;

  const toggle = async () => {
    const fn = sim.status === "running" ? api.pauseSimulation : api.resumeSimulation;
    const next = await fn(sim.id);
    setSim(next);
  };
  const setSpeed = async (s: number) => {
    const next = await api.setSimulationSpeed(sim.id, s);
    setSim(next);
  };

  return (
    <div style={styles.wrap}>
      <span style={{ fontSize: 12, opacity: 0.8 }}>
        {new Date(sim.world_time).toLocaleString("zh-CN", {
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
        })}
      </span>
      <span style={{ fontSize: 12, opacity: 0.6 }}>step {sim.current_step}</span>
      <button style={styles.btn} onClick={toggle}>
        {sim.status === "running" ? "⏸ 暂停" : "▶ 继续"}
      </button>
      {[0.5, 1, 2, 4].map((s) => (
        <button
          key={s}
          style={{
            ...styles.btn,
            opacity: sim.speed_multiplier === s ? 1 : 0.5,
            background: sim.speed_multiplier === s ? "#1d3fa1" : "#151c26",
          }}
          onClick={() => setSpeed(s)}
        >
          {s}x
        </button>
      ))}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  wrap: { display: "flex", alignItems: "center", gap: 8 },
  btn: {
    padding: "4px 10px",
    fontSize: 12,
    background: "#151c26",
    border: "1px solid rgba(255,255,255,0.08)",
    color: "#eef0f2",
    borderRadius: 6,
    cursor: "pointer",
  },
};
