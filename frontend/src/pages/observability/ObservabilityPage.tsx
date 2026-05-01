import { useEffect, useMemo, useState } from "react";
import { obsApi } from "../../api";
import { observabilitySocket } from "../../api/websocket";
import type {
  DashboardPayload,
  HealthReport,
  LLMCallRecord,
  ObservabilityEvent,
  TaskRecord,
  ToolCallRecord,
} from "../../types/observability";
import type { WorldEvent } from "../../types/domain";
import { CopyableTruncatedId, copyObservabilityText } from "./CopyableTruncatedId";
import { TraceDetail } from "./TraceDetail";
import { AgentRuntimeCard } from "./AgentRuntimeCard";

type TabId =
  | "dashboard"
  | "world-events"
  | "events"
  | "agents"
  | "traces"
  | "llm"
  | "tool"
  | "tasks"
  | "errors"
  | "ws"
  | "health";

const TABS: Array<{ id: TabId; label: string }> = [
  { id: "dashboard", label: "Dashboard" },
  { id: "world-events", label: "World Events" },
  { id: "events", label: "Obs Events" },
  { id: "agents", label: "Agent Runtime" },
  { id: "traces", label: "Traces" },
  { id: "llm", label: "LLM Calls" },
  { id: "tool", label: "Tool Calls" },
  { id: "tasks", label: "Tasks" },
  { id: "errors", label: "Errors" },
  { id: "ws", label: "WS Monitor" },
  { id: "health", label: "Health" },
];

export function ObservabilityPage() {
  const [tab, setTab] = useState<TabId>("dashboard");
  const [liveEventCount, setLiveEventCount] = useState(0);
  const [liveConnected, setLiveConnected] = useState(false);

  useEffect(() => {
    observabilitySocket.connect("*");
    const unsubStatus = observabilitySocket.onStatusChange((s) =>
      setLiveConnected(s.connected),
    );
    const unsub = observabilitySocket.subscribe((type) => {
      if (type === "observability.event_created") {
        setLiveEventCount((n) => n + 1);
      }
    });
    return () => {
      unsub();
      unsubStatus();
      observabilitySocket.close();
    };
  }, []);

  return (
    <div style={styles.root}>
      <header style={styles.header}>
        <div style={styles.brand}>AI 小镇 · 研发观测台</div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 12, alignItems: "center" }}>
          <Dot ok={liveConnected} label={liveConnected ? "实时推送已连接" : "实时推送断开"} />
          <span style={{ fontSize: 12, opacity: 0.7 }}>实时事件 {liveEventCount}</span>
          <a href="/" style={styles.returnLink}>
            ← 返回小镇
          </a>
        </div>
      </header>
      <nav style={styles.tabs}>
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            style={{
              ...styles.tabButton,
              ...(tab === t.id ? styles.tabActive : {}),
            }}
          >
            {t.label}
          </button>
        ))}
      </nav>
      <main style={styles.body}>
        {tab === "dashboard" && <DashboardView />}
        {tab === "world-events" && <WorldEventsView />}
        {tab === "events" && <ObservabilityEventsView />}
        {tab === "agents" && <AgentRuntimeView />}
        {tab === "traces" && <TracesView />}
        {tab === "llm" && <LLMCallsView />}
        {tab === "tool" && <ToolCallsView />}
        {tab === "tasks" && <TasksView />}
        {tab === "errors" && <ErrorsView />}
        {tab === "ws" && <WSView />}
        {tab === "health" && <HealthView />}
      </main>
    </div>
  );
}

// -----------------------------------------------------------------------------
// Dashboard
// -----------------------------------------------------------------------------

function DashboardView() {
  const [data, setData] = useState<DashboardPayload | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let running = true;
    const tick = async () => {
      try {
        const d = await obsApi.dashboard();
        if (running) setData(d);
        if (running) setErr(null);
      } catch (e) {
        if (running) setErr(String((e as { message?: string }).message ?? e));
      }
    };
    tick();
    const id = window.setInterval(tick, 3000);
    return () => {
      running = false;
      window.clearInterval(id);
    };
  }, []);

  if (err) return <EmptyMsg text={`加载失败：${err}`} />;
  if (!data) return <EmptyMsg text="加载中……" />;

  return (
    <div style={styles.cardGrid}>
      <Card title="仿真">
        <KV k="id" v={data.simulation.id ?? "-"} />
        <KV k="status" v={data.simulation.status ?? "-"} />
        <KV k="step" v={String(data.simulation.step ?? "-")} />
        <KV k="speed" v={data.simulation.speed?.toFixed(2) ?? "-"} />
        <KV
          k="world_time"
          v={
            data.simulation.world_time
              ? new Date(data.simulation.world_time).toLocaleString()
              : "-"
          }
        />
      </Card>
      <Card title="WebSocket 在线">
        <KV k="simulation clients" v={String(data.websocket.simulation_clients)} />
        <KV k="observability clients" v={String(data.websocket.observability_clients)} />
      </Card>
      <Card title="LLM 调用（5 分钟）">
        <KV k="total" v={String(data.llm_calls_last_5m.total)} />
        <KV
          k="avg_latency_ms"
          v={data.llm_calls_last_5m.avg_latency_ms.toFixed(1)}
        />
        <KV k="failed" v={String(data.llm_calls_last_5m.failed)} />
        <KV
          k="error_rate"
          v={`${(data.llm_calls_last_5m.error_rate * 100).toFixed(1)}%`}
        />
      </Card>
      <Card title="任务队列">
        {Object.keys(data.tasks).length === 0 ? (
          <EmptyMsg text="暂无任务" />
        ) : (
          Object.entries(data.tasks).map(([k, v]) => <KV key={k} k={k} v={String(v)} />)
        )}
      </Card>
      <Card title="错误（5 分钟）">
        <div
          style={{
            fontSize: 28,
            color: data.errors_last_5m > 0 ? "#ff7a59" : "#7bd389",
          }}
        >
          {data.errors_last_5m}
        </div>
      </Card>
      <Card title="生成时间">
        <div style={{ fontSize: 12, opacity: 0.7 }}>
          {new Date(data.generated_at).toLocaleString()}
        </div>
      </Card>
    </div>
  );
}

// -----------------------------------------------------------------------------
// World Events
// -----------------------------------------------------------------------------

function WorldEventsView() {
  const [events, setEvents] = useState<WorldEvent[]>([]);
  const [filter, setFilter] = useState({ event_type: "", entity_id: "", scene_id: "" });

  const load = async () => {
    const data = await obsApi.listWorldEvents({ ...filter, limit: 100 });
    setEvents(data);
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter.event_type, filter.entity_id, filter.scene_id]);

  return (
    <div>
      <Toolbar>
        <TextFilter
          label="event_type"
          value={filter.event_type}
          onChange={(v) => setFilter((f) => ({ ...f, event_type: v }))}
        />
        <TextFilter
          label="entity_id"
          value={filter.entity_id}
          onChange={(v) => setFilter((f) => ({ ...f, entity_id: v }))}
        />
        <TextFilter
          label="scene_id"
          value={filter.scene_id}
          onChange={(v) => setFilter((f) => ({ ...f, scene_id: v }))}
        />
        <button type="button" style={styles.btn} onClick={load}>
          刷新
        </button>
      </Toolbar>
      <Table
        columns={["time", "type", "actor", "scene", "desc", "imp"]}
        rows={events.map((e) => [
          shortTs(e.created_at),
          <span key="t" style={styles.code}>{e.event_type}</span>,
          e.actor_entity_id ?? "-",
          e.scene_id ?? "-",
          e.description,
          String(e.importance),
        ])}
      />
    </div>
  );
}

// -----------------------------------------------------------------------------
// Obs Events
// -----------------------------------------------------------------------------

function ObservabilityEventsView() {
  const [events, setEvents] = useState<ObservabilityEvent[]>([]);
  const [filter, setFilter] = useState({ category: "", level: "", trace_id: "" });

  const load = async () => {
    const data = await obsApi.listEvents({ ...filter, limit: 150 });
    setEvents(data);
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter.category, filter.level, filter.trace_id]);

  return (
    <div>
      <Toolbar>
        <TextFilter
          label="category"
          value={filter.category}
          onChange={(v) => setFilter((f) => ({ ...f, category: v }))}
          placeholder="agent_decision / tool_call / llm_call / task / world_event"
        />
        <TextFilter
          label="level"
          value={filter.level}
          onChange={(v) => setFilter((f) => ({ ...f, level: v }))}
          placeholder="INFO / WARNING / ERROR"
        />
        <TextFilter
          label="trace_id"
          value={filter.trace_id}
          onChange={(v) => setFilter((f) => ({ ...f, trace_id: v }))}
        />
        <button type="button" style={styles.btn} onClick={load}>
          刷新
        </button>
      </Toolbar>
      <Table
        columns={["time", "category", "type", "level", "title", "entity", "trace"]}
        rows={events.map((e) => [
          shortTs(e.created_at),
          e.category,
          <span key="t" style={styles.code}>{e.event_type}</span>,
          levelBadge(e.level),
          e.title ?? "-",
          e.entity_id ?? "-",
          e.trace_id ? (
            <CopyableTruncatedId key={e.id} id={e.trace_id} previewChars={12} />
          ) : (
            "-"
          ),
        ])}
      />
    </div>
  );
}

// -----------------------------------------------------------------------------
// Agent Runtime
// -----------------------------------------------------------------------------

function AgentRuntimeView() {
  const [agentId, setAgentId] = useState("");
  return (
    <div>
      <Toolbar>
        <TextFilter
          label="agent_id"
          value={agentId}
          onChange={setAgentId}
          placeholder="例如 npc_xiaofang"
        />
      </Toolbar>
      {agentId ? <AgentRuntimeCard agentId={agentId} /> : <EmptyMsg text="输入 agent_id 查看运行态" />}
    </div>
  );
}

// -----------------------------------------------------------------------------
// Traces
// -----------------------------------------------------------------------------

function TracesView() {
  const [traceId, setTraceId] = useState("");
  const [recent, setRecent] = useState<string[]>([]);

  useEffect(() => {
    (async () => {
      const events = await obsApi.listEvents({ limit: 80 });
      const unique = Array.from(
        new Set(events.map((e) => e.trace_id).filter(Boolean) as string[]),
      );
      setRecent(unique.slice(0, 15));
    })();
  }, []);

  return (
    <div>
      <Toolbar>
        <TextFilter label="trace_id" value={traceId} onChange={setTraceId} />
      </Toolbar>
      {!traceId && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 12, opacity: 0.7, marginBottom: 6 }}>
            最近 trace：
          </div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {recent.map((tid) => (
              <button
                key={tid}
                type="button"
                style={styles.chip}
                title={`${tid}\n单击查看详情 · 双击复制完整 ID`}
                onClick={() => setTraceId(tid)}
                onDoubleClick={(ev) => {
                  ev.preventDefault();
                  ev.stopPropagation();
                  void copyObservabilityText(tid);
                }}
              >
                {tid.slice(0, 14)}…
              </button>
            ))}
          </div>
        </div>
      )}
      {traceId && <TraceDetail traceId={traceId} />}
    </div>
  );
}

// -----------------------------------------------------------------------------
// LLM / Tool / Task / Error
// -----------------------------------------------------------------------------

function LLMCallsView() {
  const [rows, setRows] = useState<LLMCallRecord[]>([]);
  const load = async () => setRows(await obsApi.listLLMCalls({ limit: 150 }));
  useEffect(() => {
    load();
  }, []);
  return (
    <div>
      <Toolbar>
        <button type="button" style={styles.btn} onClick={load}>
          刷新
        </button>
      </Toolbar>
      <Table
        columns={["time", "model", "caller", "latency", "retry", "ok", "schema", "fallback", "error", "trace"]}
        rows={rows.map((r) => [
          shortTs(r.created_at),
          <span key="m" style={styles.code}>{r.model}</span>,
          r.caller_module ?? "-",
          `${r.latency_ms}ms`,
          String(r.retry_count),
          boolBadge(r.success),
          r.schema_valid === null ? "-" : boolBadge(r.schema_valid),
          boolBadge(r.fallback_used, true),
          r.error_code ?? "-",
          r.trace_id ? <CopyableTruncatedId id={r.trace_id} previewChars={12} /> : "-",
        ])}
      />
    </div>
  );
}

function ToolCallsView() {
  const [rows, setRows] = useState<ToolCallRecord[]>([]);
  const load = async () => setRows(await obsApi.listToolCalls({ limit: 150 }));
  useEffect(() => {
    load();
  }, []);
  return (
    <div>
      <Toolbar>
        <button type="button" style={styles.btn} onClick={load}>
          刷新
        </button>
      </Toolbar>
      <Table
        columns={["time", "tool", "agent", "perm", "world", "ok", "duration", "err", "trace"]}
        rows={rows.map((r) => [
          shortTs(r.created_at),
          <span key="t" style={styles.code}>{r.tool}</span>,
          r.caller_agent_id ?? "-",
          r.permission_valid === null ? "-" : boolBadge(r.permission_valid),
          r.world_state_valid === null ? "-" : boolBadge(r.world_state_valid),
          boolBadge(r.success),
          `${r.duration_ms}ms`,
          r.error_code ?? "-",
          r.trace_id ? <CopyableTruncatedId id={r.trace_id} previewChars={12} /> : "-",
        ])}
      />
    </div>
  );
}

function TasksView() {
  const [rows, setRows] = useState<TaskRecord[]>([]);
  const [filter, setFilter] = useState({ status: "", task_type: "" });
  const load = async () => setRows(await obsApi.listTasks({ ...filter, limit: 150 }));
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter.status, filter.task_type]);

  return (
    <div>
      <Toolbar>
        <TextFilter
          label="status"
          value={filter.status}
          onChange={(v) => setFilter((f) => ({ ...f, status: v }))}
          placeholder="pending / running / succeeded / failed / timeout"
        />
        <TextFilter
          label="task_type"
          value={filter.task_type}
          onChange={(v) => setFilter((f) => ({ ...f, task_type: v }))}
          placeholder="agent_decision / write_memory_embedding / ..."
        />
        <button type="button" style={styles.btn} onClick={load}>
          刷新
        </button>
      </Toolbar>
      <Table
        columns={[
          "enqueued_at",
          "type",
          "status",
          "entity",
          "step",
          "retry",
          "last_error",
          "idempotency_key",
          "trace",
        ]}
        rows={rows.map((r) => [
          shortTs(r.enqueued_at),
          <span key="t" style={styles.code}>{r.task_type}</span>,
          statusBadge(r.status),
          r.entity_id ?? "-",
          r.simulation_step ?? "-",
          `${r.retry_count}/${r.max_retries}`,
          r.last_error ? r.last_error.slice(0, 40) + "…" : "-",
          <span key="k" style={{ fontSize: 11, color: "#8b949e" }}>{r.idempotency_key}</span>,
          r.trace_id ? <CopyableTruncatedId id={r.trace_id} previewChars={12} /> : "-",
        ])}
      />
    </div>
  );
}

function ErrorsView() {
  const [rows, setRows] = useState<ObservabilityEvent[]>([]);
  const load = async () => setRows(await obsApi.listErrors(150));
  useEffect(() => {
    load();
  }, []);

  const aggregated = useMemo(() => {
    const m = new Map<string, number>();
    for (const r of rows) {
      const key = `${r.category}:${r.event_type}`;
      m.set(key, (m.get(key) ?? 0) + 1);
    }
    return Array.from(m.entries()).sort((a, b) => b[1] - a[1]);
  }, [rows]);

  return (
    <div>
      <Toolbar>
        <button type="button" style={styles.btn} onClick={load}>
          刷新
        </button>
      </Toolbar>
      <div style={styles.cardGrid}>
        <Card title="错误聚合（category:event_type）">
          {aggregated.length === 0 ? (
            <EmptyMsg text="暂无错误" />
          ) : (
            aggregated.map(([k, v]) => <KV key={k} k={k} v={String(v)} />)
          )}
        </Card>
      </div>
      <Table
        columns={["time", "level", "category", "type", "title", "entity", "trace"]}
        rows={rows.map((r) => [
          shortTs(r.created_at),
          levelBadge(r.level),
          r.category,
          <span key="t" style={styles.code}>{r.event_type}</span>,
          r.title ?? "-",
          r.entity_id ?? "-",
          r.trace_id ? <CopyableTruncatedId id={r.trace_id} previewChars={12} /> : "-",
        ])}
      />
    </div>
  );
}

// -----------------------------------------------------------------------------
// WS Monitor
// -----------------------------------------------------------------------------

function WSView() {
  const [status, setStatus] = useState(observabilitySocket.getStatus?.() ?? {
    connected: false,
    reconnecting: false,
    attempt: 0,
    lastSeq: null,
    droppedSeqs: 0,
  });
  const [recentTypes, setRecentTypes] = useState<Array<{ t: number; type: string }>>([]);

  useEffect(() => {
    const unsubStatus = observabilitySocket.onStatusChange((s) => setStatus(s));
    const unsubMsg = observabilitySocket.subscribe((type) => {
      setRecentTypes((prev) => [{ t: Date.now(), type }, ...prev].slice(0, 30));
    });
    return () => {
      unsubStatus();
      unsubMsg();
    };
  }, []);

  return (
    <div style={styles.cardGrid}>
      <Card title="观测 WS 状态">
        <KV k="connected" v={String(status.connected)} />
        <KV k="reconnecting" v={String(status.reconnecting)} />
        <KV k="attempt" v={String(status.attempt)} />
        <KV k="last_seq" v={status.lastSeq == null ? "-" : String(status.lastSeq)} />
        <KV k="dropped_seqs" v={String(status.droppedSeqs)} />
      </Card>
      <Card title="最近推送">
        {recentTypes.length === 0 ? (
          <EmptyMsg text="等待推送……" />
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {recentTypes.map((r, i) => (
              <div key={i} style={{ fontSize: 12 }}>
                <span style={{ color: "#8b949e", marginRight: 6 }}>
                  {new Date(r.t).toLocaleTimeString()}
                </span>
                <span style={styles.code}>{r.type}</span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

// -----------------------------------------------------------------------------
// Health
// -----------------------------------------------------------------------------

function HealthView() {
  const [db, setDb] = useState<HealthReport | null>(null);
  const [redis, setRedis] = useState<HealthReport | null>(null);
  const [llm, setLlm] = useState<HealthReport | null>(null);

  const load = async () => {
    const [d, r, l] = await Promise.all([
      obsApi.health.db().catch((e) => ({ ok: false, reason: String(e?.message ?? e) })),
      obsApi.health.redis().catch((e) => ({ ok: false, reason: String(e?.message ?? e) })),
      obsApi.health.llm().catch((e) => ({ ok: false, reason: String(e?.message ?? e) })),
    ]);
    setDb(d);
    setRedis(r);
    setLlm(l);
  };

  useEffect(() => {
    load();
    const id = window.setInterval(load, 5000);
    return () => window.clearInterval(id);
  }, []);

  return (
    <div style={styles.cardGrid}>
      <Card title="Postgres">
        <Dot ok={!!db?.ok} label={db?.ok ? "OK" : db?.reason ?? "unknown"} />
      </Card>
      <Card title="Redis">
        <Dot ok={!!redis?.ok} label={redis?.ok ? "OK" : redis?.reason ?? "unknown"} />
      </Card>
      <Card title="LLM">
        <Dot ok={!!llm?.ok} label={describeLlmStatus(llm)} />
        {llm && (
          <>
            <KV k="provider" v={llm.provider ?? "-"} />
            <KV k="model" v={llm.model ?? "-"} />
            <KV k="enabled" v={String(llm.enabled ?? false)} />
            <KV k="has_api_key" v={String(llm.has_api_key ?? false)} />
            {!llm.ok && llm.enabled === false && llm.has_api_key && (
              <div style={{ fontSize: 11, opacity: 0.7, marginTop: 6, lineHeight: 1.5 }}>
                提示：API Key 已填，但 <code>LLM_ENABLED=false</code>。
                在 <code>.env</code> 改为 <code>true</code> 并重启后端即可启用。
              </div>
            )}
          </>
        )}
      </Card>
    </div>
  );
}

// -----------------------------------------------------------------------------
// 小部件
// -----------------------------------------------------------------------------

function Toolbar({ children }: { children: React.ReactNode }) {
  return <div style={styles.toolbar}>{children}</div>;
}

function TextFilter({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <label style={styles.field}>
      <span style={{ fontSize: 11, color: "#8b949e", marginBottom: 2 }}>{label}</span>
      <input
        type="text"
        style={styles.input}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
      />
    </label>
  );
}

function Table({
  columns,
  rows,
}: {
  columns: string[];
  rows: Array<Array<React.ReactNode>>;
}) {
  return (
    <div style={{ overflow: "auto", maxHeight: "calc(100vh - 240px)" }}>
      <table style={styles.table}>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c} style={styles.th}>
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length} style={{ ...styles.td, textAlign: "center", opacity: 0.7 }}>
                暂无数据
              </td>
            </tr>
          ) : (
            rows.map((row, i) => (
              <tr key={i} style={{ background: i % 2 ? "#0e1420" : "transparent" }}>
                {row.map((cell, j) => (
                  <td key={j} style={styles.td}>
                    {cell}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={styles.card}>
      <div style={styles.cardTitle}>{title}</div>
      <div>{children}</div>
    </div>
  );
}

function KV({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div style={styles.kv}>
      <span style={{ color: "#8b949e" }}>{k}</span>
      <span style={{ color: "#e6edf3", fontFamily: "monospace" }}>{v}</span>
    </div>
  );
}

function EmptyMsg({ text }: { text: string }) {
  return <div style={{ padding: 16, opacity: 0.7, fontSize: 13 }}>{text}</div>;
}

function Dot({ ok, label }: { ok: boolean; label: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <span
        style={{
          display: "inline-block",
          width: 10,
          height: 10,
          borderRadius: 5,
          background: ok ? "#7bd389" : "#ff7a59",
        }}
      />
      <span style={{ fontSize: 12 }}>{label}</span>
    </div>
  );
}

function boolBadge(v: boolean, invertColor = false) {
  const good = invertColor ? !v : v;
  return (
    <span
      style={{
        padding: "1px 6px",
        borderRadius: 3,
        fontSize: 11,
        fontFamily: "monospace",
        background: good ? "rgba(123, 211, 137, 0.15)" : "rgba(255, 122, 89, 0.15)",
        color: good ? "#7bd389" : "#ff7a59",
      }}
    >
      {String(v)}
    </span>
  );
}

function levelBadge(level: string) {
  const map: Record<string, string> = {
    ERROR: "#ff7a59",
    WARNING: "#f4b53f",
    INFO: "#7bd389",
  };
  const color = map[level] ?? "#8b949e";
  return (
    <span
      style={{
        padding: "1px 6px",
        borderRadius: 3,
        fontSize: 11,
        fontFamily: "monospace",
        background: `${color}22`,
        color,
      }}
    >
      {level}
    </span>
  );
}

function statusBadge(status: string) {
  const map: Record<string, string> = {
    pending: "#f4b53f",
    running: "#3bb0ff",
    succeeded: "#7bd389",
    failed: "#ff7a59",
    timeout: "#ff7a59",
    cancelled: "#8b949e",
  };
  const color = map[status] ?? "#8b949e";
  return (
    <span
      style={{
        padding: "1px 8px",
        borderRadius: 3,
        fontSize: 11,
        fontFamily: "monospace",
        background: `${color}22`,
        color,
      }}
    >
      {status}
    </span>
  );
}

function shortTs(ts: string | null): string {
  if (!ts) return "-";
  const d = new Date(ts);
  return d.toLocaleTimeString();
}

function describeLlmStatus(llm: HealthReport | null): string {
  if (!llm) return "检查中……";
  if (llm.ok) return "已启用";
  if (llm.enabled === false && llm.has_api_key) {
    return "已填 Key，但主开关 LLM_ENABLED=false";
  }
  if (llm.enabled && !llm.has_api_key) return "已开启但缺 API Key";
  return "未启用（enabled=false 且无 Key）";
}

const styles: Record<string, React.CSSProperties> = {
  root: {
    width: "100%",
    height: "100%",
    display: "grid",
    gridTemplateRows: "48px auto 1fr",
    background: "#07090e",
    color: "#e6edf3",
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
  },
  header: {
    display: "flex",
    alignItems: "center",
    gap: 14,
    padding: "0 16px",
    background: "#11161e",
    borderBottom: "1px solid rgba(255,255,255,0.06)",
  },
  brand: { fontSize: 14, fontWeight: 600, letterSpacing: 0.5 },
  returnLink: {
    color: "#8b949e",
    fontSize: 12,
    textDecoration: "none",
    marginLeft: 6,
  },
  tabs: {
    display: "flex",
    gap: 4,
    padding: "8px 12px",
    background: "#0c1118",
    borderBottom: "1px solid rgba(255,255,255,0.06)",
    overflowX: "auto",
  },
  tabButton: {
    background: "transparent",
    color: "#8b949e",
    border: "1px solid transparent",
    padding: "4px 10px",
    borderRadius: 4,
    cursor: "pointer",
    fontSize: 12,
    whiteSpace: "nowrap",
  },
  tabActive: {
    background: "#1f2733",
    color: "#e6edf3",
    border: "1px solid rgba(255,255,255,0.12)",
  },
  body: { padding: 14, overflow: "auto" },
  cardGrid: {
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
  cardTitle: {
    fontSize: 12,
    fontWeight: 600,
    color: "#8b949e",
    marginBottom: 8,
    textTransform: "uppercase",
    letterSpacing: 1,
  },
  kv: {
    display: "flex",
    justifyContent: "space-between",
    fontSize: 12,
    padding: "2px 0",
  },
  toolbar: {
    display: "flex",
    gap: 10,
    alignItems: "flex-end",
    marginBottom: 12,
    flexWrap: "wrap",
  },
  field: { display: "flex", flexDirection: "column", minWidth: 180 },
  input: {
    background: "#0c1118",
    border: "1px solid rgba(255,255,255,0.1)",
    borderRadius: 4,
    color: "#e6edf3",
    padding: "6px 8px",
    fontSize: 12,
    fontFamily: "monospace",
  },
  btn: {
    background: "#1f2733",
    color: "#e6edf3",
    border: "1px solid rgba(255,255,255,0.12)",
    borderRadius: 4,
    padding: "6px 14px",
    fontSize: 12,
    cursor: "pointer",
  },
  chip: {
    background: "#1f2733",
    color: "#e6edf3",
    border: "1px solid rgba(255,255,255,0.12)",
    borderRadius: 3,
    padding: "2px 8px",
    fontSize: 11,
    fontFamily: "monospace",
    cursor: "pointer",
  },
  code: {
    fontFamily: "monospace",
    fontSize: 12,
    color: "#c0caf5",
  },
  table: {
    width: "100%",
    borderCollapse: "collapse",
    fontSize: 12,
  },
  th: {
    textAlign: "left",
    padding: "6px 8px",
    borderBottom: "1px solid rgba(255,255,255,0.1)",
    background: "#11161e",
    color: "#8b949e",
    position: "sticky",
    top: 0,
    textTransform: "uppercase",
    letterSpacing: 1,
    fontSize: 10,
  },
  td: {
    padding: "5px 8px",
    borderBottom: "1px solid rgba(255,255,255,0.04)",
    verticalAlign: "top",
  },
};
