import { useWorldStore } from "../stores/worldStore";

export function EventLog() {
  const events = useWorldStore((s) => s.events);
  const agents = useWorldStore((s) => s.agents);
  const display = events.slice(0, 40);
  return (
    <div style={styles.wrap}>
      <div style={styles.title}>事件日志</div>
      <div style={styles.list}>
        {display.map((e) => {
          const actorName = e.actor_entity_id ? agents[e.actor_entity_id]?.name ?? e.actor_entity_id : "";
          return (
            <div key={e.id} style={styles.row}>
              <span style={styles.time}>
                {new Date(e.created_at).toLocaleTimeString().slice(0, 8)}
              </span>
              <div style={styles.rowMain}>
                <span style={styles.tag} title={e.event_type}>
                  {labelEvent(e.event_type)}
                </span>
                <div style={styles.desc}>
                  {actorName ? <b>{actorName}　</b> : null}
                  {e.description}
                </div>
              </div>
            </div>
          );
        })}
        {display.length === 0 && (
          <div style={{ color: "#8791a1", padding: 10, fontSize: 12 }}>暂无事件</div>
        )}
      </div>
    </div>
  );
}

function labelEvent(t: string): string {
  const table: Record<string, string> = {
    "agent.moved": "移动",
    "agent.action_started": "行动",
    "agent.action_finished": "到达",
    "agent.entered_location": "进入",
    "agent.left_location": "离开",
    "agent.interacted": "互动",
    "dialogue.message_created": "对话",
    "world.object_interacted": "物体",
    "world.hazard_triggered": "危险",
    "world.scene_changed": "切换",
    "animal.reacted": "动物",
    "memory.created": "记忆",
    "weather.condition_changed": "天气变化",
    "weather.thunder": "雷鸣",
    "world.fire_started": "起火",
    "world.fire_spread": "火势蔓延",
    "world.fire_extinguished": "火熄",
    "world.storm_warning": "风暴预警",
    "world.object_spawned": "物品出现",
    "world.object_despawned": "物品消失",
    "world.object_state_changed": "物品状态",
    "nature.puddle_formed": "水坑",
    "nature.puddle_dried": "水坑干",
  };
  return table[t] ?? t;
}

const styles: Record<string, React.CSSProperties> = {
  wrap: { padding: "8px 14px", height: "100%", display: "grid", gridTemplateRows: "auto 1fr" },
  title: { fontSize: 11, color: "#8791a1", letterSpacing: 1, textTransform: "uppercase" },
  list: { overflow: "auto", display: "grid", gap: 6, padding: "6px 0" },
  row: { display: "flex", gap: 10, fontSize: 12, alignItems: "flex-start" },
  rowMain: {
    flex: 1,
    minWidth: 0,
    display: "flex",
    flexDirection: "column",
    gap: 4,
  },
  time: {
    color: "#6b7280",
    width: 62,
    flexShrink: 0,
    fontFamily: "monospace",
    paddingTop: 2,
  },
  tag: {
    display: "inline-block",
    alignSelf: "flex-start",
    background: "#1a222f",
    color: "#8cc0ff",
    padding: "2px 8px",
    borderRadius: 4,
    fontSize: 10,
    fontWeight: 600,
    lineHeight: 1.3,
    maxWidth: "100%",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  desc: {
    color: "#cbd2db",
    lineHeight: 1.45,
    wordBreak: "break-word",
    overflowWrap: "anywhere",
  },
};
