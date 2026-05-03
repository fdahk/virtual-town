import { useEffect, useState } from "react";
import { obsApi } from "../../api";
import type { LLMCallRecord, TaskRecord, ToolCallRecord, TraceBundle } from "../../types/observability";
import { CopyableTruncatedId } from "./CopyableTruncatedId";


export function TraceDetail({
  traceId,
  onNavigate,
}: {
  traceId: string;
  onNavigate?: (traceId: string) => void;
}) {
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

  return <TraceView data={data} onNavigate={onNavigate} />;
}

// ---------------------------------------------------------------------------
// TraceView
// ---------------------------------------------------------------------------

/** 单条 span 行的统一形态，便于把 event / llm / tool / task 混入同一时间轴 */
interface SpanRow {
  id: string;
  startMs: number;    // 相对 traceStart 的开始时间（ms）
  durationMs: number; // 持续时间（ms）；若无已知耗时则取 0
  category: string;
  eventType: string;
  level: "INFO" | "WARNING" | "ERROR" | string;
  title: string;
  detail?: string;
}

function buildRows(data: TraceBundle): { rows: SpanRow[]; traceStart: number; totalMs: number } {
  const ts = (s: string | null | undefined) =>
    s ? new Date(s).getTime() : null;

  const allStartMs: number[] = [];
  const allEndMs: number[] = [];

  const eventRows: SpanRow[] = data.events.map((e) => {
    const t = ts(e.created_at) ?? 0;
    const d = e.duration_ms ?? 0;
    allStartMs.push(t);
    allEndMs.push(t + d);
    return {
      id: `evt-${e.id}`,
      startMs: t,
      durationMs: d,
      category: e.category,
      eventType: e.event_type,
      level: e.level,
      title: e.title ?? e.event_type,
    };
  });

  const llmRows: SpanRow[] = data.llm_calls.map((r) => {
    const t = ts(r.created_at) ?? 0;
    const d = r.latency_ms ?? 0;
    allStartMs.push(t);
    allEndMs.push(t + d);
    return {
      id: `llm-${r.id}`,
      startMs: t,
      durationMs: d,
      category: "llm_call",
      eventType: "llm.call",
      level: r.success ? "INFO" : "ERROR",
      title: `${r.model} · ${r.latency_ms}ms · ${r.success ? "ok" : "err:" + r.error_code}`,
      detail: r.input_summary ? r.input_summary.slice(0, 120) : undefined,
    };
  });

  const toolRows: SpanRow[] = data.tool_calls.map((r) => {
    const t = ts(r.created_at) ?? 0;
    const d = r.duration_ms ?? 0;
    allStartMs.push(t);
    allEndMs.push(t + d);
    return {
      id: `tool-${r.id}`,
      startMs: t,
      durationMs: d,
      category: "tool_call",
      eventType: r.tool,
      level: r.success ? "INFO" : "WARNING",
      title: `${r.tool} · agent=${r.caller_agent_id ?? "-"} · ${r.success ? "ok" : "fail:" + r.error_code}`,
    };
  });

  const taskRows: SpanRow[] = data.tasks.map((t) => {
    const start = ts(t.enqueued_at) ?? 0;
    const end = ts(t.finished_at) ?? ts(t.started_at) ?? start;
    const d = end - start;
    allStartMs.push(start);
    allEndMs.push(end);
    return {
      id: `task-${t.id}`,
      startMs: start,
      durationMs: Math.max(0, d),
      category: "task",
      eventType: t.task_type,
      level: t.status === "succeeded" ? "INFO" : t.status === "failed" || t.status === "timeout" ? "ERROR" : "INFO",
      title: `${t.task_type} · ${t.status} · entity=${t.entity_id ?? "-"}`,
      detail: t.last_error ?? undefined,
    };
  });

  const traceStart = Math.min(...allStartMs.filter(Boolean));
  const traceEnd = Math.max(...allEndMs.filter(Boolean));
  const totalMs = traceEnd - traceStart;

  const rows = [...eventRows, ...llmRows, ...toolRows, ...taskRows]
    .map((r) => ({ ...r, startMs: r.startMs - traceStart }))
    .sort((a, b) => a.startMs - b.startMs);

  return { rows, traceStart, totalMs };
}

function TraceView({
  data,
  onNavigate,
}: {
  data: TraceBundle;
  onNavigate?: (traceId: string) => void;
}) {
  const { rows, traceStart, totalMs } = buildRows(data);

  // 「关键路径」总耗时：把所有 span 的时间区间合并后测量（去掉并行重叠）
  const criticalPathMs = criticalPath(rows);

  // 找出所有任务的 parent_trace_id（可能有多条，取唯一值）
  const parentTraceIds = [...new Set(
    data.tasks
      .map((t) => t.parent_trace_id)
      .filter((id): id is string => !!id),
  )];

  return (
    <div style={styles.root}>
      {/* Header */}
      <div style={styles.header}>
        <div style={styles.traceId}>
          trace_id: <CopyableTruncatedId id={data.trace_id} full />
        </div>
        {parentTraceIds.length > 0 && (
          <div style={{ fontSize: 11, fontFamily: "monospace", color: "#8b949e", marginTop: 4 }}>
            触发方 trace:{" "}
            {parentTraceIds.map((pid) => (
              <button
                key={pid}
                type="button"
                title={`${pid}\n点击跳转到触发方 trace`}
                onClick={() => onNavigate?.(pid)}
                style={{
                  background: "none",
                  border: "1px solid #30363d",
                  borderRadius: 4,
                  color: "#79c0ff",
                  cursor: onNavigate ? "pointer" : "default",
                  fontSize: 11,
                  fontFamily: "monospace",
                  padding: "1px 6px",
                  marginRight: 4,
                }}
              >
                {pid.slice(0, 18)}…
              </button>
            ))}
          </div>
        )}
        <div style={styles.stats}>
          <StatBadge label="总 span 数" value={rows.length} />
          <StatBadge label="wall-clock" value={`${totalMs.toFixed(0)} ms`} color="#79c0ff" />
          <StatBadge label="关键路径（串行）" value={`${criticalPathMs.toFixed(0)} ms`} color="#f4b53f" />
          <StatBadge label="并行节省" value={`${Math.max(0, criticalPathMs - totalMs).toFixed(0)} ms`} color="#7bd389" />
          <StatBadge label="llm" value={data.llm_calls.length} />
          <StatBadge label="tool" value={data.tool_calls.length} />
          <StatBadge label="task" value={data.tasks.length} />
        </div>
      </div>

      {/* 时间刻度说明 */}
      <div style={styles.scaleNote}>
        时间轴起点 {new Date(traceStart).toLocaleTimeString()}，
        每行左侧 = 相对 trace 首事件的 wall-clock 偏移，横条宽度 = 事件持续时间（等比缩放）。
        并行的事件横条会在同一时区重叠。
      </div>

      {/* Waterfall */}
      <div style={styles.timeline}>
        {/* 刻度头 */}
        <ScaleHeader totalMs={totalMs} />

        {/* 行 */}
        {rows.map((row) => (
          <WaterfallRow key={row.id} row={row} totalMs={totalMs} />
        ))}
      </div>

      {/* 详情区（各类型分区，附加 LLM 摘要 / task 错误等扩展字段） */}
      {data.llm_calls.length > 0 && <LLMSection calls={data.llm_calls} />}
      {data.tool_calls.length > 0 && <ToolSection calls={data.tool_calls} />}
      {data.tasks.length > 0 && <TaskSection tasks={data.tasks} onNavigate={onNavigate} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 关键路径：把所有 span 的 [start, start+dur) 区间合并，测量覆盖总长（去掉并行重叠）
// ---------------------------------------------------------------------------

function criticalPath(rows: SpanRow[]): number {
  const intervals = rows
    .filter((r) => r.durationMs > 0)
    .map((r) => [r.startMs, r.startMs + r.durationMs] as [number, number])
    .sort((a, b) => a[0] - b[0]);

  let total = 0;
  let curEnd = -Infinity;
  for (const [s, e] of intervals) {
    if (s > curEnd) {
      total += e - s;
      curEnd = e;
    } else if (e > curEnd) {
      total += e - curEnd;
      curEnd = e;
    }
  }
  return total;
}

// ---------------------------------------------------------------------------
// 刻度头
// ---------------------------------------------------------------------------

function ScaleHeader({ totalMs }: { totalMs: number }) {
  const ticks = 5;
  return (
    <div style={{ ...styles.spanRow, borderBottom: "1px solid rgba(255,255,255,0.1)", marginBottom: 2 }}>
      {/* 左侧 meta 列 */}
      <span style={{ ...styles.metaCol, color: "#8b949e", fontSize: 10 }}>offset</span>
      <span style={{ ...styles.levelCol, fontSize: 10, color: "#8b949e" }}>lv</span>
      <span style={{ ...styles.catCol, fontSize: 10, color: "#8b949e" }}>category</span>
      <span style={{ ...styles.typeCol, fontSize: 10, color: "#8b949e" }}>type · title</span>
      {/* 甘特区 */}
      <div style={{ ...styles.barCol, position: "relative" }}>
        {Array.from({ length: ticks + 1 }, (_, i) => {
          const pct = (i / ticks) * 100;
          const label = `${((totalMs * i) / ticks).toFixed(0)}ms`;
          return (
            <span
              key={i}
              style={{
                position: "absolute",
                left: `${pct}%`,
                fontSize: 9,
                color: "#444e5e",
                transform: "translateX(-50%)",
                userSelect: "none",
              }}
            >
              {label}
            </span>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 单行 Waterfall
// ---------------------------------------------------------------------------

const CATEGORY_COLOR: Record<string, string> = {
  llm_call: "#a78bfa",
  tool_call: "#fb923c",
  task: "#38bdf8",
  world_event: "#4ade80",
  agent_decision: "#f472b6",
  memory: "#fbbf24",
  error: "#f87171",
};
const DEFAULT_BAR_COLOR = "#6b7280";

const LEVEL_COLOR: Record<string, string> = {
  ERROR: "#ff7a59",
  WARNING: "#f4b53f",
  INFO: "#7bd389",
};

function WaterfallRow({ row, totalMs }: { row: SpanRow; totalMs: number }) {
  const [hovered, setHovered] = useState(false);

  const barColor = CATEGORY_COLOR[row.category] ?? DEFAULT_BAR_COLOR;
  const levelColor = LEVEL_COLOR[row.level] ?? "#8b949e";

  // 横条位置（百分比）
  const leftPct = totalMs > 0 ? (row.startMs / totalMs) * 100 : 0;
  // 最小 0.3% 保证可见
  const widthPct = totalMs > 0 ? Math.max((row.durationMs / totalMs) * 100, 0.3) : 0.3;

  const offsetLabel = row.startMs < 1000
    ? `${row.startMs.toFixed(0)}ms`
    : `${(row.startMs / 1000).toFixed(2)}s`;

  return (
    <div
      style={{
        ...styles.spanRow,
        background: hovered ? "rgba(255,255,255,0.03)" : "transparent",
      }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      title={`${row.category} · ${row.eventType}\n+${row.startMs.toFixed(0)}ms, ${row.durationMs}ms\n${row.title}${row.detail ? "\n" + row.detail : ""}`}
    >
      {/* offset */}
      <span style={{ ...styles.metaCol, color: "#8b949e" }}>{offsetLabel}</span>

      {/* level badge */}
      <span style={{ ...styles.levelCol, color: levelColor }}>
        {row.level.slice(0, 4)}
      </span>

      {/* category */}
      <span style={{ ...styles.catCol, color: barColor }}>
        {row.category}
      </span>

      {/* type + title */}
      <span style={styles.typeCol} title={row.title}>
        <span style={{ color: "#c0caf5" }}>{row.eventType}</span>
        {row.durationMs > 0 && (
          <span style={{ color: "#7bd389", marginLeft: 4 }}>{row.durationMs}ms</span>
        )}
        <span style={{ color: "#8b949e", marginLeft: 4 }}>{row.title}</span>
      </span>

      {/* Waterfall bar */}
      <div style={{ ...styles.barCol, position: "relative" }}>
        {/* 背景网格线 */}
        {[25, 50, 75].map((pct) => (
          <div
            key={pct}
            style={{
              position: "absolute",
              left: `${pct}%`,
              top: 0,
              bottom: 0,
              width: 1,
              background: "rgba(255,255,255,0.04)",
              pointerEvents: "none",
            }}
          />
        ))}
        {/* 实际 bar */}
        {row.durationMs > 0 ? (
          <div
            style={{
              position: "absolute",
              left: `${leftPct}%`,
              width: `${widthPct}%`,
              top: "20%",
              bottom: "20%",
              background: barColor,
              opacity: 0.75,
              borderRadius: 2,
              minWidth: 3,
              transition: "opacity 0.1s",
            }}
          />
        ) : (
          /* 无 duration：画菱形标记 */
          <div
            style={{
              position: "absolute",
              left: `${leftPct}%`,
              top: "50%",
              transform: "translate(-50%, -50%) rotate(45deg)",
              width: 6,
              height: 6,
              background: barColor,
              opacity: 0.9,
            }}
          />
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 详情区
// ---------------------------------------------------------------------------

function LLMSection({ calls }: { calls: LLMCallRecord[] }) {
  return (
    <Section title="LLM 调用详情">
      {calls.map((r) => (
        <div key={r.id} style={styles.detailBox}>
          <div style={styles.detailRow}>
            <b style={{ color: "#a78bfa" }}>{r.model}</b> · {r.provider} · {r.caller_module ?? "-"}
          </div>
          <div style={styles.detailRow}>
            latency={r.latency_ms}ms · retry={r.retry_count} ·
            success=<span style={{ color: r.success ? "#7bd389" : "#ff7a59" }}>{String(r.success)}</span> ·
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
    </Section>
  );
}

function ToolSection({ calls }: { calls: ToolCallRecord[] }) {
  return (
    <Section title="Tool 调用详情">
      {calls.map((r) => (
        <div key={r.id} style={styles.detailBox}>
          <div style={styles.detailRow}>
            <b style={{ color: "#fb923c" }}>{r.tool}</b> · agent={r.caller_agent_id ?? "-"} · duration={r.duration_ms}ms
          </div>
          <div style={styles.detailRow}>
            schema={String(r.schema_valid)} · perm={String(r.permission_valid)} ·
            world={String(r.world_state_valid)} ·
            success=<span style={{ color: r.success ? "#7bd389" : "#ff7a59" }}>{String(r.success)}</span>
          </div>
          <pre style={styles.pre}>{JSON.stringify(r.arguments, null, 2)}</pre>
          {r.error_code && (
            <div style={{ ...styles.detailRow, color: "#ff7a59" }}>error: {r.error_code}</div>
          )}
          {r.result_summary && (
            <div style={{ ...styles.detailRow, color: "#8b949e" }}>result: {r.result_summary}</div>
          )}
        </div>
      ))}
    </Section>
  );
}

function TaskSection({
  tasks,
  onNavigate,
}: {
  tasks: TaskRecord[];
  onNavigate?: (traceId: string) => void;
}) {
  return (
    <Section title="任务详情">
      {tasks.map((t) => (
        <div key={t.id} style={styles.detailBox}>
          <div style={styles.detailRow}>
            <b style={{ color: "#38bdf8" }}>{t.task_type}</b> · entity={t.entity_id ?? "-"} ·
            status=<span style={{
              color: t.status === "succeeded" ? "#7bd389" : t.status === "failed" || t.status === "timeout" ? "#ff7a59" : "#f4b53f",
            }}>{t.status}</span> ·
            retry={t.retry_count}/{t.max_retries}
          </div>
          {t.enqueued_at && (
            <div style={{ ...styles.detailRow, color: "#8b949e", fontSize: 11 }}>
              enqueued={new Date(t.enqueued_at).toLocaleTimeString()}
              {t.started_at && (
                <>
                  {" · "}waited={((new Date(t.started_at).getTime() - new Date(t.enqueued_at).getTime()) / 1000).toFixed(1)}s
                  {" · "}started={new Date(t.started_at).toLocaleTimeString()}
                </>
              )}
              {t.finished_at && ` · finished=${new Date(t.finished_at).toLocaleTimeString()}`}
            </div>
          )}
          {t.parent_trace_id && (
            <div style={{ ...styles.detailRow, color: "#8b949e", fontSize: 11 }}>
              触发方 trace:{" "}
              <button
                type="button"
                title={`${t.parent_trace_id}\n点击跳转到触发方 trace（world_tick）`}
                onClick={() => onNavigate?.(t.parent_trace_id!)}
                style={{
                  background: "none",
                  border: "none",
                  color: onNavigate ? "#79c0ff" : "#8b949e",
                  cursor: onNavigate ? "pointer" : "default",
                  fontSize: 11,
                  fontFamily: "monospace",
                  padding: 0,
                  textDecoration: onNavigate ? "underline" : "none",
                }}
              >
                {t.parent_trace_id.slice(0, 20)}…
              </button>
            </div>
          )}
          {t.last_error && (
            <div style={{ ...styles.detailRow, color: "#ff7a59" }}>error: {t.last_error}</div>
          )}
        </div>
      ))}
    </Section>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 style={styles.section}>{title}</h3>
      {children}
    </section>
  );
}

function StatBadge({ label, value, color = "#e6edf3" }: { label: string; value: string | number; color?: string }) {
  return (
    <span style={{ fontSize: 11, fontFamily: "monospace", color: "#8b949e" }}>
      {label}:{" "}
      <span style={{ color, fontWeight: 600 }}>{value}</span>
    </span>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const styles: Record<string, React.CSSProperties> = {
  root: { display: "flex", flexDirection: "column", gap: 12 },
  header: {
    background: "#11161e",
    border: "1px solid rgba(255,255,255,0.06)",
    borderRadius: 6,
    padding: 10,
    display: "flex",
    flexDirection: "column",
    gap: 8,
  },
  traceId: { fontFamily: "monospace", fontSize: 13 },
  stats: { display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center" },
  scaleNote: {
    fontSize: 11,
    color: "#8b949e",
    padding: "4px 0",
    lineHeight: 1.5,
  },
  section: {
    fontSize: 11,
    color: "#8b949e",
    margin: "4px 0 6px",
    letterSpacing: 1,
    textTransform: "uppercase" as const,
  },
  timeline: {
    background: "#0c1118",
    border: "1px solid rgba(255,255,255,0.04)",
    borderRadius: 6,
    padding: "8px 8px 4px",
    overflowX: "auto",
    overflowY: "auto",
    maxHeight: "60vh",
  },
  spanRow: {
    display: "grid",
    gridTemplateColumns: "64px 38px 100px 280px 1fr",
    gap: 6,
    padding: "3px 4px",
    fontSize: 11,
    fontFamily: "monospace",
    borderBottom: "1px solid rgba(255,255,255,0.03)",
    alignItems: "center",
    minWidth: 900,
  },
  metaCol: { textAlign: "right" as const, whiteSpace: "nowrap" as const },
  levelCol: { textAlign: "center" as const },
  catCol: { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" as const },
  typeCol: {
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap" as const,
    fontSize: 11,
  },
  barCol: {
    height: 20,
    position: "relative" as const,
    minWidth: 200,
  },
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
