import { useState } from "react";
import { api } from "../api";
import { useWorldStore } from "../stores/worldStore";

const SUGGESTED_TRAITS = [
  "好奇",
  "友善",
  "理性",
  "热情",
  "内向",
  "幽默",
  "勇敢",
  "细腻",
];

export function CreatePlayerPage({ onCreated }: { onCreated(): void }) {
  const [name, setName] = useState("旅人");
  const [traits, setTraits] = useState<string[]>(["好奇", "友善"]);
  const [background, setBackground] = useState("");
  const [appearance, setAppearance] = useState("一位刚到小镇的访客");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const setPlayer = useWorldStore((s) => s.setPlayer);

  const toggleTrait = (t: string) => {
    setTraits((prev) =>
      prev.includes(t) ? prev.filter((x) => x !== t) : prev.concat(t),
    );
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      setError("请填写角色名称");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const profile = await api.createPlayer({
        name: name.trim(),
        personality: traits,
        appearance_description: appearance,
        background,
      });
      setPlayer(profile);
      onCreated();
    } catch (err) {
      setError((err as { message?: string }).message ?? "创建失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={styles.page}>
      <div style={styles.card}>
        <h1 style={{ margin: 0, fontSize: 24 }}>欢迎来到 AI 小镇</h1>
        <p style={{ opacity: 0.75, marginTop: 6 }}>
          创建你的角色，然后走进这个 2D 小镇。NPC 会记得你做过的事。
        </p>
        <form onSubmit={submit} style={{ display: "grid", gap: 14, marginTop: 18 }}>
          <label style={styles.label}>
            <span>名字</span>
            <input
              style={styles.input}
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={20}
              autoFocus
            />
          </label>

          <div style={styles.label}>
            <span>性格（多选）</span>
            <div style={styles.traitRow}>
              {SUGGESTED_TRAITS.map((t) => (
                <button
                  type="button"
                  key={t}
                  onClick={() => toggleTrait(t)}
                  style={{
                    ...styles.trait,
                    background: traits.includes(t) ? "#3c74ff" : "#1c2127",
                  }}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>

          <label style={styles.label}>
            <span>外貌描述</span>
            <input
              style={styles.input}
              value={appearance}
              onChange={(e) => setAppearance(e.target.value)}
              maxLength={140}
            />
          </label>

          <label style={styles.label}>
            <span>背景故事（可选）</span>
            <textarea
              style={{ ...styles.input, resize: "vertical", minHeight: 80 }}
              value={background}
              onChange={(e) => setBackground(e.target.value)}
              maxLength={500}
            />
          </label>

          {error && <div style={styles.error}>{error}</div>}

          <button type="submit" style={styles.submit} disabled={busy}>
            {busy ? "进入小镇中……" : "进入小镇"}
          </button>
        </form>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  page: {
    width: "100%",
    height: "100%",
    display: "grid",
    placeItems: "center",
    background:
      "radial-gradient(ellipse at top, #1d2736 0%, #0b0f14 60%, #070a10 100%)",
  },
  card: {
    width: 520,
    maxWidth: "92vw",
    padding: "28px 32px",
    background: "rgba(20, 25, 32, 0.85)",
    borderRadius: 14,
    boxShadow: "0 10px 40px rgba(0,0,0,0.5)",
    border: "1px solid rgba(255,255,255,0.05)",
    backdropFilter: "blur(8px)",
  },
  label: {
    display: "grid",
    gap: 6,
    fontSize: 13,
    color: "#cbd2db",
  },
  input: {
    padding: "9px 12px",
    borderRadius: 8,
    background: "#0d1117",
    color: "#f5f5f5",
    border: "1px solid rgba(255,255,255,0.1)",
    outline: "none",
    fontSize: 14,
  },
  traitRow: {
    display: "flex",
    gap: 8,
    flexWrap: "wrap",
  },
  trait: {
    padding: "6px 12px",
    color: "#fff",
    border: "none",
    borderRadius: 999,
    fontSize: 13,
    cursor: "pointer",
    transition: "background 0.2s",
  },
  submit: {
    marginTop: 8,
    padding: "11px 16px",
    background: "linear-gradient(120deg, #4b6fff, #8151ff)",
    color: "white",
    border: "none",
    borderRadius: 10,
    fontSize: 15,
    fontWeight: 600,
    cursor: "pointer",
  },
  error: {
    color: "#ff8080",
    fontSize: 13,
  },
};
