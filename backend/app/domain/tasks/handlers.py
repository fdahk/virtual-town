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

from sqlalchemy import select

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
    """关系数值 delta 累加 + summary 刷新（阶段 15.4）。

    payload 可选字段：
    - ``delta``：{familiarity / trust / affection / fear}
    - ``summary``：直接覆盖一段摘要
    - ``regenerate_summary``：True 时自动基于 memories 让 LLM 生成摘要；
      LLM 不可用则规则兜底。
    """

    task_type = "relationship_update"
    default_timeout_seconds = 20.0
    default_max_retries = 1

    async def handle(self, ctx: TaskContext) -> TaskResult:
        from app.db.models import Agent, Memory
        from app.llm.client import get_llm_client
        from app.prompts import render_prompt
        from sqlalchemy import desc as _desc

        from_id = ctx.payload.get("from_agent_id")
        to_id = ctx.payload.get("to_entity_id")
        delta = ctx.payload.get("delta") or {}
        summary_hint = ctx.payload.get("summary")
        regenerate = bool(ctx.payload.get("regenerate_summary"))
        if not from_id or not to_id:
            return TaskResult(
                success=False,
                error_code="TOOL_INVALID_ARGUMENTS",
                error_message="from_agent_id/to_entity_id missing",
                retryable=False,
            )
        stmt = select(Relationship).where(
            Relationship.from_agent_id == from_id,
            Relationship.to_entity_id == to_id,
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

        # 自动刷新摘要（LLM 优先，规则兜底）
        if regenerate:
            try:
                from_agent = await ctx.session.get(Agent, from_id)
                to_agent = await ctx.session.get(Agent, to_id)
                evidence_stmt = (
                    select(Memory)
                    .where(
                        Memory.agent_id == from_id,
                        (Memory.subject == (to_agent.name if to_agent else None))
                        | (Memory.object == (to_agent.name if to_agent else None))
                        | (Memory.description.ilike(f"%{to_id}%")),
                    )
                    .order_by(_desc(Memory.created_at))
                    .limit(6)
                )
                mems = (await ctx.session.execute(evidence_stmt)).scalars().all()
                evidence_block = (
                    "\n".join(
                        f"- [{m.memory_type} imp={m.importance}] {m.description}"
                        for m in mems
                    )
                    or "- 暂无"
                )

                client = get_llm_client()
                out_summary: str | None = None
                if client.settings.llm_is_configured:
                    user, meta = render_prompt(
                        "relationship_summary",
                        {
                            "from_block": (
                                f"- {from_agent.name if from_agent else from_id}"
                            ),
                            "to_block": (
                                f"- {to_agent.name if to_agent else to_id}"
                            ),
                            "evidence_block": evidence_block,
                            "familiarity": f"{float(rel.familiarity):.1f}",
                            "trust": f"{float(rel.trust):.1f}",
                            "affection": f"{float(rel.affection):.1f}",
                            "fear": f"{float(rel.fear):.1f}",
                        },
                    )
                    data = await client.chat_json(
                        system="你必须只输出 JSON。",
                        user=user,
                        model=client.settings.model_for_role(meta.model_role or "chat"),
                        temperature=0.3,
                        max_tokens=200,
                        prompt_template_id=meta.id,
                        prompt_version=meta.version,
                        caller_module="relationship_summary",
                    )
                    if data and isinstance(data.get("summary"), str):
                        out_summary = data["summary"][:2000]
                if out_summary is None and mems:
                    out_summary = (
                        f"最近 {len(mems)} 次互动：" + "；".join(
                            m.description[:40] for m in mems[:3]
                        )
                    )[:2000]
                if out_summary:
                    rel.summary = out_summary

                # 清空 Redis 累计计数（阶段 15.4）
                try:
                    from app.domain.planning.relationship import reset_summary_counter

                    await reset_summary_counter(
                        from_agent_id=from_id, to_entity_id=to_id
                    )
                except Exception:
                    pass
            except Exception:
                logger.exception("relationship summary regenerate failed")

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
                "summary": rel.summary,
            },
        )


# -----------------------------------------------------------------------------
# 占位 handler（阶段 15 / 16 深化）
# -----------------------------------------------------------------------------


class GenerateDailyPlanHandler(TaskHandler):
    """生成（或刷新）某个 Agent 的 daily_plan（阶段 15.1）。"""

    task_type = "generate_daily_plan"
    default_timeout_seconds = 30.0
    default_max_retries = 1

    async def handle(self, ctx: TaskContext) -> TaskResult:
        from app.db.models import Agent
        from app.domain.planning import get_planning_service

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
        force = bool(ctx.payload.get("force", False))
        agent = await ctx.session.get(Agent, agent_id)
        if agent is None:
            return TaskResult(
                success=False,
                error_code="AGENT_NOT_FOUND",
                error_message=f"agent {agent_id} missing",
                retryable=False,
            )
        try:
            plan = await get_planning_service().ensure_daily_plan(
                ctx.session, agent, world_time=world_time, force_refresh=force
            )
            await ctx.session.commit()
        except Exception as exc:
            return TaskResult(
                success=False,
                error_code="AGENT_DECISION_FAILED",
                error_message=str(exc),
                retryable=True,
            )
        return TaskResult(
            success=True,
            result={
                "agent_id": agent_id,
                "plan_id": plan.id,
                "segments": len(plan.segments),
                "source": plan.source,
            },
        )


class QueryRewriteHandler(TaskHandler):
    """玩家文本改写任务（阶段 16.1）。

    运行时 payload：
        {player_agent_id, target_agent_id, scene_id, raw_text,
         conversation_id (optional)}

    结果写入 ``tasks.result``，由调用方轮询或实时消费。
    """

    task_type = "query_rewrite"
    default_timeout_seconds = 15.0
    default_max_retries = 1

    async def handle(self, ctx: TaskContext) -> TaskResult:
        from app.db.models import Agent
        from app.domain.dialogue.conversation_store import get_conversation_store
        from app.domain.dialogue.query_rewrite import rewrite_query

        p = ctx.payload
        player_id = p.get("player_agent_id")
        target_id = p.get("target_agent_id")
        scene_id = p.get("scene_id")
        raw = p.get("raw_text") or ""
        conv = p.get("conversation_id")
        if not (player_id and target_id and scene_id and raw):
            return TaskResult(
                success=False,
                error_code="TOOL_INVALID_ARGUMENTS",
                error_message="missing player/target/scene/raw_text",
                retryable=False,
            )
        player = await ctx.session.get(Agent, player_id)
        target = await ctx.session.get(Agent, target_id)
        if player is None or target is None:
            return TaskResult(
                success=False,
                error_code="AGENT_NOT_FOUND",
                error_message="player or target missing",
                retryable=False,
            )
        window: list[dict[str, Any]] = []
        if conv:
            try:
                window = await get_conversation_store().recent(conv, limit=4)
            except Exception:
                window = []
        out = await rewrite_query(
            ctx.session,
            player=player,
            target=target,
            scene_id=scene_id,
            raw_text=raw,
            recent_dialogue=window,
        )
        return TaskResult(success=True, result=out.model_dump())


class GenerateDialogueReplyHandler(TaskHandler):
    """NPC 结构化回复任务（阶段 16.4）。

    payload：
        {target_agent_id, player_agent_id, player_text, memories[],
         dialogue_window[], relationship_summary, state_summary}
    """

    task_type = "generate_dialogue_reply"
    default_timeout_seconds = 30.0
    default_max_retries = 1

    async def handle(self, ctx: TaskContext) -> TaskResult:
        from app.db.models import Agent
        from app.domain.dialogue.reply import generate_structured_reply
        from app.services.agent_service import profile_from_orm

        p = ctx.payload
        target_id = p.get("target_agent_id")
        player_id = p.get("player_agent_id")
        if not target_id:
            return TaskResult(
                success=False,
                error_code="TOOL_INVALID_ARGUMENTS",
                error_message="target_agent_id missing",
                retryable=False,
            )
        target = await ctx.session.get(Agent, target_id)
        if target is None:
            return TaskResult(
                success=False,
                error_code="AGENT_NOT_FOUND",
                error_message=f"agent {target_id} missing",
                retryable=False,
            )
        player_name = "玩家"
        if player_id:
            pl = await ctx.session.get(Agent, player_id)
            if pl is not None:
                player_name = pl.name

        out = await generate_structured_reply(
            npc_profile=profile_from_orm(target),
            player_name=player_name,
            player_text=p.get("player_text", ""),
            memories=list(p.get("memories") or []),
            dialogue_window=list(p.get("dialogue_window") or []),
            relationship_summary=p.get("relationship_summary"),
            state_summary=p.get("state_summary"),
        )
        return TaskResult(success=True, result=out.model_dump())


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
