import { useCallback, useEffect, useRef, useState } from "react";
import { useWorldStore } from "../stores/worldStore";
import { api } from "../api";
import { usePortrait } from "../game/assets/usePortrait";

interface ChatItem {
  side: "me" | "npc" | "system";
  text: string;
  emotion?: string | null;
  citations?: Array<{ description: string; score: number }>;
}

type Phase = "requesting" | "accepted" | "soft_declined" | "hard_declined";

export function ChatPanel() {
  const pending = useWorldStore((s) => s.pendingDialogue);
  const setPending = useWorldStore((s) => s.setPendingDialogue);
  const [messages, setMessages] = useState<ChatItem[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>("requesting");
  const [decline, setDecline] = useState<{ kind: "soft" | "hard"; line: string | null; reason: string | null } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const portrait = usePortrait(pending?.targetId ?? null);

  const handleClose = useCallback(() => {
    if (pending?.targetId && phase === "accepted") {
      // 释放 NPC 的 CHATTING 状态，让 NPC 恢复自主行动
      api.endChat({ npc_id: pending.targetId }).catch(() => null);
    }
    setPending(null);
  }, [pending, setPending, phase]);

  // 阶段 19：进入面板时先发起 interaction request 评估
  useEffect(() => {
    if (!pending) return;
    setMessages([]);
    setInput("");
    setError(null);
    setDecline(null);
    setPhase("requesting");
    setBusy(true);
    api
      .requestInteraction({
        target_entity_id: pending.targetId,
        kind: "chat",
        reason: "玩家点击发起对话",
      })
      .then((resp) => {
        if (resp.status === "accepted") {
          setPhase("accepted");
          setTimeout(() => inputRef.current?.focus(), 10);
        } else {
          const kind = resp.decline_kind === "soft" ? "soft" : "hard";
          setPhase(kind === "soft" ? "soft_declined" : "hard_declined");
          setDecline({
            kind,
            line: resp.npc_line ?? null,
            reason: resp.reason ?? null,
          });
        }
      })
      .catch((err) => {
        const e = err as { code?: string; message?: string };
        setError(e.message ?? "请求失败");
        setPhase("hard_declined");
        setDecline({ kind: "hard", line: null, reason: e.message ?? null });
      })
      .finally(() => setBusy(false));
    // 仅在新目标上重置请求；依赖完整 pending 会因 zustand 引用变化产生多余重试
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 与 handleClose 不同，此处只关心 targetId
  }, [pending?.targetId]);

  // Esc 键关闭对话
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && pending) handleClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pending, handleClose]);

  if (!pending) return null;

  const send = async () => {
    const text = input.trim();
    if (!text || busy || phase !== "accepted") return;
    setBusy(true);
    setError(null);
    setMessages((prev) => prev.concat({ side: "me", text }));
    setInput("");
    try {
      const resp = await api.talkPlayer({
        target_entity_id: pending.targetId,
        text,
      });
      setMessages((prev) =>
        prev.concat({
          side: "npc",
          text: resp.reply,
          emotion: resp.emotion,
          citations: resp.citations?.slice(0, 2),
        }),
      );
    } catch (err) {
      const e = err as { code?: string; message?: string };
      setError(e.message ?? "对话失败");
      if (e.code === "OUT_OF_RANGE") {
        setError("离得太远了，先走近一点。");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={styles.overlay}>
      <div style={styles.panel}>
        <div style={styles.header}>
          <div style={{ fontWeight: 600 }}>与「{pending.targetName}」对话</div>
          <button style={styles.close} onClick={handleClose}>
            ×
          </button>
        </div>
        <div style={styles.bodyRow}>
          <div style={styles.portraitCol}>
            {portrait ? (
              <img src={portrait} alt={pending.targetName} style={styles.portraitImg} />
            ) : (
              <div style={styles.portraitPlaceholder}>
                <span style={{ fontSize: 12, opacity: 0.65 }}>{pending.targetName}</span>
              </div>
            )}
            <div style={styles.portraitName}>{pending.targetName}</div>
          </div>
        <div style={styles.body}>
          {phase === "requesting" && (
            <div style={{ opacity: 0.7, fontSize: 13, textAlign: "center", marginTop: 60 }}>
              <div style={{ marginBottom: 8 }}>正在询问 {pending.targetName} 是否方便…</div>
              <div style={{ fontSize: 11, opacity: 0.55 }}>对方需要根据当前状态决定是否接受</div>
            </div>
          )}
          {phase === "soft_declined" && (
            <div style={{ marginTop: 30, padding: "12px 16px" }}>
              <div style={{ fontSize: 13, color: "#cbd2db", marginBottom: 6 }}>
                {pending.targetName} 暂时没空：
              </div>
              <div style={{ ...styles.bubble, alignSelf: "flex-start", background: "#1a222f", color: "#fff" }}>
                {decline?.line ?? "等我忙完再聊吧。"}
              </div>
              <div style={{ fontSize: 11, opacity: 0.45, marginTop: 12 }}>
                建议稍后再来。
              </div>
            </div>
          )}
          {phase === "hard_declined" && (
            <div style={{ marginTop: 30, padding: "12px 16px" }}>
              <div style={{ fontSize: 13, color: "#ffae8a" }}>
                {decline?.line ?? `${pending.targetName} 现在无法回应你。`}
              </div>
              <div style={{ fontSize: 11, opacity: 0.5, marginTop: 8 }}>
                {decline?.reason ?? ""}
              </div>
            </div>
          )}
          {phase === "accepted" && messages.length === 0 && (
            <div style={{ opacity: 0.55, fontSize: 13, textAlign: "center", marginTop: 40 }}>
              问问他 / 她一个问题吧，比如「小王喜欢喝什么？」
            </div>
          )}
          {phase === "accepted" &&
            messages.map((m, i) => (
              <div
                key={i}
                style={{
                  ...styles.bubble,
                  alignSelf: m.side === "me" ? "flex-end" : "flex-start",
                  background: m.side === "me" ? "#3c74ff" : "#1a222f",
                  color: "#fff",
                }}
              >
                <div>{m.text}</div>
                {m.emotion && (
                  <div style={{ fontSize: 10, marginTop: 4, opacity: 0.7 }}>
                    情绪：{m.emotion}
                  </div>
                )}
                {m.citations && m.citations.length > 0 && (
                  <div style={{ fontSize: 10, marginTop: 6, opacity: 0.65 }}>
                    引用记忆：
                    <ul style={{ margin: "4px 0", paddingLeft: 14 }}>
                      {m.citations.map((c, j) => (
                        <li key={j}>
                          {c.description}（{c.score.toFixed(2)}）
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            ))}
        </div>
        </div>
        {error && <div style={styles.error}>{error}</div>}
        {phase === "accepted" ? (
          <div style={styles.inputRow}>
            <input
              ref={inputRef}
              style={styles.input}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && send()}
              placeholder="输入你想说的话（Enter 发送）"
              maxLength={300}
              disabled={busy}
            />
            <button style={styles.send} onClick={send} disabled={busy || !input.trim()}>
              {busy ? "对方思考中" : "发送"}
            </button>
          </div>
        ) : (
          <div style={styles.inputRow}>
            <button style={{ ...styles.send, marginLeft: "auto" }} onClick={handleClose}>
              {phase === "requesting" ? "取消请求" : "关闭"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  overlay: {
    position: "fixed",
    inset: 0,
    background: "rgba(0,0,0,0.35)",
    display: "grid",
    placeItems: "center",
    zIndex: 100,
  },
  panel: {
    width: 680,
    maxWidth: "94vw",
    height: 560,
    maxHeight: "82vh",
    background: "#0e1218",
    borderRadius: 12,
    display: "grid",
    gridTemplateRows: "auto 1fr auto auto",
    border: "1px solid rgba(255,255,255,0.08)",
    boxShadow: "0 20px 60px rgba(0,0,0,0.5)",
  },
  bodyRow: {
    display: "grid",
    gridTemplateColumns: "180px 1fr",
    gap: 0,
    overflow: "hidden",
  },
  portraitCol: {
    padding: "14px 12px",
    borderRight: "1px solid rgba(255,255,255,0.06)",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: 8,
    background: "rgba(255,255,255,0.02)",
  },
  portraitImg: {
    width: 156,
    height: 188,
    objectFit: "cover",
    objectPosition: "center top",
    borderRadius: 8,
    boxShadow: "0 4px 16px rgba(0,0,0,0.35)",
  },
  portraitPlaceholder: {
    width: 156,
    height: 188,
    borderRadius: 8,
    background: "linear-gradient(150deg,#1b2431,#222b3a)",
    display: "grid",
    placeItems: "center",
    boxShadow: "0 4px 16px rgba(0,0,0,0.35)",
  },
  portraitName: {
    fontSize: 13,
    color: "#cbd2db",
    fontWeight: 600,
  },
  header: {
    padding: "12px 16px",
    borderBottom: "1px solid rgba(255,255,255,0.06)",
    display: "flex",
    alignItems: "center",
  },
  close: {
    marginLeft: "auto",
    background: "transparent",
    border: "none",
    color: "#aaa",
    fontSize: 20,
    cursor: "pointer",
  },
  body: {
    padding: 14,
    display: "flex",
    flexDirection: "column",
    gap: 8,
    overflow: "auto",
  },
  bubble: {
    maxWidth: "80%",
    padding: "8px 12px",
    borderRadius: 12,
    fontSize: 14,
    lineHeight: 1.45,
    whiteSpace: "pre-wrap",
  },
  inputRow: {
    display: "flex",
    gap: 6,
    padding: 12,
    borderTop: "1px solid rgba(255,255,255,0.06)",
  },
  input: {
    flex: 1,
    padding: "8px 10px",
    borderRadius: 6,
    background: "#0a0e14",
    color: "#fff",
    border: "1px solid rgba(255,255,255,0.08)",
    outline: "none",
    fontSize: 14,
  },
  send: {
    padding: "8px 14px",
    background: "#3c74ff",
    color: "white",
    border: "none",
    borderRadius: 6,
    cursor: "pointer",
  },
  error: { color: "#ff8080", fontSize: 12, padding: "0 14px 6px" },
};
