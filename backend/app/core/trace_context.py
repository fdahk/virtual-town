"""
Trace context（可观测性基础设施 14.1）。

目标：
- 在跨模块调用链（REST 入口、WS 入口、tick 入口、任务投递入口）中传递
  ``request_id / trace_id / span_id / parent_span_id / simulation_id /
  agent_id / player_id / task_id / event_id``。
- 基于 ``contextvars`` 实现，天然兼容 ``asyncio`` 的 Task 隔离。
- 所有结构化日志自动携带 trace 字段（见 ``logging.py``）。
- 任何业务代码只应通过此模块读写 trace 上下文，避免直接操作 contextvars。

使用方式：

.. code-block:: python

    with TraceContext.start_trace("agent_decision", simulation_id=sim, agent_id=agent):
        with TraceContext.span("agent.perceive"):
            ...

span / trace 会在退出时自动关闭；嵌套 span 自动设置 parent_span_id。
"""

from __future__ import annotations

import contextvars
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

# contextvars -----------------------------------------------------------------

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "vt_request_id", default=None
)
_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "vt_trace_id", default=None
)
_span_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "vt_span_id", default=None
)
_parent_span_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "vt_parent_span_id", default=None
)
_simulation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "vt_simulation_id", default=None
)
_agent_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "vt_agent_id", default=None
)
_player_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "vt_player_id", default=None
)
_task_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "vt_task_id", default=None
)
_event_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "vt_event_id", default=None
)
# trace 分类：agent_decision / player_query / world_tick / task / websocket / http
_category: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "vt_category", default=None
)


@dataclass
class TraceSnapshot:
    """快照：可用于跨线程/进程/任务传递。"""

    request_id: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    simulation_id: str | None = None
    agent_id: str | None = None
    player_id: str | None = None
    task_id: str | None = None
    event_id: str | None = None
    category: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_log_extra(self) -> dict[str, Any]:
        """转换为 logger ``extra`` 友好的 dict。"""
        data: dict[str, Any] = {}
        for key, value in {
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "simulation_id": self.simulation_id,
            "agent_id": self.agent_id,
            "player_id": self.player_id,
            "task_id": self.task_id,
            "event_id": self.event_id,
            "category": self.category,
        }.items():
            if value is not None:
                data[key] = value
        return data


def _gen(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def current() -> TraceSnapshot:
    """读取当前 contextvars 的快照。"""
    return TraceSnapshot(
        request_id=_request_id.get(),
        trace_id=_trace_id.get(),
        span_id=_span_id.get(),
        parent_span_id=_parent_span_id.get(),
        simulation_id=_simulation_id.get(),
        agent_id=_agent_id.get(),
        player_id=_player_id.get(),
        task_id=_task_id.get(),
        event_id=_event_id.get(),
        category=_category.get(),
    )


class TraceContext:
    """Trace / Span 封装（优先通过类方法使用，便于 mock）。"""

    # ------------------------------------------------------------------
    # 直接赋值辅助（用于 REST 入口、WS 入口、任务入口设置顶层字段）
    # ------------------------------------------------------------------

    @staticmethod
    def set_request_id(value: str | None) -> contextvars.Token[str | None]:
        return _request_id.set(value)

    @staticmethod
    def set_simulation_id(value: str | None) -> contextvars.Token[str | None]:
        return _simulation_id.set(value)

    @staticmethod
    def set_agent_id(value: str | None) -> contextvars.Token[str | None]:
        return _agent_id.set(value)

    @staticmethod
    def set_player_id(value: str | None) -> contextvars.Token[str | None]:
        return _player_id.set(value)

    @staticmethod
    def set_task_id(value: str | None) -> contextvars.Token[str | None]:
        return _task_id.set(value)

    @staticmethod
    def set_event_id(value: str | None) -> contextvars.Token[str | None]:
        return _event_id.set(value)

    @staticmethod
    def set_category(value: str | None) -> contextvars.Token[str | None]:
        return _category.set(value)

    # ------------------------------------------------------------------
    # Trace / Span 生命周期
    # ------------------------------------------------------------------

    @classmethod
    @contextmanager
    def start_trace(
        cls,
        category: str,
        *,
        trace_id: str | None = None,
        request_id: str | None = None,
        simulation_id: str | None = None,
        agent_id: str | None = None,
        player_id: str | None = None,
        task_id: str | None = None,
    ) -> Iterator[TraceSnapshot]:
        """
        开启一条新 trace（顶层 span）。

        - ``category`` 必填，用于观测平台按类别筛选。
        - 同一 trace 内的所有 span 共享 trace_id。
        - 未指定 trace_id 时自动生成。
        """
        new_trace_id = trace_id or _gen("trace")
        new_span_id = _gen("span")
        tokens = [
            _trace_id.set(new_trace_id),
            _span_id.set(new_span_id),
            _parent_span_id.set(None),
            _category.set(category),
        ]
        if request_id is not None:
            tokens.append(_request_id.set(request_id))
        elif _request_id.get() is None:
            tokens.append(_request_id.set(_gen("req")))
        if simulation_id is not None:
            tokens.append(_simulation_id.set(simulation_id))
        if agent_id is not None:
            tokens.append(_agent_id.set(agent_id))
        if player_id is not None:
            tokens.append(_player_id.set(player_id))
        if task_id is not None:
            tokens.append(_task_id.set(task_id))

        snapshot = current()
        try:
            yield snapshot
        finally:
            for token in reversed(tokens):
                try:
                    token.var.reset(token)
                except Exception:
                    # tokens 来源不同的 ContextVar，reset 失败不影响其他 var
                    pass

    @classmethod
    @contextmanager
    def span(cls, name: str, **attrs: Any) -> Iterator[TraceSnapshot]:
        """
        开启子 span。嵌套调用时自动继承 trace_id 并设置 parent_span_id。
        """
        new_span_id = _gen("span")
        parent = _span_id.get()
        tokens = [
            _parent_span_id.set(parent),
            _span_id.set(new_span_id),
        ]
        # 允许在 span 内覆盖 agent / player / task
        for key, value in attrs.items():
            if value is None:
                continue
            if key == "agent_id":
                tokens.append(_agent_id.set(value))
            elif key == "player_id":
                tokens.append(_player_id.set(value))
            elif key == "task_id":
                tokens.append(_task_id.set(value))
            elif key == "event_id":
                tokens.append(_event_id.set(value))
        snapshot = current()
        # span 的 name 作为 log extra 辅助字段传递
        snapshot.extra["span_name"] = name
        try:
            yield snapshot
        finally:
            for token in reversed(tokens):
                try:
                    token.var.reset(token)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # 与快照互转（跨任务边界传递）
    # ------------------------------------------------------------------

    @staticmethod
    def snapshot() -> TraceSnapshot:
        return current()

    @classmethod
    @contextmanager
    def apply(cls, snapshot: TraceSnapshot) -> Iterator[TraceSnapshot]:
        """
        将一份 TraceSnapshot 恢复为当前 contextvars。
        用于：任务 worker 从 payload 中恢复 trace，保持链路连续。
        """
        tokens = [
            _request_id.set(snapshot.request_id),
            _trace_id.set(snapshot.trace_id),
            _span_id.set(snapshot.span_id),
            _parent_span_id.set(snapshot.parent_span_id),
            _simulation_id.set(snapshot.simulation_id),
            _agent_id.set(snapshot.agent_id),
            _player_id.set(snapshot.player_id),
            _task_id.set(snapshot.task_id),
            _event_id.set(snapshot.event_id),
            _category.set(snapshot.category),
        ]
        try:
            yield snapshot
        finally:
            for token in reversed(tokens):
                try:
                    token.var.reset(token)
                except Exception:
                    pass

    @staticmethod
    def to_dict() -> dict[str, Any]:
        """把当前 trace 压成 JSON 可序列化 dict，任务 payload 中使用。"""
        return current().as_log_extra()


__all__ = ["TraceContext", "TraceSnapshot", "current"]
