"""task_status_log: error_code

Stage 21 hotfix：

``app.services.observer.record_task_status`` 一直把 ``error_code`` 字段传给
``TaskStatusLog(**record)``，但 ORM/数据库一直没有该列，导致 worker 在
deadline-exceeded / handler-missing 等失败路径上每次 flush 都抛
``TypeError: 'error_code' is an invalid keyword argument for TaskStatusLog``，
观测批次被整批丢弃，事件 / LLM / Tool 调用日志同时丢失。

补一列 ``error_code String(64) NULL``，与 Redis 摘要 / 事件总线已有的
``error_code`` 字段对齐。

Revision ID: 20260502_0006
Revises: 20260502_0005
Create Date: 2026-05-02 13:35:00
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260502_0006"
down_revision: str | None = "20260502_0005"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "task_status_log",
        sa.Column("error_code", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("task_status_log", "error_code")
