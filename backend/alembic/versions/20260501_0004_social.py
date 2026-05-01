"""social: agent_states busy fields + interaction_requests table

阶段 19（智能 NPC 社会化升级）：

1. ``agent_states`` 新增 4 个字段：
   - ``busy_until`` (TIMESTAMPTZ, NULL)：当前活动占用结束时间
   - ``interruptible`` (BOOL, NOT NULL, DEFAULT TRUE)：是否允许被打断
   - ``current_priority`` (INT, NOT NULL, DEFAULT 0)：当前任务优先级 0-10
   - ``last_social_at`` (TIMESTAMPTZ, NULL)：上一次完成社交的游戏时间
2. 新建 ``interaction_requests`` 表：持久化 NPC↔NPC、玩家↔NPC 的请求-同意-拒绝记录。
3. 扩展 ``AgentState`` 字符串枚举：新增 ``AWAITING_RESPONSE`` / ``BUSY_REFUSING``
   （仅约定，无 schema 变化；前后端类型同步即可）。

Revision ID: 20260501_0004
Revises: 20260501_0003
Create Date: 2026-05-01 17:00:00
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260501_0004"
down_revision: str | None = "20260501_0003"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_states",
        sa.Column("busy_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_states",
        sa.Column(
            "interruptible",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "agent_states",
        sa.Column(
            "current_priority",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "agent_states",
        sa.Column("last_social_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "interaction_requests",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("simulation_id", sa.String(64), nullable=True),
        sa.Column(
            "requester_id",
            sa.String(64),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_id",
            sa.String(64),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False, server_default="chat"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("decline_kind", sa.String(8), nullable=True),
        sa.Column("npc_line", sa.Text, nullable=True),
        sa.Column(
            "requester_priority",
            sa.Integer,
            nullable=False,
            server_default=sa.text("3"),
        ),
        sa.Column(
            "target_priority_at_request",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_interaction_requests_target_status",
        "interaction_requests",
        ["target_id", "status"],
    )
    op.create_index(
        "ix_interaction_requests_requester_created",
        "interaction_requests",
        ["requester_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_interaction_requests_requester_created",
        table_name="interaction_requests",
    )
    op.drop_index(
        "ix_interaction_requests_target_status",
        table_name="interaction_requests",
    )
    op.drop_table("interaction_requests")

    op.drop_column("agent_states", "last_social_at")
    op.drop_column("agent_states", "current_priority")
    op.drop_column("agent_states", "interruptible")
    op.drop_column("agent_states", "busy_until")
