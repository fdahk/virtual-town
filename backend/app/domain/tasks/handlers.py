"""
7 类任务处理器（阶段 12 §3）。

- agent_decision         — 让 LLM 为某个 Agent 做决策（核心）。
- generate_daily_plan    — 日计划生成（占位，阶段 15 做深化）。
- generate_dialogue_reply — 生成 NPC 对话回复（阶段 16 会深化）。
- write_memory_embedding — 为已写入的记忆补算 embedding。
- daily_reflection       — 每日反思与总结。
- query_rewrite          — 玩家文本改写（占位）。
- relationship_update    — 关系摘要更新（占位）。

占位 handler 只做最小兜底逻辑，保证阶段 12 能跑通 ``pending → running →
succeeded / failed`` 生命周期。阶段 15/16 会把具体实现填充进来。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, update

from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import Memory, Relationship
from app.domain.tasks.registry import (
    TaskContext,
    TaskHandler,
    TaskResult,
    get_task_registry,
)

logger = get_logger(__name__)


# -----------------------------------------------------------------------------
# agent_decision
# -----------------------------------------------------------------------------


class AgentDecisionHandler(TaskHandler):
    task_type = "agent_decision"
    default_timeout_seconds = 45.0
    default_max_retries = 1

    async def handle(self, ctx: TaskContext) -> TaskResult:
        from app.llm.agent_decision import decide_with_llm

        agent_id = ctx.payload.get("agent_id") or ctx.entity_id
        simulation_id = ctx.payload.get("simulation_id") or ctx.simulation_id or "sim"
        world_time_raw = ctx.payload.get("world_time")
        try:
            world_time = (
                datetime.fromisoformat(world_time_raw)
                if isinstance(world_time_raw, str)
                else (world_time_raw or utcnow())
            )
        except Exception:
            world_time = utcnow()

        if not agent_id:
            return TaskResult(
                success=False,
                error_code="TOOL_INVALID_ARGUMENTS",
                error_message="agent_id missing",
                retryable=False,
            )

        try:
            results = await decide_with_llm(
                ctx.session,
                agent_id=agent_id,
                simulation_id=simulation_id,
                world_time=world_time,
            )
        except Exception as exc:
            logger.exception("agent_decision task failed")
            return TaskResult(
                success=False,
                error_code="LLM_UPSTREAM_ERROR",
                error_message=str(exc),
                retryable=True,
                fallback_hint="fallback_to_rule",
            )

        if results is None:
            return TaskResult(
                success=False,
                error_code="LLM_NOT_CONFIGURED",
                error_message="LLM not configured or returned None",
                retryable=False,
                fallback_hint="fallback_to_rule",
            )

        any_success = any(r.success for r in results)
        summary = {
            "agent_id": agent_id,
            "tool_results": [
                {
                    "tool": r.tool,
                    "success": r.success,
                    "error_code": (r.error.code if r.error else None),
                }
                for r in results
            ],
        }
        if not any_success:
            return TaskResult(
                success=False,
                result=summary,
                error_code="TOOL_EXECUTION_FAILED",
                error_message="all tools failed",
                retryable=False,
                fallback_hint="fallback_to_rule",
            )
        return TaskResult(success=True, result=summary)


# -----------------------------------------------------------------------------
# write_memory_embedding
# -----------------------------------------------------------------------------


class WriteMemoryEmbeddingHandler(TaskHandler):
    task_type = "write_memory_embedding"
    default_timeout_seconds = 20.0
    default_max_retries = 2

    async def handle(self, ctx: TaskContext) -> TaskResult:
        from app.llm.embedding import get_embedding_service

        memory_id = ctx.payload.get("memory_id")
        if not memory_id:
            return TaskResult(
                success=False,
                error_code="TOOL_INVALID_ARGUMENTS",
                error_message="memory_id missing",
                retryable=False,
            )
        mem = await ctx.session.get(Memory, memory_id)
        if mem is None:
            return TaskResult(
                success=False,
                error_code="MEMORY_NOT_FOUND",
                error_message=f"memory {memory_id} not found",
                retryable=False,
            )
        if mem.embedding is not None:
            return TaskResult(success=True, result={"memory_id": memory_id, "skipped": True})
        try:
            vec = await get_embedding_service().embed(mem.description)
        except Exception as exc:
            return TaskResult(
                success=False,
                error_code="MEMORY_EMBEDDING_FAILED",
                error_message=str(exc),
                retryable=True,
            )
        if vec is None:
            return TaskResult(
                success=False,
                error_code="LLM_NOT_CONFIGURED",
                error_message="embedding not available",
                retryable=False,
            )
        mem.embedding = vec
        await ctx.session.commit()
        return TaskResult(success=True, result={"memory_id": memory_id})


# -----------------------------------------------------------------------------
# daily_reflection
# -----------------------------------------------------------------------------


class DailyReflectionHandler(TaskHandler):
    task_type = "daily_reflection"
    default_timeout_seconds = 40.0
    default_max_retries = 1

    async def handle(self, ctx: TaskContext) -> TaskResult:
        from app.domain.memory.reflection import maybe_daily_summary, maybe_reflect

        agent_id = ctx.payload.get("agent_id") or ctx.entity_id
        if not agent_id:
            return TaskResult(
                success=False,
                error_code="TOOL_INVALID_ARGUMENTS",
                error_message="agent_id missing",
                retryable=False,
            )
        world_time_raw = ctx.payload.get("world_time")
        try:
            world_time = (
                datetime.fromisoformat(world_time_raw)
                if isinstance(world_time_raw, str)
                else (world_time_raw or utcnow())
            )
        except Exception:
            world_time = utcnow()

        summary_mem = None
        try:
            reflections = await maybe_reflect(ctx.session, agent_id, world_time=world_time)
            summary_mem = await maybe_daily_summary(ctx.session, agent_id, world_time=world_time)
        except Exception as exc:
            return TaskResult(
                success=False,
                error_code="MEMORY_WRITE_FAILED",
                error_message=str(exc),
                retryable=True,
            )
        return TaskResult(
            success=True,
            result={
                "agent_id": agent_id,
                "reflection_ids": [m.id for m in (reflections or [])],
                "summary_id": summary_mem.id if summary_mem else None,
            },
        )


# -----------------------------------------------------------------------------
# relationship_update
# -----------------------------------------------------------------------------


class RelationshipUpdateHandler(TaskHandler):
    task_type = "relationship_update"
    default_timeout_seconds = 20.0
    default_max_retries = 1

    async def handle(self, ctx: TaskContext) -> TaskResult:
        from_id = ctx.payload.get("from_agent_id")
        to_id = ctx.payload.get("to_entity_id")
        delta = ctx.payload.get("delta") or {}
        summary_hint = ctx.payload.get("summary")
        if not from_id or not to_id:
            return TaskResult(
                success=False,
                error_code="TOOL_INVALID_ARGUMENTS",
                error_message="from_agent_id/to_entity_id missing",
                retryable=False,
            )
        stmt = (
            select(Relationship)
            .where(
                Relationship.from_agent_id == from_id,
                Relationship.to_entity_id == to_id,
            )
        )
        rel = (await ctx.session.execute(stmt)).scalar_one_or_none()
        if rel is None:
            rel = Relationship(from_agent_id=from_id, to_entity_id=to_id)
            ctx.session.add(rel)
        for key in ("familiarity", "trust", "affection", "fear"):
            if key in delta:
                try:
                    current = float(getattr(rel, key) or 0.0)
                    rel_val = current + float(delta[key])
                    setattr(rel, key, max(min(rel_val, 10.0), -10.0))
                except Exception:
                    continue
        if summary_hint:
            rel.summary = str(summary_hint)[:2000]
        await ctx.session.commit()
        return TaskResult(
            success=True,
            result={
                "from_agent_id": from_id,
                "to_entity_id": to_id,
                "familiarity": rel.familiarity,
                "trust": rel.trust,
                "affection": rel.affection,
                "fear": rel.fear,
            },
        )


# -----------------------------------------------------------------------------
# 占位 handler（阶段 15 / 16 深化）
# -----------------------------------------------------------------------------


class _PlaceholderHandler(TaskHandler):
    """占位：只记录任务被处理，返回 success。"""

    task_type = "__placeholder__"

    async def handle(self, ctx: TaskContext) -> TaskResult:
        logger.info(
            "placeholder task executed",
            extra={"task_type": ctx.task_type, "payload_keys": list(ctx.payload.keys())},
        )
        return TaskResult(
            success=True,
            result={"placeholder": True, "task_type": ctx.task_type},
        )


class GenerateDailyPlanHandler(_PlaceholderHandler):
    task_type = "generate_daily_plan"
    default_timeout_seconds = 30.0


class GenerateDialogueReplyHandler(_PlaceholderHandler):
    task_type = "generate_dialogue_reply"
    default_timeout_seconds = 30.0


class QueryRewriteHandler(_PlaceholderHandler):
    task_type = "query_rewrite"
    default_timeout_seconds = 15.0


# -----------------------------------------------------------------------------
# 注册
# -----------------------------------------------------------------------------


def register_default_handlers() -> None:
    reg = get_task_registry()
    reg.register(AgentDecisionHandler())
    reg.register(WriteMemoryEmbeddingHandler())
    reg.register(DailyReflectionHandler())
    reg.register(RelationshipUpdateHandler())
    reg.register(GenerateDailyPlanHandler())
    reg.register(GenerateDialogueReplyHandler())
    reg.register(QueryRewriteHandler())


__all__ = [
    "AgentDecisionHandler",
    "DailyReflectionHandler",
    "GenerateDailyPlanHandler",
    "GenerateDialogueReplyHandler",
    "QueryRewriteHandler",
    "RelationshipUpdateHandler",
    "WriteMemoryEmbeddingHandler",
    "register_default_handlers",
]
