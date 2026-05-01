"""Agent REST 路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import TargetNotFound
from app.db.session import get_session
from app.schemas.agent import (
    AgentProfile,
    AgentProfilePatch,
    AgentRuntimeState,
    Relationship,
)
from app.schemas.memory import Memory, MemorySearchRequest, MemorySearchResult
from app.services.agent_service import get_agent_service

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("", response_model=list[AgentProfile])
async def list_agents(session: AsyncSession = Depends(get_session)) -> list[AgentProfile]:
    return await get_agent_service().list_agents(session)


@router.get("/{agent_id}", response_model=AgentProfile)
async def get_agent(agent_id: str, session: AsyncSession = Depends(get_session)) -> AgentProfile:
    profile = await get_agent_service().get_profile(session, agent_id)
    if profile is None:
        raise TargetNotFound(f"agent {agent_id} not found")
    return profile


@router.patch("/{agent_id}/profile", response_model=AgentProfile)
async def update_agent_profile(
    agent_id: str,
    patch: AgentProfilePatch,
    session: AsyncSession = Depends(get_session),
) -> AgentProfile:
    return await get_agent_service().update_profile(session, agent_id, patch)


@router.get("/{agent_id}/state", response_model=AgentRuntimeState)
async def get_agent_state(
    agent_id: str, session: AsyncSession = Depends(get_session)
) -> AgentRuntimeState:
    state = await get_agent_service().get_runtime_state(session, agent_id)
    if state is None:
        raise TargetNotFound(f"agent {agent_id} not found")
    return state


@router.get("/{agent_id}/relationships", response_model=list[Relationship])
async def list_agent_relationships(
    agent_id: str, session: AsyncSession = Depends(get_session)
) -> list[Relationship]:
    return await get_agent_service().list_relationships(session, agent_id)


@router.get("/{agent_id}/memories", response_model=list[Memory])
async def list_agent_memories(
    agent_id: str,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
) -> list[Memory]:
    return await get_agent_service().list_memories(session, agent_id, limit=limit)


@router.post("/{agent_id}/memory/search", response_model=list[MemorySearchResult])
async def search_agent_memories(
    agent_id: str,
    request: MemorySearchRequest,
    session: AsyncSession = Depends(get_session),
) -> list[MemorySearchResult]:
    return await get_agent_service().search_memories(session, agent_id, request)


@router.post("/{agent_id}/reflect", response_model=list[Memory])
async def force_agent_reflect(
    agent_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[Memory]:
    """手动触发反思。用于演示与测试。"""
    from app.core.time import utcnow
    from app.domain.memory.reflection import maybe_reflect
    from app.schemas.memory import Memory as MemorySchema

    memories = await maybe_reflect(session, agent_id, world_time=utcnow(), force=True)
    return [MemorySchema.model_validate(m) for m in memories]


@router.post("/{agent_id}/decide")
async def force_agent_decide(
    agent_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """
    手动触发一次决策（阶段 15.6）。

    - 若 LLM 可用：调用 ``decide_with_llm`` 走完整管线，返回 tool_results。
    - 否则：返回当前的层次化计划上下文（daily / segment / current_task），供演示。
    """
    from app.core.time import utcnow
    from app.db.models import Agent
    from app.domain.planning import get_planning_service
    from app.llm.agent_decision import decide_with_llm
    from app.services.simulation_runtime import get_simulation_runtime

    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise TargetNotFound(f"agent {agent_id} not found")

    sim = get_simulation_runtime().engine.get_simulation()
    sim_id = sim.id if sim else "manual"
    world_time = sim.world_time if sim else utcnow()

    # 先刷新计划上下文
    plan_ctx = await get_planning_service().get_current_context(
        session, agent, world_time=world_time
    )
    await session.commit()

    results = await decide_with_llm(
        session,
        agent_id=agent_id,
        simulation_id=sim_id,
        world_time=world_time,
    )
    await session.commit()
    if results is None:
        return {
            "applied": False,
            "reason": "LLM not configured or returned None; rule fallback applies in world tick",
            "plan": plan_ctx,
            "tool_results": [],
        }
    return {
        "applied": True,
        "plan": plan_ctx,
        "tool_results": [r.model_dump() for r in results],
    }
