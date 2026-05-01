import { useEffect, useRef, useState } from "react";
import { useWorldStore } from "../stores/worldStore";
import { api } from "../api";
import type { WorldObject } from "../types/domain";

// ── 交互动词中文标签 ────────────────────────────────────────────
const INTERACTION_LABELS: Record<string, string> = {
  fish:       "🎣 钓鱼",
  pick:       "🤌 采摘",
  sit:        "🪑 坐下",
  rest:       "😴 休息",
  rest_under: "🌿 树下小憩",
  inspect:    "🔍 察看",
  read:       "📖 阅读",
  smell:      "🌸 闻香",
  water:      "💧 浇水",
  climb:      "🧗 攀爬",
  push:       "📦 推动",
  open:       "📬 打开",
  jump_over:  "🦘 跳过",
  move:       "🚚 搬动",
  make_coffee:"☕ 制作咖啡",
};

// ── 物品类型图标 ─────────────────────────────────────────────────
const TYPE_ICON: Record<string, string> = {
  furniture:   "🪑",
  facility:    "⚙️",
  barrier:     "🚧",
  plant:       "🌿",
  decoration:  "🎪",
  item:        "📦",
  nature_spot: "🍃",
};

// ── 状态字段的中文描述 ───────────────────────────────────────────
interface StateBadge {
  label: string;
  active: boolean;
  activeColor: string;
  inactiveColor: string;
}

function buildStateBadges(state: Record<string, unknown>): StateBadge[] {
  const badges: StateBadge[] = [];

  if ("mushroom_present" in state)
    badges.push({
      label: state.mushroom_present ? "🍄 蘑菇已出现" : "🍄 蘑菇未出现",
      active: !!state.mushroom_present,
      activeColor: "#6e4f1e",
      inactiveColor: "#2a3040",
    });

  if ("fruit_ripe" in state)
    badges.push({
      label: state.fruit_ripe ? "🍎 果实已成熟" : "🍏 果实未成熟",
      active: !!state.fruit_ripe,
      activeColor: "#7a2020",
      inactiveColor: "#2a3040",
    });

  if ("fishing_active" in state)
    badges.push({
      label: state.fishing_active ? "🎣 鱼儿活跃" : "🎣 鱼儿平静",
      active: !!state.fishing_active,
      activeColor: "#1a4a7a",
      inactiveColor: "#2a3040",
    });

  if ("bloomed" in state)
    badges.push({
      label: state.bloomed ? "🌸 花朵盛开" : "🌱 花朵待放",
      active: !!state.bloomed,
      activeColor: "#6a2060",
      inactiveColor: "#2a3040",
    });

  if ("has_puddle" in state)
    badges.push({
      label: state.has_puddle ? "💧 积水中" : "🌤️ 已干燥",
      active: !!state.has_puddle,
      activeColor: "#1a3a5a",
      inactiveColor: "#2a3040",
    });

  if ("firefly_active" in state)
    badges.push({
      label: state.firefly_active ? "✨ 萤火虫活跃" : "🌙 萤火虫休息",
      active: !!state.firefly_active,
      activeColor: "#4a4010",
      inactiveColor: "#2a3040",
    });

  if (state.flammable) {
    const burning = !!(state as Record<string, unknown>).burning;
    badges.push({
      label: burning ? "🔥 燃烧中！" : "⚠️ 易燃物",
      active: burning,
      activeColor: "#7a2010",
      inactiveColor: "#3a2a10",
    });
  }

  if (state.notice_text && typeof state.notice_text === "string")
    badges.push({
      label: "📋 有公告",
      active: true,
      activeColor: "#2a4a2a",
      inactiveColor: "#2a3040",
    });

  return badges;
}

export function ObjectPanel() {
  const objectId = useWorldStore((s) => s.selectedObjectId);
  const sceneId = useWorldStore((s) => s.playerSceneId);
  const objectsByScene = useWorldStore((s) => s.objectsByScene);
  const selectObject = useWorldStore((s) => s.selectObject);

  const obj: WorldObject | undefined = sceneId
    ? objectsByScene[sceneId]?.find((o) => o.id === objectId)
    : undefined;

  const [interacting, setInteracting] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // 切换物品时清空上一次的交互状态
  useEffect(() => {
    setInteracting(null);
    setFeedback(null);
    // 取消上一次可能还挂起的请求
    abortRef.current?.abort();
  }, [objectId]);

  // 反馈消息 4 秒后自动消失
  useEffect(() => {
    if (!feedback) return;
    const t = setTimeout(() => setFeedback(null), 4000);
    return () => clearTimeout(t);
  }, [feedback]);

  if (!obj) {
    return (
      <div style={{ padding: 18, color: "#8791a1", fontSize: 13 }}>
        在地图上点击物品，查看交互选项。
      </div>
    );
  }

  const icon = TYPE_ICON[obj.object_type] ?? "📦";
  const badges = buildStateBadges(obj.state ?? {});
  const noticeText =
    typeof obj.state?.notice_text === "string" ? obj.state.notice_text : null;

  const handleInteract = async (action: string) => {
    abortRef.current?.abort();
    abortRef.current = new AbortController();
    setInteracting(action);
    setFeedback(null);

    // 10 秒超时保护，防止网络慢时按钮永久 disabled
    const timeoutId = setTimeout(() => {
      abortRef.current?.abort();
      setInteracting(null);
      setFeedback("⏱️ 请求超时，请稍后重试。");
    }, 10_000);

    try {
      await api.interactPlayer({ object_id: obj.id, interaction_type: action });
      setFeedback("✅ 指令已发送，等待角色响应");
    } catch (err) {
      const code = (err as { code?: string }).code;
      if (code === "OUT_OF_RANGE") {
        setFeedback("⚠️ 距离太远，请靠近后再试。");
      } else if ((err as Error)?.name === "AbortError") {
        // 切换物品或超时触发的中止，静默处理
      } else {
        setFeedback("❌ 操作失败，请稍后重试。");
      }
    } finally {
      clearTimeout(timeoutId);
      setInteracting(null);
    }
  };

  return (
    <div style={styles.wrap}>
      {/* 标题行 */}
      <div style={styles.header}>
        <span style={styles.icon}>{icon}</span>
        <div style={{ display: "grid", gap: 2 }}>
          <div style={{ fontSize: 15, fontWeight: 700 }}>{obj.name}</div>
          <div style={{ fontSize: 11, opacity: 0.55 }}>
            {labelType(obj.object_type)}
            {obj.blocks_movement ? " · 阻挡通行" : " · 可通行"}
          </div>
        </div>
        <button
          style={styles.closeBtn}
          title="关闭"
          onClick={() => selectObject(null)}
        >
          ✕
        </button>
      </div>

      {/* 动态状态徽章 */}
      {badges.length > 0 && (
        <div style={{ marginTop: 12, display: "grid", gap: 4 }}>
          <div style={styles.sectionTitle}>当前状态</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {badges.map((b, i) => (
              <span
                key={i}
                style={{
                  ...styles.badge,
                  background: b.active ? b.activeColor : b.inactiveColor,
                  opacity: b.active ? 1 : 0.65,
                }}
              >
                {b.label}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* 公告文字 */}
      {noticeText && (
        <div style={{ marginTop: 12 }}>
          <div style={styles.sectionTitle}>公告内容</div>
          <div style={styles.noticeBox}>{noticeText}</div>
        </div>
      )}

      {/* 交互按钮 */}
      {obj.available_interactions.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div style={styles.sectionTitle}>可交互操作</div>
          <div style={styles.actions}>
            {obj.available_interactions.map((action) => (
              <button
                key={action}
                style={{
                  ...styles.actionBtn,
                  opacity: interacting === action ? 0.6 : 1,
                }}
                disabled={interacting !== null}
                onClick={() => handleInteract(action)}
              >
                {INTERACTION_LABELS[action] ?? action}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* 操作反馈 */}
      {feedback && <div style={styles.feedback}>{feedback}</div>}

      {/* 额外属性（标签、位置） */}
      <div style={{ marginTop: 12 }}>
        <div style={styles.sectionTitle}>物品信息</div>
        <div style={styles.section}>
          <KV label="位置" value={`(${obj.position.x}, ${obj.position.y})`} />
          {obj.tags.length > 0 && (
            <KV label="标签" value={obj.tags.join(" · ")} />
          )}
        </div>
      </div>
    </div>
  );
}

function KV({ label, value }: { label: string; value: string }) {
  return (
    <div style={styles.kv}>
      <span style={{ color: "#8791a1", width: 40, flexShrink: 0 }}>{label}</span>
      <span style={{ wordBreak: "break-all" }}>{value}</span>
    </div>
  );
}

function labelType(t: string): string {
  const MAP: Record<string, string> = {
    furniture: "家具",
    facility: "设施",
    barrier: "障碍物",
    plant: "植物",
    decoration: "装饰",
    item: "物品",
    nature_spot: "自然景点",
  };
  return MAP[t] ?? t;
}

const styles: Record<string, React.CSSProperties> = {
  wrap: { padding: 14, overflow: "auto", fontSize: 13 },
  header: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    paddingBottom: 12,
    borderBottom: "1px solid rgba(255,255,255,0.06)",
  },
  icon: {
    fontSize: 28,
    lineHeight: 1,
    flexShrink: 0,
  },
  closeBtn: {
    marginLeft: "auto",
    background: "transparent",
    border: "none",
    color: "#8791a1",
    cursor: "pointer",
    fontSize: 14,
    padding: "2px 6px",
    borderRadius: 4,
  },
  sectionTitle: {
    fontSize: 11,
    color: "#8791a1",
    letterSpacing: 1,
    textTransform: "uppercase",
    marginBottom: 4,
  },
  badge: {
    fontSize: 11,
    padding: "3px 8px",
    borderRadius: 999,
    color: "#e0e6ef",
    border: "1px solid rgba(255,255,255,0.08)",
  },
  noticeBox: {
    fontSize: 12,
    color: "#cbd2db",
    lineHeight: 1.5,
    background: "rgba(255,255,255,0.04)",
    border: "1px solid rgba(255,255,255,0.08)",
    padding: "8px 10px",
    borderRadius: 6,
    marginTop: 4,
  },
  actions: { display: "flex", gap: 6, flexWrap: "wrap", marginTop: 4 },
  actionBtn: {
    padding: "6px 11px",
    fontSize: 12,
    border: "1px solid rgba(255,255,255,0.1)",
    background: "#1a2234",
    color: "#eef0f2",
    borderRadius: 999,
    cursor: "pointer",
    transition: "opacity 0.15s",
  },
  feedback: {
    marginTop: 10,
    fontSize: 12,
    color: "#a0b0c0",
    padding: "6px 10px",
    background: "rgba(255,255,255,0.04)",
    borderRadius: 6,
    border: "1px solid rgba(255,255,255,0.07)",
  },
  section: { display: "grid", gap: 5 },
  kv: { display: "flex", gap: 8, fontSize: 12 },
};
