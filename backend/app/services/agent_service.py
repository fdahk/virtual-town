"""AgentService：Agent 档案、运行态、关系、记忆。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import TargetNotFound
from app.core.time import utcnow
from app.db.models import Agent, AgentState, Memory, Relationship
from app.schemas.agent import (
    AgentAppearance,
    AgentProfile,
    AgentProfilePatch,
    AgentRuntimeState,
)
from app.schemas.agent import Relationship as RelationshipSchema
from app.schemas.memory import Memory as MemorySchema
from app.schemas.memory import MemoryScoreDetail, MemorySearchRequest, MemorySearchResult
from app.schemas.world import TilePosition


def profile_from_orm(agent: Agent) -> AgentProfile:
    appearance = agent.appearance or {}
    return AgentProfile(
        id=agent.id,
        entity_type=agent.entity_type,  # type: ignore[arg-type]
        name=agent.name,
        age=agent.age,
        gender=agent.gender,
        species=agent.species,
        appearance=AgentAppearance(**appearance) if isinstance(appearance, dict) else AgentAppearance(),
        occupation=agent.occupation,
        personality=agent.personality or [],
        background=agent.background or "",
        lifestyle=agent.lifestyle,
        long_term_goals=agent.long_term_goals or [],
        home_location_id=agent.home_location_id,
    )


def runtime_from_orm(state: AgentState) -> AgentRuntimeState:
    return AgentRuntimeState(
        agent_id=state.agent_id,
        scene_id=state.scene_id,
        position=TilePosition(x=state.x, y=state.y),
        state=state.state,
        emotion=state.emotion,
        energy=state.energy,
        hunger=state.hunger,
        social_need=state.social_need,
        fear=state.fear,
        status_effects=state.status_effects or [],
        current_action_id=state.current_action_id,
        current_goal=state.current_goal,
        facing=state.facing,
        updated_at=state.updated_at,
        busy_until=getattr(state, "busy_until", None),
        interruptible=getattr(state, "interruptible", True),
        current_priority=getattr(state, "current_priority", 0),
        last_social_at=getattr(state, "last_social_at", None),
    )


class AgentService:
    async def list_agents(self, session: AsyncSession) -> list[AgentProfile]:
        rows = (await session.execute(select(Agent).order_by(Agent.name))).scalars().all()
        return [profile_from_orm(r) for r in rows]

    async def get_profile(self, session: AsyncSession, agent_id: str) -> AgentProfile | None:
        row = await session.get(Agent, agent_id)
        return profile_from_orm(row) if row else None

    async def get_runtime_state(
        self, session: AsyncSession, agent_id: str
    ) -> AgentRuntimeState | None:
        row = await session.get(AgentState, agent_id)
        return runtime_from_orm(row) if row else None

    async def update_profile(
        self, session: AsyncSession, agent_id: str, patch: AgentProfilePatch
    ) -> AgentProfile:
        row = await session.get(Agent, agent_id)
        if row is None:
            raise TargetNotFound(f"agent {agent_id} not found")
        data: dict[str, Any] = patch.model_dump(exclude_unset=True)
        if "appearance" in data and data["appearance"] is not None:
            data["appearance"] = data["appearance"].model_dump()  # type: ignore[assignment]
        for key, value in data.items():
            setattr(row, key, value)
        await session.commit()
        await session.refresh(row)
        return profile_from_orm(row)

    async def list_relationships(
        self, session: AsyncSession, agent_id: str
    ) -> list[RelationshipSchema]:
        rows = (
            await session.execute(
                select(Relationship).where(Relationship.from_agent_id == agent_id)
            )
        ).scalars().all()
        return [RelationshipSchema.model_validate(r) for r in rows]

    async def list_memories(
        self, session: AsyncSession, agent_id: str, limit: int = 50
    ) -> list[MemorySchema]:
        rows = (
            await session.execute(
                select(Memory)
                .where(Memory.agent_id == agent_id)
                .order_by(desc(Memory.created_at))
                .limit(limit)
            )
        ).scalars().all()
        return [MemorySchema.model_validate(r) for r in rows]

    async def search_memories(
        self, session: AsyncSession, agent_id: str, request: MemorySearchRequest
    ) -> list[MemorySearchResult]:
        """
        MVP：先使用关键字 + 时近性 + 重要度 的简单评分。
        后续 Sprint 7 接入 pgvector 语义召回，该方法会调用 `MemoryService`。
        """
        from app.services.memory_service import get_memory_service

        service = get_memory_service()
        return await service.search(session, agent_id, request, now=utcnow())


_agent_service: AgentService | None = None


def get_agent_service() -> AgentService:
    global _agent_service
    if _agent_service is None:
        _agent_service = AgentService()
    return _agent_service
