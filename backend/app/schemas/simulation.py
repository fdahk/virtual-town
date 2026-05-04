"""仿真 schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.agent import AgentRuntimeState

# 仅三档（与前端 TimeControl 一致）。API 请求必须精确命中其一；导入 / 冷启动走 normalize。
SIMULATION_SPEED_TIERS: tuple[float, ...] = (0.5, 1.0, 2.0)
_SPEED_TIER_EPS = 1e-6


def normalize_simulation_speed_multiplier(v: float) -> float:
    """旧存档或导入 JSON：落到几何距离最近的一档。"""
    x = float(v)
    return min(SIMULATION_SPEED_TIERS, key=lambda t: abs(t - x))


def parse_strict_speed_multiplier(v: float) -> float:
    """POST set_speed / create_simulation：必须是三档之一。"""
    x = float(v)
    for t in SIMULATION_SPEED_TIERS:
        if abs(x - t) <= _SPEED_TIER_EPS:
            return t
    allowed = ", ".join(str(t) for t in SIMULATION_SPEED_TIERS)
    raise ValueError(f"speed_multiplier must be one of ({allowed}), got {v}")


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
    speed_multiplier: float

    @field_validator("speed_multiplier")
    @classmethod
    def _speed_must_be_tier(cls, v: float) -> float:
        return parse_strict_speed_multiplier(v)


class CreateSimulationRequest(BaseModel):
    world_tick_hz: float = 2.0
    ai_tick_minutes: int = 5
    speed_multiplier: float = Field(default=1.0)

    @field_validator("speed_multiplier")
    @classmethod
    def _speed_must_be_tier(cls, v: float) -> float:
        return parse_strict_speed_multiplier(v)
