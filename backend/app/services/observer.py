"""
Observer 记录器（阶段 14.2）。

统一的观测埋点入口。业务代码**只能**通过此模块写入观测表：

- ``record_event(category, event_type, ...)`` — 观测事件流
- ``record_llm_call(...)`` — LLM 调用审计
- ``record_tool_call(...)`` — Tool 调用审计
- ``record_task_status(...)`` — 任务状态变迁审计

所有写入都走**后台批量落库**，避免业务主路径上的 I/O 阻塞：

1. 内存缓冲区按类别分桶。
2. 异步 flush 任务每 ``flush_interval`` 秒或缓冲达到 ``batch_size`` 时落库。
3. flush 失败：丢弃最旧一批并记警告，避免无界积压。

同时：通过事件总线发布 ``observability.event`` 主题，供观测 WS 网关消费。
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.event_bus import get_event_bus
from app.core.logging import get_logger
from app.core.redis_client import get_redis, key_task_status
from app.core.time import utcnow
from app.core.trace_context import TraceContext, current as current_trace
from app.db.models import (
    LLMCallRecord,
    ObservabilityEvent,
    TaskStatusLog,
    ToolCallRecord,
)

logger = get_logger(__name__)

OBSERVABILITY_TOPIC = "observability.event"

# 观测事件 category
CATEGORY_WORLD_EVENT = "world_event"
CATEGORY_SIMULATION_TICK = "simulation_tick"
CATEGORY_AGENT_RUNTIME = "agent_runtime"
CATEGORY_AGENT_DECISION = "agent_decision"
CATEGORY_PLAYER_QUERY = "player_query"
CATEGORY_LLM_CALL = "llm_call"
CATEGORY_TOOL_CALL = "tool_call"
CATEGORY_MEMORY = "memory"
CATEGORY_TASK = "task"
CATEGORY_WEBSOCKET = "websocket"
CATEGORY_ERROR = "error"
CATEGORY_PERFORMANCE = "performance"


# -----------------------------------------------------------------------------
# 内部缓冲数据结构
# -----------------------------------------------------------------------------


@dataclass
class _Buffer:
    max_size: int = 2000
    events: deque[dict[str, Any]] = field(default_factory=deque)
    llm_calls: deque[dict[str, Any]] = field(default_factory=deque)
    tool_calls: deque[dict[str, Any]] = field(default_factory=deque)
    task_status: deque[dict[str, Any]] = field(default_factory=deque)

    def drop_overflow(self) -> None:
        for q in (self.events, self.llm_calls, self.tool_calls, self.task_status):
            while len(q) > self.max_size:
                q.popleft()


class Observer:
    """全局 Observer 单例。"""

    def __init__(
        self,
        *,
        flush_interval: float = 1.5,
        batch_size: int = 200,
    ) -> None:
        self._buf = _Buffer()
        self._lock = asyncio.Lock()
        self._session_factory: async_sessionmaker | None = None
        self._flush_task: asyncio.Task[None] | None = None
        self._stop = False
        self.flush_interval = flush_interval
        self.batch_size = batch_size

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def bind_session_factory(self, factory: async_sessionmaker) -> None:
        self._session_factory = factory

    async def start(self) -> None:
        if self._flush_task is not None:
            return
        self._stop = False
        self._flush_task = asyncio.create_task(self._flush_loop(), name="observer-flush")

    def is_running(self) -> bool:
        return self._flush_task is not None and not self._flush_task.done()

    async def stop(self) -> None:
        self._stop = True
        if self._flush_task is not None:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None
        # 最后一次 flush
        await self._flush_now()

    async def _flush_loop(self) -> None:
        try:
            while not self._stop:
                await asyncio.sleep(self.flush_interval)
                try:
                    await self._flush_now()
                except Exception:
                    logger.exception("observer flush failed")
        except asyncio.CancelledError:
            raise

    # ------------------------------------------------------------------
    # 写入入口
    # ------------------------------------------------------------------

    async def record_event(
        self,
        *,
        category: str,
        event_type: str,
        title: str | None = None,
        level: str = "INFO",
        payload: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        world_step: int | None = None,
        world_time: Any = None,
        entity_id: str | None = None,
        player_id: str | None = None,
        task_id: str | None = None,
        simulation_id: str | None = None,
    ) -> None:
        snap = current_trace()
        now = utcnow()
        record = {
            "category": category,
            "event_type": event_type,
            "title": title or event_type,
            "level": level,
            "payload": payload or {},
            "duration_ms": duration_ms,
            "world_step": world_step,
            "world_time": world_time,
            "entity_id": entity_id or snap.agent_id,
            "player_id": player_id or snap.player_id,
            "task_id": task_id or snap.task_id,
            "simulation_id": simulation_id or snap.simulation_id,
            "trace_id": snap.trace_id,
            "span_id": snap.span_id,
            "parent_span_id": snap.parent_span_id,
            "created_at": now,
        }
        async with self._lock:
            self._buf.events.append(record)
            self._buf.drop_overflow()
        await self._publish_live(record)
        await self._maybe_sync_flush()

    async def record_llm_call(
        self,
        *,
        provider: str,
        model: str,
        prompt_template_id: str | None = None,
        prompt_version: str | None = None,
        caller_module: str | None = None,
        input_summary: str | None = None,
        token_estimate: int | None = None,
        latency_ms: int = 0,
        retry_count: int = 0,
        success: bool = True,
        schema_valid: bool | None = None,
        fallback_used: bool = False,
        error_code: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        snap = current_trace()
        now = utcnow()
        record = {
            "provider": provider,
            "model": model,
            "prompt_template_id": prompt_template_id,
            "prompt_version": prompt_version,
            "caller_module": caller_module,
            "input_summary": (input_summary or "")[:512] or None,
            "token_estimate": token_estimate,
            "latency_ms": latency_ms,
            "retry_count": retry_count,
            "success": success,
            "schema_valid": schema_valid,
            "fallback_used": fallback_used,
            "error_code": error_code,
            "agent_id": agent_id or snap.agent_id,
            "simulation_id": snap.simulation_id,
            "trace_id": snap.trace_id,
            "span_id": snap.span_id,
            "created_at": now,
        }
        async with self._lock:
            self._buf.llm_calls.append(record)
        # LLM 调用也推一条 observability 事件
        await self.record_event(
            category=CATEGORY_LLM_CALL,
            event_type="llm.call_completed" if success else "llm.call_failed",
            level="INFO" if success else "WARNING",
            title=f"LLM {model} {'ok' if success else 'fail'}",
            payload={
                "provider": provider,
                "model": model,
                "latency_ms": latency_ms,
                "retry_count": retry_count,
                "schema_valid": schema_valid,
                "fallback_used": fallback_used,
                "error_code": error_code,
            },
            duration_ms=latency_ms,
        )

    async def record_tool_call(
        self,
        *,
        tool: str,
        arguments: dict[str, Any],
        success: bool,
        duration_ms: int,
        schema_valid: bool | None = None,
        permission_valid: bool | None = None,
        world_state_valid: bool | None = None,
        result_summary: str | None = None,
        error_code: str | None = None,
        caller_agent_id: str | None = None,
        entity_type: str | None = None,
        source: str | None = None,
        policy_outcome: str | None = None,
    ) -> None:
        snap = current_trace()
        now = utcnow()
        record = {
            "tool": tool,
            "arguments": _redact_arguments(arguments),
            "success": success,
            "duration_ms": duration_ms,
            "schema_valid": schema_valid,
            "permission_valid": permission_valid,
            "world_state_valid": world_state_valid,
            "result_summary": (result_summary or "")[:512] or None,
            "error_code": error_code,
            "caller_agent_id": caller_agent_id or snap.agent_id,
            "entity_type": entity_type,
            "source": source,
            "policy_outcome": policy_outcome,
            "simulation_id": snap.simulation_id,
            "trace_id": snap.trace_id,
            "span_id": snap.span_id,
            "created_at": now,
        }
        async with self._lock:
            self._buf.tool_calls.append(record)
        await self.record_event(
            category=CATEGORY_TOOL_CALL,
            event_type="tool.call_completed" if success else "tool.call_failed",
            level="INFO" if success else "WARNING",
            title=f"tool {tool} {'ok' if success else 'fail'}",
            payload={
                "tool": tool,
                "success": success,
                "duration_ms": duration_ms,
                "error_code": error_code,
            },
            duration_ms=duration_ms,
            entity_id=caller_agent_id,
        )

    async def record_task_status(
        self,
        *,
        task_id: str,
        from_status: str | None,
        to_status: str,
        retry_count: int = 0,
        message: str | None = None,
        error_code: str | None = None,
    ) -> None:
        snap = current_trace()
        now = utcnow()
        record = {
            "task_id": task_id,
            "from_status": from_status,
            "to_status": to_status,
            "retry_count": retry_count,
            "message": (message or "")[:1000] or None,
            "error_code": error_code,
            "trace_id": snap.trace_id,
            "created_at": now,
        }
        async with self._lock:
            self._buf.task_status.append(record)
        # Redis 摘要（供观测平台轮询）
        try:
            redis = get_redis()
            await redis.set_json(
                key_task_status(task_id),
                {
                    "task_id": task_id,
                    "status": to_status,
                    "retry_count": retry_count,
                    "updated_at": now.isoformat(),
                    "message": record["message"],
                    "error_code": error_code,
                },
                ttl_seconds=3600,
            )
        except Exception:
            pass
        await self.record_event(
            category=CATEGORY_TASK,
            event_type=f"task.{to_status}",
            level="INFO" if to_status in {"pending", "running", "succeeded"} else "WARNING",
            title=f"task {task_id[:12]} {to_status}",
            payload={
                "task_id": task_id,
                "from_status": from_status,
                "to_status": to_status,
                "retry_count": retry_count,
                "message": record["message"],
                "error_code": error_code,
            },
            task_id=task_id,
        )

    # ------------------------------------------------------------------
    # 内部：批量落库
    # ------------------------------------------------------------------

    async def _maybe_sync_flush(self) -> None:
        # 达到阈值时立刻 flush 一次（防止短时突发把内存打爆）
        total = (
            len(self._buf.events)
            + len(self._buf.llm_calls)
            + len(self._buf.tool_calls)
            + len(self._buf.task_status)
        )
        if total >= self.batch_size * 2:
            await self._flush_now()

    async def _flush_now(self) -> None:
        if self._session_factory is None:
            return
        async with self._lock:
            events = list(self._buf.events)
            llm_calls = list(self._buf.llm_calls)
            tool_calls = list(self._buf.tool_calls)
            task_status = list(self._buf.task_status)
            self._buf.events.clear()
            self._buf.llm_calls.clear()
            self._buf.tool_calls.clear()
            self._buf.task_status.clear()

        if not any([events, llm_calls, tool_calls, task_status]):
            return

        try:
            async with self._session_factory() as session:
                for record in events:
                    session.add(ObservabilityEvent(**record))
                for record in llm_calls:
                    session.add(LLMCallRecord(**record))
                for record in tool_calls:
                    session.add(ToolCallRecord(**record))
                for record in task_status:
                    session.add(TaskStatusLog(**record))
                await session.commit()
        except Exception:
            logger.exception(
                "observer flush db error, discarding batch",
                extra={
                    "events": len(events),
                    "llm_calls": len(llm_calls),
                    "tool_calls": len(tool_calls),
                    "task_status": len(task_status),
                },
            )

    async def _publish_live(self, record: dict[str, Any]) -> None:
        """向事件总线发布实时事件，供观测 WS 消费。"""
        try:
            await get_event_bus().publish(
                OBSERVABILITY_TOPIC,
                {
                    "simulation_id": record.get("simulation_id"),
                    "trace_id": record.get("trace_id"),
                    "span_id": record.get("span_id"),
                    "category": record.get("category"),
                    "event_type": record.get("event_type"),
                    "level": record.get("level"),
                    "title": record.get("title"),
                    "entity_id": record.get("entity_id"),
                    "player_id": record.get("player_id"),
                    "task_id": record.get("task_id"),
                    "payload": record.get("payload"),
                    "created_at": (
                        record["created_at"].isoformat()
                        if record.get("created_at") is not None
                        else None
                    ),
                },
            )
        except Exception:
            pass


_REDACT_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "token",
    "password",
    "secret",
    "raw_prompt",
    "prompt_full",
}


def _redact_arguments(args: dict[str, Any]) -> dict[str, Any]:
    """敏感字段脱敏；超长字符串截断。"""
    if not isinstance(args, dict):
        return {"__value__": str(args)[:256]}
    out: dict[str, Any] = {}
    for key, value in args.items():
        lk = str(key).lower()
        if lk in _REDACT_KEYS:
            out[key] = "***"
            continue
        if isinstance(value, str) and len(value) > 512:
            out[key] = value[:512] + "…"
        elif isinstance(value, dict):
            out[key] = _redact_arguments(value)
        elif isinstance(value, list):
            out[key] = [
                _redact_arguments(v) if isinstance(v, dict) else v
                for v in value[:8]
            ]
        else:
            out[key] = value
    return out


_observer: Observer | None = None


def get_observer() -> Observer:
    global _observer
    if _observer is None:
        _observer = Observer()
    return _observer


__all__ = [
    "CATEGORY_AGENT_DECISION",
    "CATEGORY_AGENT_RUNTIME",
    "CATEGORY_ERROR",
    "CATEGORY_LLM_CALL",
    "CATEGORY_MEMORY",
    "CATEGORY_PERFORMANCE",
    "CATEGORY_PLAYER_QUERY",
    "CATEGORY_SIMULATION_TICK",
    "CATEGORY_TASK",
    "CATEGORY_TOOL_CALL",
    "CATEGORY_WEBSOCKET",
    "CATEGORY_WORLD_EVENT",
    "OBSERVABILITY_TOPIC",
    "Observer",
    "get_observer",
    "TraceContext",
]
