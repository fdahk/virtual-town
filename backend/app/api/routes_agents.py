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
