import { useEffect, useState } from "react";
import { obsApi } from "../../api";
import type { TraceBundle } from "../../types/observability";

export function TraceDetail({ traceId }: { traceId: string }) {
  const [data, setData] = useState<TraceBundle | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setData(null);
    setErr(null);
    obsApi
      .getTrace(traceId)
      .then(setData)
      .catch((e) => setErr(String((e as { message?: string }).message ?? e)));
  }, [traceId]);

  if (err) {
    return (
      <div style={{ padding: 16, color: "#ff7a59", fontSize: 12 }}>
        查询失败：{err}
      </div>
    );
  }
  if (!data) return <div style={{ padding: 16, opacity: 0.7 }}>加载中……</div>;

  const startTs = data.events[0]?.created_at
    ? new Date(data.events[0].created_at).getTime()
    : null;

  return (
    <div style={styles.root}>
      <div style={styles.header}>
        <div style={styles.traceId}>trace_id: {data.trace_id}</div>
        <div style={{ fontSize: 12, opacity: 0.7 }}>
          events {data.events.length} · llm {data.llm_calls.length} · tool {data.tool_calls.length} · task {data.tasks.length}
        </div>
      </div>

      <section>
        <h3 style={styles.section}>Span 时间线</h3>
        <div style={styles.timeline}>
          {data.events.map((e) => {
            const t = e.created_at ? new Date(e.created_at).getTime() : null;
            const offset = startTs && t ? t - startTs : 0;
            return (
              <div key={e.id} style={styles.spanRow}>
                <span style={styles.offset}>{offset.toFixed(0)}ms</span>
                <span style={styles.level}>{e.level}</span>
                <span style={styles.category}>{e.category}</span>
                <span style={styles.eventType}>{e.event_type}</span>
                {e.duration_ms != null && (
                  <span style={styles.duration}>{e.duration_ms}ms</span>
                )}
                <span style={styles.title}>{e.title ?? ""}</span>
              </div>
            );
          })}
        </div>
      </section>

      {data.llm_calls.length > 0 && (
        <section>
          <h3 style={styles.section}>LLM 调用</h3>
          {data.llm_calls.map((r) => (
            <div key={r.id} style={styles.detailBox}>
              <div style={styles.detailRow}>
                <b>{r.model}</b> · {r.provider} · {r.caller_module ?? "-"}
              </div>
              <div style={styles.detailRow}>
                latency={r.latency_ms}ms · retry={r.retry_count} · success={String(r.success)} ·
                schema_valid={String(r.schema_valid)} · fallback={String(r.fallback_used)}
              </div>
              {r.input_summary && (
                <div style={{ ...styles.detailRow, color: "#8b949e" }}>
                  input: {r.input_summary.slice(0, 200)}
                </div>
              )}
              {r.error_code && (
                <div style={{ ...styles.detailRow, color: "#ff7a59" }}>
                  error: {r.error_code}
                </div>
              )}
            </div>
          ))}
        </section>
      )}

      {data.tool_calls.length > 0 && (
        <section>
          <h3 style={styles.section}>Tool 调用</h3>
          {data.tool_calls.map((r) => (
            <div key={r.id} style={styles.detailBox}>
              <div style={styles.detailRow}>
                <b>{r.tool}</b> · agent={r.caller_agent_id ?? "-"} · duration={r.duration_ms}ms
              </div>
              <div style={styles.detailRow}>
                schema={String(r.schema_valid)} · perm={String(r.permission_valid)} ·
                world={String(r.world_state_valid)} · success={String(r.success)}
              </div>
              <pre style={styles.pre}>
                {JSON.stringify(r.arguments, null, 2)}
              </pre>
              {r.error_code && (
                <div style={{ ...styles.detailRow, color: "#ff7a59" }}>
                  error: {r.error_code}
                </div>
              )}
              {r.result_summary && (
                <div style={{ ...styles.detailRow, color: "#8b949e" }}>
                  result: {r.result_summary}
                </div>
              )}
            </div>
          ))}
        </section>
      )}

      {data.tasks.length > 0 && (
        <section>
          <h3 style={styles.section}>任务</h3>
          {data.tasks.map((t) => (
            <div key={t.id} style={styles.detailBox}>
              <div style={styles.detailRow}>
                <b>{t.task_type}</b> · status={t.status} · retry={t.retry_count}/{t.max_retries}
              </div>
              {t.last_error && (
                <div style={{ ...styles.detailRow, color: "#ff7a59" }}>
                  error: {t.last_error.slice(0, 200)}
                </div>
              )}
            </div>
          ))}
        </section>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  root: { display: "flex", flexDirection: "column", gap: 12 },
  header: {
    background: "#11161e",
    border: "1px solid rgba(255,255,255,0.06)",
    borderRadius: 6,
    padding: 10,
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
  },
  traceId: { fontFamily: "monospace", fontSize: 13 },
  section: { fontSize: 12, color: "#8b949e", margin: "4px 0 6px", letterSpacing: 1, textTransform: "uppercase" },
  timeline: {
    background: "#0c1118",
    border: "1px solid rgba(255,255,255,0.04)",
    borderRadius: 6,
    padding: 8,
    maxHeight: 320,
    overflow: "auto",
  },
  spanRow: {
    display: "grid",
    gridTemplateColumns: "70px 70px 120px 200px 70px 1fr",
    gap: 8,
    padding: "4px 6px",
    fontSize: 12,
    fontFamily: "monospace",
    borderBottom: "1px solid rgba(255,255,255,0.04)",
  },
  offset: { color: "#8b949e", textAlign: "right" },
  level: { color: "#f4b53f" },
  category: { color: "#79c0ff" },
  eventType: { color: "#c0caf5" },
  duration: { color: "#7bd389", textAlign: "right" },
  title: { color: "#e6edf3", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },
  detailBox: {
    background: "#0c1118",
    border: "1px solid rgba(255,255,255,0.04)",
    borderRadius: 4,
    padding: 8,
    marginBottom: 6,
    fontSize: 12,
    fontFamily: "monospace",
  },
  detailRow: { padding: "2px 0" },
  pre: {
    background: "#070a10",
    padding: 8,
    borderRadius: 4,
    fontSize: 11,
    overflow: "auto",
    maxHeight: 180,
  },
};
