"""
TaskRegistry：任务类型注册表（阶段 12 §3）。

约定：
- 每个 task_type 对应一个 ``TaskHandler`` 实现。
- Handler 只能通过 ``TaskContext.session`` 访问数据库，不直接从容器 DI。
- Handler 返回 ``TaskResult``，包含成功/失败、retryable、result payload、
  以及可选的 fallback_hint（供业务兜底使用）。
- Handler 必须是幂等的：给定相同 payload，重复执行应得到相同的业务结果。
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class TaskContext:
    """任务执行上下文。"""

    task_id: str
    task_type: str
    payload: dict[str, Any]
    session: AsyncSession
    retry_count: int
    max_retries: int
    enqueued_at: datetime
    simulation_id: str | None = None
    entity_id: str | None = None
    simulation_step: int | None = None
    trace_id: str | None = None


@dataclass
class TaskResult:
    success: bool
    result: dict[str, Any] = field(default_factory=dict)
    # 业务兜底提示；worker / 业务方可据此决定是否触发 fallback
    fallback_hint: str | None = None
    error_message: str | None = None
    error_code: str | None = None
    # 是否允许 worker 进一步重试（即使 retry_count < max_retries）
    retryable: bool = False


class TaskHandler(abc.ABC):
    """任务处理器基类。"""

    task_type: str
    # 默认超时（worker 会把超出这个时间的任务标 timeout）
    default_timeout_seconds: float = 30.0
    default_max_retries: int = 2

    @abc.abstractmethod
    async def handle(self, ctx: TaskContext) -> TaskResult: ...


class TaskRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, TaskHandler] = {}

    def register(self, handler: TaskHandler) -> None:
        if not getattr(handler, "task_type", None):
            raise ValueError("handler missing task_type")
        self._handlers[handler.task_type] = handler

    def get(self, task_type: str) -> TaskHandler | None:
        return self._handlers.get(task_type)

    def list_types(self) -> list[str]:
        return sorted(self._handlers.keys())


_registry: TaskRegistry | None = None


def get_task_registry() -> TaskRegistry:
    global _registry
    if _registry is None:
        _registry = TaskRegistry()
    return _registry


__all__ = [
    "TaskContext",
    "TaskHandler",
    "TaskRegistry",
    "TaskResult",
    "get_task_registry",
]
