"""infra: tasks + observability + pgvector HNSW

对应 MVP 开发任务拆分 §12 / §13 / §14：

1. 新增异步任务队列表 ``tasks``、``task_status_log``（§12）。
2. 新增观测表 ``observability_events`` / ``llm_calls`` / ``tool_calls``（§14.2）。
3. 为 ``memories.embedding`` 创建 pgvector HNSW 索引（§13.1）。
4. 为 ``agent_states`` 添加 scene 查询索引。

Revision ID: 20260430_0002
Revises: 20260430_0001
Create Date: 2026-04-30 22:00:00
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260430_0002"
down_revision: str | None = "20260430_0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # §12 异步任务队列
    # ------------------------------------------------------------------
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("task_type", sa.String(48), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("priority", sa.Integer, nullable=False, server_default=sa.text("5")),
        sa.Column("entity_id", sa.String(64), nullable=True),
        sa.Column("simulation_id", sa.String(64), nullable=True),
        sa.Column("simulation_step", sa.Integer, nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result", postgresql.JSONB, nullable=True),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("max_retries", sa.Integer, nullable=False, server_default=sa.text("2")),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("idempotency_key", name="uq_tasks_idempotency_key"),
    )
    op.create_index("ix_tasks_status_enqueued_at", "tasks", ["status", "enqueued_at"])
    op.create_index("ix_tasks_type_status", "tasks", ["task_type", "status"])
    op.create_index("ix_tasks_trace_id", "tasks", ["trace_id"])

    op.create_table(
        "task_status_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("from_status", sa.String(16), nullable=True),
        sa.Column("to_status", sa.String(16), nullable=False),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("message", sa.Text, nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_task_status_log_task_created", "task_status_log", ["task_id", "created_at"])

    # ------------------------------------------------------------------
    # §14.2 观测事件 / LLM / Tool 审计
    # ------------------------------------------------------------------
    op.create_table(
        "observability_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("simulation_id", sa.String(64), nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("span_id", sa.String(64), nullable=True),
        sa.Column("parent_span_id", sa.String(64), nullable=True),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("level", sa.String(8), nullable=False, server_default="INFO"),
        sa.Column("title", sa.String(256), nullable=True),
        sa.Column("entity_id", sa.String(64), nullable=True),
        sa.Column("player_id", sa.String(64), nullable=True),
        sa.Column("task_id", sa.String(64), nullable=True),
        sa.Column("world_step", sa.Integer, nullable=True),
        sa.Column("world_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_obs_events_created", "observability_events", ["created_at"])
    op.create_index("ix_obs_events_trace", "observability_events", ["trace_id"])
    op.create_index(
        "ix_obs_events_sim_created", "observability_events", ["simulation_id", "created_at"]
    )
    op.create_index(
        "ix_obs_events_category", "observability_events", ["category", "created_at"]
    )

    op.create_table(
        "llm_calls",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("span_id", sa.String(64), nullable=True),
        sa.Column("simulation_id", sa.String(64), nullable=True),
        sa.Column("agent_id", sa.String(64), nullable=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("prompt_template_id", sa.String(64), nullable=True),
        sa.Column("prompt_version", sa.String(16), nullable=True),
        sa.Column("caller_module", sa.String(64), nullable=True),
        sa.Column("input_summary", sa.Text, nullable=True),
        sa.Column("token_estimate", sa.Integer, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("success", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("schema_valid", sa.Boolean, nullable=True),
        sa.Column("fallback_used", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_llm_calls_created", "llm_calls", ["created_at"])
    op.create_index("ix_llm_calls_trace", "llm_calls", ["trace_id"])
    op.create_index("ix_llm_calls_model", "llm_calls", ["model", "created_at"])

    op.create_table(
        "tool_calls",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("span_id", sa.String(64), nullable=True),
        sa.Column("simulation_id", sa.String(64), nullable=True),
        sa.Column("tool", sa.String(64), nullable=False),
        sa.Column("caller_agent_id", sa.String(64), nullable=True),
        sa.Column("entity_type", sa.String(16), nullable=True),
        sa.Column("arguments", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("schema_valid", sa.Boolean, nullable=True),
        sa.Column("permission_valid", sa.Boolean, nullable=True),
        sa.Column("world_state_valid", sa.Boolean, nullable=True),
        sa.Column("success", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("duration_ms", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("result_summary", sa.Text, nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("source", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tool_calls_created", "tool_calls", ["created_at"])
    op.create_index("ix_tool_calls_trace", "tool_calls", ["trace_id"])
    op.create_index("ix_tool_calls_tool", "tool_calls", ["tool", "created_at"])
    op.create_index("ix_tool_calls_agent", "tool_calls", ["caller_agent_id", "created_at"])

    # ------------------------------------------------------------------
    # §13 pgvector HNSW 索引（大规模记忆检索）
    # ------------------------------------------------------------------
    # NOTE: HNSW 在 pgvector 0.5+ 可用；这里使用 cosine 距离操作符类。
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memories_embedding_hnsw "
        "ON memories USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )

    # ------------------------------------------------------------------
    # §13 agent_states 场景索引，Redis miss 时走 Postgres 批量取
    # ------------------------------------------------------------------
    op.create_index(
        "ix_agent_states_scene",
        "agent_states",
        ["scene_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_agent_states_scene")
    op.execute("DROP INDEX IF EXISTS ix_memories_embedding_hnsw")

    op.drop_index("ix_tool_calls_agent", table_name="tool_calls")
    op.drop_index("ix_tool_calls_tool", table_name="tool_calls")
    op.drop_index("ix_tool_calls_trace", table_name="tool_calls")
    op.drop_index("ix_tool_calls_created", table_name="tool_calls")
    op.drop_table("tool_calls")

    op.drop_index("ix_llm_calls_model", table_name="llm_calls")
    op.drop_index("ix_llm_calls_trace", table_name="llm_calls")
    op.drop_index("ix_llm_calls_created", table_name="llm_calls")
    op.drop_table("llm_calls")

    op.drop_index("ix_obs_events_category", table_name="observability_events")
    op.drop_index("ix_obs_events_sim_created", table_name="observability_events")
    op.drop_index("ix_obs_events_trace", table_name="observability_events")
    op.drop_index("ix_obs_events_created", table_name="observability_events")
    op.drop_table("observability_events")

    op.drop_index("ix_task_status_log_task_created", table_name="task_status_log")
    op.drop_table("task_status_log")

    op.drop_index("ix_tasks_trace_id", table_name="tasks")
    op.drop_index("ix_tasks_type_status", table_name="tasks")
    op.drop_index("ix_tasks_status_enqueued_at", table_name="tasks")
    op.drop_table("tasks")
