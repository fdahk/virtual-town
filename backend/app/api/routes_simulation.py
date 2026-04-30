"""仿真 REST 路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.simulation import (
    CreateSimulationRequest,
    SetSpeedRequest,
    Simulation,
    SimulationState,
    SimulationStateDelta,
    WorldEvent,
)
from app.services.simulation_runtime import get_simulation_runtime

router = APIRouter(prefix="/simulations", tags=["simulation"])


@router.get("/current", response_model=Simulation)
async def current_simulation(session: AsyncSession = Depends(get_session)) -> Simulation:
    return await get_simulation_runtime().get_current_simulation(session)


@router.post("", response_model=Simulation)
async def create_simulation(
    request: CreateSimulationRequest, session: AsyncSession = Depends(get_session)
) -> Simulation:
    return await get_simulation_runtime().create_simulation(session, request)


@router.get("/{simulation_id}/state", response_model=SimulationState)
async def get_state(
    simulation_id: str, session: AsyncSession = Depends(get_session)
) -> SimulationState:
    return await get_simulation_runtime().get_state(session, simulation_id)


@router.post("/{simulation_id}/start", response_model=Simulation)
async def start_simulation(simulation_id: str) -> Simulation:
    return await get_simulation_runtime().start_simulation(simulation_id)


@router.post("/{simulation_id}/pause", response_model=Simulation)
async def pause_simulation(simulation_id: str) -> Simulation:
    return await get_simulation_runtime().pause_simulation(simulation_id)


@router.post("/{simulation_id}/resume", response_model=Simulation)
async def resume_simulation(simulation_id: str) -> Simulation:
    return await get_simulation_runtime().resume_simulation(simulation_id)


@router.post("/{simulation_id}/step", response_model=SimulationStateDelta)
async def step_simulation(simulation_id: str) -> SimulationStateDelta:
    return await get_simulation_runtime().step_once(simulation_id)


@router.post("/{simulation_id}/speed", response_model=Simulation)
async def set_speed(simulation_id: str, request: SetSpeedRequest) -> Simulation:
    return await get_simulation_runtime().set_speed(simulation_id, request.speed_multiplier)


@router.get("/{simulation_id}/events", response_model=list[WorldEvent])
async def list_events(
    simulation_id: str,
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
) -> list[WorldEvent]:
    return await get_simulation_runtime().list_events(session, simulation_id, limit=limit)
