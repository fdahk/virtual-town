// 观测平台（§14）前端类型定义。
// 与 backend/app/api/routes_observability.py 的序列化字段保持一致。

export interface ObservabilityEvent {
  id: string;
  simulation_id: string | null;
  trace_id: string | null;
  span_id: string | null;
  parent_span_id: string | null;
  category: string;
  event_type: string;
  level: "INFO" | "WARNING" | "ERROR" | string;
  title: string | null;
  entity_id: string | null;
  player_id: string | null;
  task_id: string | null;
  world_step: number | null;
  duration_ms: number | null;
  payload: Record<string, unknown>;
  created_at: string | null;
}

export interface LLMCallRecord {
  id: string;
  trace_id: string | null;
  span_id: string | null;
  simulation_id: string | null;
  agent_id: string | null;
  provider: string;
  model: string;
  prompt_template_id: string | null;
  prompt_version: string | null;
  caller_module: string | null;
  input_summary: string | null;
  token_estimate: number | null;
  latency_ms: number;
  retry_count: number;
  success: boolean;
  schema_valid: boolean | null;
  fallback_used: boolean;
  error_code: string | null;
  created_at: string | null;
}

export interface ToolCallRecord {
  id: string;
  trace_id: string | null;
  span_id: string | null;
  simulation_id: string | null;
  tool: string;
  caller_agent_id: string | null;
  entity_type: string | null;
  arguments: Record<string, unknown>;
  schema_valid: boolean | null;
  permission_valid: boolean | null;
  world_state_valid: boolean | null;
  success: boolean;
  duration_ms: number;
  result_summary: string | null;
  error_code: string | null;
  source: string | null;
  created_at: string | null;
}

export interface TaskRecord {
  id: string;
  task_type: string;
  status:
    | "pending"
    | "running"
    | "succeeded"
    | "failed"
    | "timeout"
    | "cancelled"
    | string;
  priority: number;
  entity_id: string | null;
  simulation_id: string | null;
  simulation_step: number | null;
  idempotency_key: string;
  retry_count: number;
  max_retries: number;
  trace_id: string | null;
  last_error: string | null;
  payload: Record<string, unknown>;
  result: Record<string, unknown> | null;
  enqueued_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface DashboardPayload {
  simulation: {
    id: string | null;
    status: string | null;
    step: number | null;
    speed: number | null;
    world_time: string | null;
  };
  websocket: {
    simulation_clients: number;
    observability_clients: number;
  };
  llm_calls_last_5m: {
    total: number;
    avg_latency_ms: number;
    failed: number;
    error_rate: number;
  };
  tasks: Record<string, number>;
  errors_last_5m: number;
  generated_at: string;
}

export interface TraceBundle {
  trace_id: string;
  events: ObservabilityEvent[];
  llm_calls: LLMCallRecord[];
  tool_calls: ToolCallRecord[];
  tasks: TaskRecord[];
}

export interface AgentRuntimeView {
  agent: {
    id: string;
    name: string;
    entity_type: string;
    personality: string[];
    occupation: string | null;
  };
  state: {
    scene_id: string;
    x: number;
    y: number;
    state: string;
    emotion: string | null;
    energy: number;
    hunger: number;
    social_need: number;
    fear: number;
    status_effects: string[];
    current_goal: string | null;
    facing: string;
    path: Array<{ x: number; y: number }>;
  } | null;
  redis_runtime: Record<string, unknown> | null;
  recent_events: ObservabilityEvent[];
  recent_memories: Array<{
    id: string;
    description: string;
    scope: string;
    importance: number;
    created_at: string | null;
  }>;
}

export interface HealthReport {
  ok: boolean;
  reason?: string;
  provider?: string;
  model?: string;
  base_url?: string;
  enabled?: boolean;
  has_api_key?: boolean;
}
