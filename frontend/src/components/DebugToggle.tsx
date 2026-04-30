import { useState } from "react";
import { eventBus } from "../game/eventBus";

const LAYERS: Array<{ key: "collision" | "hazard" | "portal"; label: string; defaultOn: boolean }> = [
  { key: "collision", label: "碰撞", defaultOn: false },
  { key: "hazard", label: "危险", defaultOn: true },
  { key: "portal", label: "出入口", defaultOn: true },
];

export function DebugToggle() {
  const [state, setState] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(LAYERS.map((l) => [l.key, l.defaultOn])),
  );
  return (
    <div style={{ display: "flex", gap: 6 }}>
      {LAYERS.map((l) => (
        <button
          key={l.key}
          onClick={() => {
            const next = !state[l.key];
            setState({ ...state, [l.key]: next });
            eventBus.emit({
              type: "debug.layer.toggle",
              payload: { layer: l.key, enabled: next },
            });
          }}
          style={{
            padding: "3px 8px",
            fontSize: 11,
            background: state[l.key] ? "#1f4a85" : "#151c26",
            color: "#eef0f2",
            border: "1px solid rgba(255,255,255,0.08)",
            borderRadius: 999,
            cursor: "pointer",
          }}
        >
          {l.label}
        </button>
      ))}
    </div>
  );
}
