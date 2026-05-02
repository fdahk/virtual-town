"""memory consolidation: summarized_into_id

阶段 20（记忆系统重构）：

1. ``memories`` 新增列 ``summarized_into_id`` (String(64), NULL)：
   指向把这条记忆收录进去的 summary 记忆。consolidation worker 会把
   archived 同主题 ≥3 条合并成一条 long_term summary 后回填。
2. 新建索引 ``ix_memories_summarized_into``：consolidation 任务每天扫"未合并的
   archived 记忆"会按此列做 IS NULL 过滤。
3. ``scope`` 字段值集合扩展为 working / short_term / long_term / archived /
   ``consolidated``（原文已被合并、仅作证据，不进默认检索）。该值是字符串约定，
   无 schema 变更。

Revision ID: 20260502_0005
Revises: 20260501_0004
Create Date: 2026-05-02 00:30:00
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260502_0005"
down_revision: str | None = "20260501_0004"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "memories",
        sa.Column("summarized_into_id", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_memories_summarized_into",
        "memories",
        ["summarized_into_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_memories_summarized_into", table_name="memories")
    op.drop_column("memories", "summarized_into_id")
