"""
可观测性 REST API（阶段 14.3）。

所有路径挂在 ``/api/observability`` 下。普通玩家 UI 不访问这里。

- ``GET  /dashboard``                     — 实时聚合指标
- ``GET  /events``                         — 观测事件（可筛 category / trace_id / level）
- ``GET  /world-events``                   — 世界事件流（可筛类型 / 实体 / 场景）
- ``GET  /agents/{agent_id}/runtime``      — Agent 运行视图（状态 + 最近事件 + 最近记忆）
- ``GET  /traces/{trace_id}``              — trace 完整 span 树
- ``GET  /llm-calls``                      — LLM 调用列表
- ``GET  /tool-calls``                     — Tool 调用列表
- ``GET  /tasks``                          — 任务队列列表
- ``GET  /memories``                       — 记忆列表
- ``GET  /errors``                         — 错误中心聚合

健康检查：``GET /health/db``、``/health/redis``、``/health/llm``
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import TargetNotFound
from app.core.logging import get_logger
from app.core.redis_client import get_redis
from app.core.time import utcnow
from app.db.models import (
    Agent,
    AgentState,
    LLMCallRecord,
    Memory,
    ObservabilityEvent,
    Task,
    ToolCallRecord,
    WorldEvent,
)
from app.db.session import get_session
from app.services.observer import (
    CATEGORY_AGENT_DECISION,
    CATEGORY_ERROR,
    CATEGORY_LLM_CALL,
    CATEGORY_PLAYER_QUERY,
    CATEGORY_TOOL_CALL,
    CATEGORY_WORLD_EVENT,
)
from app.services.simulation_runtime import get_simulation_runtime
from app.websocket.gateway import get_connection_manager
from app.websocket.observability import get_observability_manager

logger = get_logger(__name__)

router = APIRouter(prefix="/observability", tags=["observability"])


# -----------------------------------------------------------------------------
# Dashboard
# -----------------------------------------------------------------------------


@router.get("/dashboard")
async def get_dashboard(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    now = utcnow()
    window_start = now - timedelta(minutes=5)
    sim = get_simulation_runtime().engine.get_simulation()

    # LLM 聚合
    llm_stmt = select(
        func.count(LLMCallRecord.id),
        func.avg(LLMCallRecord.latency_ms),
        func.sum(func.cast(LLMCallRecord.success == False, type_=__import__("sqlalchemy").Integer)),  # noqa: E712
    ).where(LLMCallRecord.created_at >= window_start)
    total_llm, avg_latency, failed_llm = (await session.execute(llm_stmt)).one()

    # 任务聚合
    task_stmt = select(Task.status, func.count(Task.id)).group_by(Task.status)
    task_counts = {
        row[0]: int(row[1]) for row in (await session.execute(task_stmt)).all()
    }

    # 错误聚合（最近 5 分钟）
    err_stmt = select(func.count(ObservabilityEvent.id)).where(
        and_(
            ObservabilityEvent.category == CATEGORY_ERROR,
            ObservabilityEvent.created_at >= window_start,
        )
    )
    error_count = int((await session.execute(err_stmt)).scalar() or 0)

    # WS
    sim_id = sim.id if sim else None
    online = get_connection_manager().total(sim_id) if sim_id else 0
    obs_online = get_observability_manager().total(sim_id) if sim_id else 0

    return {
        "simulation": {
            "id": sim_id,
            "status": sim.status if sim else None,
            "step": sim.current_step if sim else None,
            "speed": sim.speed_multiplier if sim else None,
            "world_time": sim.world_time.isoformat() if sim else None,
        },
        "websocket": {
            "simulation_clients": online,
            "observability_clients": obs_online,
        },
        "llm_calls_last_5m": {
            "total": int(total_llm or 0),
            "avg_latency_ms": float(avg_latency or 0.0),
            "failed": int(failed_llm or 0),
            "error_rate": (
                float(failed_llm or 0) / float(total_llm)
                if total_llm
                else 0.0
            ),
        },
        "tasks": task_counts,
        "errors_last_5m": error_count,
        "generated_at": now.isoformat(),
    }


# -----------------------------------------------------------------------------
# Events
# -----------------------------------------------------------------------------


@router.get("/events")
async def list_events(
    session: AsyncSession = Depends(get_session),
    category: str | None = Query(None),
    trace_id: str | None = Query(None),
    level: str | None = Query(None),
    simulation_id: str | None = Query(None),
    entity_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    stmt = select(ObservabilityEvent).order_by(desc(ObservabilityEvent.created_at)).limit(limit)
    if category:
        stmt = stmt.where(ObservabilityEvent.category == category)
    if trace_id:
        stmt = stmt.where(ObservabilityEvent.trace_id == trace_id)
    if level:
        stmt = stmt.where(ObservabilityEvent.level == level)
    if simulation_id:
        stmt = stmt.where(ObservabilityEvent.simulation_id == simulation_id)
    if entity_id:
        stmt = stmt.where(ObservabilityEvent.entity_id == entity_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [_serialize_obs_event(r) for r in rows]


@router.get("/world-events")
async def list_world_events(
    session: AsyncSession = Depends(get_session),
    simulation_id: str | None = Query(None),
    event_type: str | None = Query(None),
    scene_id: str | None = Query(None),
    entity_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    stmt = select(WorldEvent).order_by(desc(WorldEvent.created_at)).limit(limit)
    if simulation_id:
        stmt = stmt.where(WorldEvent.simulation_id == simulation_id)
    if event_type:
        stmt = stmt.where(WorldEvent.event_type == event_type)
    if scene_id:
        stmt = stmt.where(WorldEvent.scene_id == scene_id)
    if entity_id:
        stmt = stmt.where(
            (WorldEvent.actor_entity_id == entity_id)
            | (WorldEvent.target_entity_id == entity_id)
        )
    rows = (await session.execute(stmt)).scalars().all()
    return [_serialize_world_event(r) for r in rows]


@router.get("/agents/{agent_id}/runtime")
async def agent_runtime(
    agent_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise TargetNotFound(f"agent {agent_id} not found")
    state = await session.get(AgentState, agent_id)

    # 最近 20 条事件
    events_stmt = (
        select(ObservabilityEvent)
        .where(ObservabilityEvent.entity_id == agent_id)
        .order_by(desc(ObservabilityEvent.created_at))
        .limit(20)
    )
    events = [_serialize_obs_event(r) for r in (await session.execute(events_stmt)).scalars().all()]

    # 最近 10 条记忆
    mem_stmt = (
        select(Memory)
        .where(Memory.agent_id == agent_id)
        .order_by(desc(Memory.created_at))
        .limit(10)
    )
    memories = [_serialize_memory(r) for r in (await session.execute(mem_stmt)).scalars().all()]

    # Redis runtime 热数据
    from app.core.redis_client import key_agent_runtime

    cache = await get_redis().get_json(key_agent_runtime(agent_id))

    return {
        "agent": {
            "id": agent.id,
            "name": agent.name,
            "entity_type": agent.entity_type,
            "personality": agent.personality,
            "occupation": agent.occupation,
        },
        "state": (
            {
                "scene_id": state.scene_id,
                "x": state.x,
                "y": state.y,
                "state": state.state,
                "emotion": state.emotion,
                "energy": state.energy,
                "hunger": state.hunger,
                "social_need": state.social_need,
                "fear": state.fear,
                "status_effects": state.status_effects,
                "current_goal": state.current_goal,
                "facing": state.facing,
                "path": state.path,
            }
            if state is not None
            else None
        ),
        "redis_runtime": cache,
        "recent_events": events,
        "recent_memories": memories,
    }


# -----------------------------------------------------------------------------
# Trace
# -----------------------------------------------------------------------------


@router.get("/traces/{trace_id}")
async def get_trace(
    trace_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    events_stmt = (
        select(ObservabilityEvent)
        .where(ObservabilityEvent.trace_id == trace_id)
        .order_by(ObservabilityEvent.created_at.asc())
    )
    events = [_serialize_obs_event(r) for r in (await session.execute(events_stmt)).scalars().all()]

    llm_stmt = (
        select(LLMCallRecord)
        .where(LLMCallRecord.trace_id == trace_id)
        .order_by(LLMCallRecord.created_at.asc())
    )
    llm_calls = [_serialize_llm_call(r) for r in (await session.execute(llm_stmt)).scalars().all()]

    tool_stmt = (
        select(ToolCallRecord)
        .where(ToolCallRecord.trace_id == trace_id)
        .order_by(ToolCallRecord.created_at.asc())
    )
    tool_calls = [_serialize_tool_call(r) for r in (await session.execute(tool_stmt)).scalars().all()]

    task_stmt = select(Task).where(Task.trace_id == trace_id).limit(20)
    tasks = [_serialize_task(r) for r in (await session.execute(task_stmt)).scalars().all()]

    if not events and not llm_calls and not tool_calls:
        raise TargetNotFound(f"trace {trace_id} not found")

    return {
        "trace_id": trace_id,
        "events": events,
        "llm_calls": llm_calls,
        "tool_calls": tool_calls,
        "tasks": tasks,
    }


# -----------------------------------------------------------------------------
# LLM / Tool / Task / Memory / Error 列表
# -----------------------------------------------------------------------------


@router.get("/llm-calls")
async def list_llm_calls(
    session: AsyncSession = Depends(get_session),
    success: bool | None = Query(None),
    model: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    stmt = select(LLMCallRecord).order_by(desc(LLMCallRecord.created_at)).limit(limit)
    if success is not None:
        stmt = stmt.where(LLMCallRecord.success == success)
    if model:
        stmt = stmt.where(LLMCallRecord.model == model)
    rows = (await session.execute(stmt)).scalars().all()
    return [_serialize_llm_call(r) for r in rows]


@router.get("/tool-calls")
async def list_tool_calls(
    session: AsyncSession = Depends(get_session),
    tool: str | None = Query(None),
    success: bool | None = Query(None),
    agent_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    stmt = select(ToolCallRecord).order_by(desc(ToolCallRecord.created_at)).limit(limit)
    if tool:
        stmt = stmt.where(ToolCallRecord.tool == tool)
    if success is not None:
        stmt = stmt.where(ToolCallRecord.success == success)
    if agent_id:
        stmt = stmt.where(ToolCallRecord.caller_agent_id == agent_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [_serialize_tool_call(r) for r in rows]


@router.get("/tasks")
async def list_tasks(
    session: AsyncSession = Depends(get_session),
    status: str | None = Query(None),
    task_type: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    stmt = select(Task).order_by(desc(Task.enqueued_at)).limit(limit)
    if status:
        stmt = stmt.where(Task.status == status)
    if task_type:
        stmt = stmt.where(Task.task_type == task_type)
    rows = (await session.execute(stmt)).scalars().all()
    return [_serialize_task(r) for r in rows]


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    task = await session.get(Task, task_id)
    if task is None:
        raise TargetNotFound(f"task {task_id} not found")
    return _serialize_task(task)


@router.get("/memories")
async def list_memories(
    session: AsyncSession = Depends(get_session),
    agent_id: str | None = Query(None),
    scope: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    stmt = select(Memory).order_by(desc(Memory.created_at)).limit(limit)
    if agent_id:
        stmt = stmt.where(Memory.agent_id == agent_id)
    if scope:
        stmt = stmt.where(Memory.scope == scope)
    rows = (await session.execute(stmt)).scalars().all()
    return [_serialize_memory(r) for r in rows]


@router.get("/errors")
async def list_errors(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    stmt = (
        select(ObservabilityEvent)
        .where(ObservabilityEvent.level.in_(["ERROR", "WARNING"]))
        .order_by(desc(ObservabilityEvent.created_at))
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_serialize_obs_event(r) for r in rows]


# -----------------------------------------------------------------------------
# Health checks
# -----------------------------------------------------------------------------


health_router = APIRouter(prefix="/health", tags=["observability"])


@health_router.get("/db")
async def health_db(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    try:
        await session.execute(select(1))
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}


@health_router.get("/redis")
async def health_redis() -> dict[str, Any]:
    return await get_redis().health_check()


@health_router.get("/llm")
async def health_llm() -> dict[str, Any]:
    settings = get_settings()
    return {
        "ok": bool(settings.llm_is_configured),
        "provider": settings.llm_provider,
        "model": settings.llm_chat_model,
        "base_url": settings.llm_base_url,
        "enabled": settings.llm_enabled,
        "has_api_key": bool(settings.llm_api_key),
    }


# -----------------------------------------------------------------------------
# 序列化辅助
# -----------------------------------------------------------------------------


def _serialize_obs_event(r: ObservabilityEvent) -> dict[str, Any]:
    return {
        "id": r.id,
        "simulation_id": r.simulation_id,
        "trace_id": r.trace_id,
        "span_id": r.span_id,
        "parent_span_id": r.parent_span_id,
        "category": r.category,
        "event_type": r.event_type,
        "level": r.level,
        "title": r.title,
        "entity_id": r.entity_id,
        "player_id": r.player_id,
        "task_id": r.task_id,
        "world_step": r.world_step,
        "duration_ms": r.duration_ms,
        "payload": r.payload,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _serialize_world_event(r: WorldEvent) -> dict[str, Any]:
    return {
        "id": r.id,
        "simulation_id": r.simulation_id,
        "event_type": r.event_type,
        "source": r.source,
        "actor_entity_id": r.actor_entity_id,
        "target_entity_id": r.target_entity_id,
        "location_id": r.location_id,
        "scene_id": r.scene_id,
        "description": r.description,
        "importance": r.importance,
        "payload": r.payload,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _serialize_llm_call(r: LLMCallRecord) -> dict[str, Any]:
    return {
        "id": r.id,
        "trace_id": r.trace_id,
        "span_id": r.span_id,
        "simulation_id": r.simulation_id,
        "agent_id": r.agent_id,
        "provider": r.provider,
        "model": r.model,
        "prompt_template_id": r.prompt_template_id,
        "prompt_version": r.prompt_version,
        "caller_module": r.caller_module,
        "input_summary": r.input_summary,
        "token_estimate": r.token_estimate,
        "latency_ms": r.latency_ms,
        "retry_count": r.retry_count,
        "success": r.success,
        "schema_valid": r.schema_valid,
        "fallback_used": r.fallback_used,
        "error_code": r.error_code,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _serialize_tool_call(r: ToolCallRecord) -> dict[str, Any]:
    return {
        "id": r.id,
        "trace_id": r.trace_id,
        "span_id": r.span_id,
        "simulation_id": r.simulation_id,
        "tool": r.tool,
        "caller_agent_id": r.caller_agent_id,
        "entity_type": r.entity_type,
        "arguments": r.arguments,
        "schema_valid": r.schema_valid,
        "permission_valid": r.permission_valid,
        "world_state_valid": r.world_state_valid,
        "success": r.success,
        "duration_ms": r.duration_ms,
        "result_summary": r.result_summary,
        "error_code": r.error_code,
        "source": r.source,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _serialize_task(r: Task) -> dict[str, Any]:
    trace_meta: dict[str, Any] = {}
    if isinstance(r.payload, dict) and "__trace__" in r.payload:
        trace_meta = r.payload["__trace__"] or {}
    return {
        "id": r.id,
        "task_type": r.task_type,
        "status": r.status,
        "priority": r.priority,
        "entity_id": r.entity_id,
        "simulation_id": r.simulation_id,
        "simulation_step": r.simulation_step,
        "idempotency_key": r.idempotency_key,
        "retry_count": r.retry_count,
        "max_retries": r.max_retries,
        "trace_id": r.trace_id,
        "parent_trace_id": trace_meta.get("parent_trace_id"),
        "last_error": r.last_error,
        "payload": {k: v for k, v in (r.payload or {}).items() if k != "__trace__"},
        "result": r.result,
        "enqueued_at": r.enqueued_at.isoformat() if r.enqueued_at else None,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
    }


def _serialize_memory(r: Memory) -> dict[str, Any]:
    return {
        "id": r.id,
        "agent_id": r.agent_id,
        "memory_type": r.memory_type,
        "scope": r.scope,
        "description": r.description,
        "importance": r.importance,
        "emotional_valence": r.emotional_valence,
        "keywords": r.keywords,
        "evidence_memory_ids": r.evidence_memory_ids,
        "has_embedding": r.embedding is not None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "last_accessed_at": r.last_accessed_at.isoformat() if r.last_accessed_at else None,
        "ttl_expires_at": r.ttl_expires_at.isoformat() if r.ttl_expires_at else None,
    }


__all__ = ["router", "health_router"]
