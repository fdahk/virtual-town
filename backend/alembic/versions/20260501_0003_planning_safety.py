"""planning + safety: agent_plans, memory.importance_detail

对应 MVP 开发任务拆分 §15 / §18：

1. 新增 ``agent_plans`` 表（§15.1-2）：保存 daily_plan / hourly_schedule /
   task_decomposition 三层计划，跨 tick / 重启持久。
2. 为 ``memories`` 新增 ``importance_detail`` JSONB 字段（§15.5）：
   记录 base / emotion / relationship / novelty / danger 五分项明细。
3. 为 ``tool_calls`` 新增 ``policy_outcome`` 字段（§18.2）：
   权限审计 / 越权统计使用。

Revision ID: 20260501_0003
Revises: 20260430_0002
Create Date: 2026-05-01 01:00:00
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260501_0003"
down_revision: str | None = "20260430_0002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # §15 层次化规划：agent_plans
    # ------------------------------------------------------------------
    op.create_table(
        "agent_plans",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "agent_id",
            sa.String(64),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plan_type", sa.String(32), nullable=False),
        sa.Column("day_key", sa.String(16), nullable=False),
        sa.Column("hour_key", sa.Integer, nullable=True),
        sa.Column(
            "parent_plan_id",
            sa.String(64),
            sa.ForeignKey("agent_plans.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "content",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("source", sa.String(16), nullable=False, server_default="rule"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_agent_plans_agent_day", "agent_plans", ["agent_id", "day_key"])
    op.create_index(
        "ix_agent_plans_agent_type_status",
        "agent_plans",
        ["agent_id", "plan_type", "status"],
    )

    # ------------------------------------------------------------------
    # §15.5 memories.importance_detail（分项明细）
    # ------------------------------------------------------------------
    op.add_column(
        "memories",
        sa.Column(
            "importance_detail",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    # ------------------------------------------------------------------
    # §18.2 tool_calls.policy_outcome（权限审计）
    # ------------------------------------------------------------------
    op.add_column(
        "tool_calls",
        sa.Column("policy_outcome", sa.String(24), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tool_calls", "policy_outcome")
    op.drop_column("memories", "importance_detail")
    op.drop_index("ix_agent_plans_agent_type_status", table_name="agent_plans")
    op.drop_index("ix_agent_plans_agent_day", table_name="agent_plans")
    op.drop_table("agent_plans")
