import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import type { Memory, MemorySearchResult } from "../types/domain";

export function MemoryViewer({
  agentId,
  onClose,
}: {
  agentId: string;
  onClose(): void;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<MemorySearchResult[] | null>(null);
  const listQuery = useQuery({
    queryKey: ["memory", agentId],
    queryFn: () => api.listAgentMemories(agentId, 30),
  });

  const search = async () => {
    if (!query.trim()) {
      setResults(null);
      return;
    }
    const r = await api.searchAgentMemories(agentId, query.trim());
    setResults(r);
  };

  return (
    <div style={styles.overlay}>
      <div style={styles.panel}>
        <div style={styles.header}>
          <div style={{ fontWeight: 600 }}>记忆查看器</div>
          <button style={styles.close} onClick={onClose}>×</button>
        </div>
        <div style={styles.searchRow}>
          <input
            style={styles.input}
            value={query}
            placeholder="语义搜索 NPC 记忆（例：小王喜欢喝什么）"
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && search()}
          />
          <button style={styles.btn} onClick={search}>
            搜索
          </button>
        </div>
        <div style={styles.body}>
          {results ? (
            <SearchResults results={results} />
          ) : (
            <ListAll list={listQuery.data ?? []} />
          )}
        </div>
      </div>
    </div>
  );
}

function SearchResults({ results }: { results: MemorySearchResult[] }) {
  if (!results.length) {
    return <div style={styles.empty}>没有匹配的记忆。</div>;
  }
  return (
    <div style={{ display: "grid", gap: 8 }}>
      {results.map((r) => (
        <div key={r.memory.id} style={styles.memCard}>
          <div style={{ fontSize: 13 }}>{r.memory.description}</div>
          <div style={styles.scoreRow}>
            <span>总分 {r.score.toFixed(2)}</span>
            <span>相关 {r.score_detail.relevance.toFixed(2)}</span>
            <span>重要 {r.score_detail.importance.toFixed(2)}</span>
            <span>新近 {r.score_detail.recency.toFixed(2)}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

function ListAll({ list }: { list: Memory[] }) {
  if (!list.length) return <div style={styles.empty}>暂无记忆。</div>;
  return (
    <div style={{ display: "grid", gap: 6 }}>
      {list.map((m) => (
        <div key={m.id} style={styles.memCard}>
          <div style={{ fontSize: 13 }}>{m.description}</div>
          <div style={styles.scoreRow}>
            <span>{labelType(m.memory_type)}</span>
            <span>重要 {m.importance}</span>
            <span>{labelScope(m.scope)}</span>
            <span>{new Date(m.created_at).toLocaleString()}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

function labelType(t: string): string {
  return { event: "事件", thought: "想法", chat: "对话", summary: "总结" }[t] ?? t;
}
function labelScope(s: string): string {
  return { working: "工作", short_term: "短期", long_term: "长期" }[s] ?? s;
}

const styles: Record<string, React.CSSProperties> = {
  overlay: {
    position: "fixed",
    inset: 0,
    background: "rgba(0,0,0,0.5)",
    display: "grid",
    placeItems: "center",
    zIndex: 120,
  },
  panel: {
    width: 640,
    maxWidth: "92vw",
    height: 620,
    maxHeight: "85vh",
    background: "#0e1218",
    borderRadius: 12,
    display: "grid",
    gridTemplateRows: "auto auto 1fr",
    border: "1px solid rgba(255,255,255,0.08)",
  },
  header: {
    padding: "12px 16px",
    borderBottom: "1px solid rgba(255,255,255,0.06)",
    display: "flex",
  },
  close: {
    marginLeft: "auto",
    background: "transparent",
    border: "none",
    color: "#aaa",
    fontSize: 20,
    cursor: "pointer",
  },
  searchRow: { display: "flex", gap: 6, padding: 12 },
  input: {
    flex: 1,
    padding: "8px 10px",
    borderRadius: 6,
    background: "#0a0e14",
    color: "#fff",
    border: "1px solid rgba(255,255,255,0.08)",
  },
  btn: {
    padding: "6px 14px",
    background: "#3c74ff",
    color: "#fff",
    border: "none",
    borderRadius: 6,
    cursor: "pointer",
  },
  body: { padding: 14, overflow: "auto" },
  memCard: {
    padding: 10,
    background: "rgba(255,255,255,0.04)",
    borderRadius: 6,
  },
  scoreRow: { display: "flex", gap: 10, fontSize: 11, opacity: 0.7, marginTop: 4 },
  empty: { color: "#8791a1", fontSize: 13 },
};
