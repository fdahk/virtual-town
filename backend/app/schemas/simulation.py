"""仿真 schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.agent import AgentRuntimeState


class Simulation(BaseModel):
    id: str
    status: Literal["idle", "running", "paused", "stopped"]
    world_time: datetime
    world_tick_hz: float
    ai_tick_minutes: int
    speed_multiplier: float
    current_step: int

    class Config:
        from_attributes = True


class SimulationState(BaseModel):
    simulation: Simulation
    entities: list[AgentRuntimeState] = Field(default_factory=list)


class WorldEvent(BaseModel):
    id: str
    simulation_id: str
    event_type: str
    source: str
    actor_entity_id: str | None = None
    target_entity_id: str | None = None
    location_id: str | None = None
    scene_id: str | None = None
    description: str
    importance: int = 1
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    class Config:
        from_attributes = True


class AgentActionRead(BaseModel):
    id: str
    agent_id: str
    action_type: str
    description: str
    target_location_id: str | None = None
    target_entity_id: str | None = None
    target_object_id: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    status: str

    class Config:
        from_attributes = True


class SimulationDeltaPayload(BaseModel):
    step: int
    world_time: datetime
    # seq 单调递增；前端可据此检测丢失与乱序（§14.5 WebSocket 健壮性）
    seq: int | None = None
    entity_updates: list[AgentRuntimeState] = Field(default_factory=list)
    events: list[WorldEvent] = Field(default_factory=list)


class SimulationStateDelta(BaseModel):
    simulation: Simulation
    delta: SimulationDeltaPayload


class SetSpeedRequest(BaseModel):
    speed_multiplier: float = Field(..., ge=0.1, le=10.0)


class CreateSimulationRequest(BaseModel):
    world_tick_hz: float = 5.0
    ai_tick_minutes: int = 5
    speed_multiplier: float = 1.0
