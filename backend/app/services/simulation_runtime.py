"""
SimulationRuntime：包装 SimulationEngine，暴露给 API 层。

- 单例，启动时创建唯一 SimulationEngine。
- 提供 start/pause/resume/step/speed 控制。
- 对外返回 Pydantic schema。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import TargetNotFound
from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import Simulation as SimulationORM
from app.db.models import WorldEvent as WorldEventORM
from app.db.session import get_session_factory
from app.domain.simulation.engine import PlayerInputEvent, SimulationEngine
from app.schemas.agent import AgentRuntimeState
from app.schemas.simulation import (
    CreateSimulationRequest,
    Simulation,
    SimulationDeltaPayload,
    SimulationState,
    SimulationStateDelta,
    WorldEvent as WorldEventSchema,
    normalize_simulation_speed_multiplier,
)

logger = get_logger(__name__)


class SimulationRuntime:
    def __init__(self) -> None:
        self._engine: SimulationEngine | None = None

    async def start(self) -> None:
        if self._engine is None:
            self._engine = SimulationEngine(get_session_factory())
        await self._engine.start()

    async def stop(self) -> None:
        if self._engine is not None:
            await self._engine.stop()

    @property
    def engine(self) -> SimulationEngine:
        if self._engine is None:
            raise RuntimeError("simulation engine not started")
        return self._engine

    async def reload(self) -> None:
        """Seed 等数据变更后调用，重新加载内存状态。"""
        if self._engine is not None:
            await self._engine.reload_from_db()

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    async def get_current_simulation(self, session: AsyncSession) -> Simulation:
        sim = self.engine.get_simulation()
        if sim is None:
            raise TargetNotFound("no active simulation")
        return Simulation.model_validate(sim)

    async def get_state(self, session: AsyncSession, simulation_id: str) -> SimulationState:
        sim = self.engine.get_simulation()
        if sim is None or sim.id != simulation_id:
            raise TargetNotFound(f"simulation {simulation_id} not found")
        _, entities = self.engine.snapshot()
        return SimulationState(
            simulation=Simulation.model_validate(sim),
            entities=entities,
        )

    async def list_events(
        self, session: AsyncSession, simulation_id: str, *, limit: int = 100
    ) -> list[WorldEventSchema]:
        stmt = (
            select(WorldEventORM)
            .where(WorldEventORM.simulation_id == simulation_id)
            .order_by(desc(WorldEventORM.created_at))
            .limit(limit)
        )
        rows = (await session.execute(stmt)).scalars().all()
        return [WorldEventSchema.model_validate(r) for r in rows]

    # ------------------------------------------------------------------
    # 控制
    # ------------------------------------------------------------------

    async def create_simulation(
        self, session: AsyncSession, request: CreateSimulationRequest
    ) -> Simulation:
        sim = self.engine.get_simulation()
        if sim is None:
            raise TargetNotFound("no simulation available")
        db_sim = await session.get(SimulationORM, sim.id)
        if db_sim is None:
            raise TargetNotFound("simulation row missing")
        db_sim.world_tick_hz = request.world_tick_hz
        db_sim.ai_tick_minutes = request.ai_tick_minutes
        db_sim.speed_multiplier = normalize_simulation_speed_multiplier(
            request.speed_multiplier
        )
        db_sim.status = "running"
        await session.commit()
        self.engine.set_speed(db_sim.speed_multiplier)
        self.engine.set_status("running")
        return Simulation.model_validate(db_sim)

    async def start_simulation(self, simulation_id: str) -> Simulation:
        return await self._set_status(simulation_id, "running")

    async def pause_simulation(self, simulation_id: str) -> Simulation:
        return await self._set_status(simulation_id, "paused")

    async def resume_simulation(self, simulation_id: str) -> Simulation:
        return await self._set_status(simulation_id, "running")

    async def set_speed(self, simulation_id: str, multiplier: float) -> Simulation:
        sim = self.engine.get_simulation()
        if sim is None or sim.id != simulation_id:
            raise TargetNotFound(f"simulation {simulation_id} not found")
        async with get_session_factory()() as session:
            db_sim = await session.get(SimulationORM, sim.id)
            if db_sim is None:
                raise TargetNotFound("simulation row missing")
            clamped = normalize_simulation_speed_multiplier(multiplier)
            db_sim.speed_multiplier = clamped
            await session.commit()
            self.engine.set_speed(clamped)
            return Simulation.model_validate(db_sim)

    async def step_once(self, simulation_id: str) -> SimulationStateDelta:
        sim = self.engine.get_simulation()
        if sim is None or sim.id != simulation_id:
            raise TargetNotFound(f"simulation {simulation_id} not found")
        # 临时切到 running 执行一次 tick 后恢复原状态
        prev = sim.status
        self.engine.set_status("running")
        await self.engine._tick()  # type: ignore[attr-defined]
        self.engine.set_status(prev if prev != "running" else "running")
        _, entities = self.engine.snapshot()
        delta = SimulationDeltaPayload(
            step=sim.current_step,
            world_time=sim.world_time,
            entity_updates=entities,
            events=[],
        )
        return SimulationStateDelta(
            simulation=Simulation.model_validate(sim),
            delta=delta,
        )

    async def _set_status(self, simulation_id: str, status: str) -> Simulation:
        sim = self.engine.get_simulation()
        if sim is None or sim.id != simulation_id:
            raise TargetNotFound(f"simulation {simulation_id} not found")
        async with get_session_factory()() as session:
            db_sim = await session.get(SimulationORM, sim.id)
            if db_sim is None:
                raise TargetNotFound("simulation row missing")
            db_sim.status = status
            await session.commit()
            self.engine.set_status(status)
            return Simulation.model_validate(db_sim)

    # ------------------------------------------------------------------
    # 玩家输入
    # ------------------------------------------------------------------

    def enqueue_player_input(self, evt: PlayerInputEvent) -> None:
        self.engine.enqueue_input(evt)


_runtime: SimulationRuntime | None = None


def get_simulation_runtime() -> SimulationRuntime:
    global _runtime
    if _runtime is None:
        _runtime = SimulationRuntime()
    return _runtime
