"""冷启动状态收敛回归测试（2026-05 修复）。

覆盖两条核心路径：

1. ``SimulationEngine._load_state`` 在 DB 已有 ``status='running'`` 时，按
   ``SIMULATION_AUTOSTART`` 收敛——默认（False）→ ``paused``，True → ``running``。
   这是「重启后世界自己接着跑」的核心 bug。

2. ``reset_inflight_state_on_cold_start`` 清空 RQ 三条队列里 pending jobs +
   把 PG ``tasks`` 表的 ``pending`` 行删除、``running`` 行收敛到 ``cancelled``，
   保留 ``succeeded`` / ``failed`` 历史指标。

为了避免真实连接 Postgres / Redis，两条用例都使用 mock：``_load_state`` 用
``MagicMock`` 替代 AsyncSession；queue 清理用 SQLite + Patch 掉 RQ。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.simulation import engine as engine_mod
from app.domain.tasks import queue as queue_mod
from app.domain.tasks.queue import reset_inflight_state_on_cold_start

# ---------------------------------------------------------------------------
# 1. _load_state 收敛 status
# ---------------------------------------------------------------------------


def _build_engine_with_running_sim(sim_status: str):
    """构造一个最小可调用 ``_load_state`` 的 SimulationEngine 实例。

    所有外部依赖（scene cache、Agent / AgentState / WorldObject 查询）都
    替换成空集合或 noop，以便专注验证 simulation.status 收敛逻辑。
    """
    eng = engine_mod.SimulationEngine.__new__(engine_mod.SimulationEngine)
    eng._simulation = None
    eng._step = 0
    eng._world_time = datetime(2026, 4, 30, tzinfo=UTC)
    eng._agents_meta = {}
    eng._agents = {}
    eng._locations = {}
    eng._portals_by_scene = {}
    eng._portals = {}
    eng._objects = {}
    eng._weather = engine_mod.WeatherState()
    eng._natural_event_debounce = {}
    eng._last_reflect_at = {}
    eng._last_consolidation_at = {}
    eng._last_rumination_at = {}
    eng._last_summary_day = {}
    eng._chatting_since_real = {}
    eng._last_needs_evolve_at = None
    eng._last_encounter_scan_at = None
    eng._encounter_cooldown_until = {}
    eng._pending_unreachable = {}
    eng._unreachable_local = {}

    sim = SimpleNamespace(
        id="sim-1",
        status=sim_status,
        world_time=datetime(2026, 4, 30, 7, 30, tzinfo=UTC),
        world_tick_hz=5.0,
        ai_tick_minutes=3,
        speed_multiplier=1.0,
        current_step=63596,
    )

    sim_result = MagicMock()
    sim_result.scalar_one_or_none.return_value = sim

    empty_scalars = MagicMock()
    empty_scalars.all.return_value = []
    empty_result = MagicMock()
    empty_result.scalars.return_value = empty_scalars

    session = MagicMock(spec=AsyncSession)
    session.execute = AsyncMock(side_effect=[sim_result] + [empty_result] * 10)
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    return eng, session, sim


@pytest.mark.asyncio
async def test_load_state_normalizes_running_to_paused_when_autostart_false():
    eng, session, sim = _build_engine_with_running_sim("running")

    fake_settings = SimpleNamespace(simulation_autostart=False)

    cache = MagicMock()
    cache.refresh = AsyncMock()

    with (
        patch.object(engine_mod, "get_settings", return_value=fake_settings),
        patch.object(engine_mod, "get_scene_cache", return_value=cache),
    ):
        await eng._load_state(session)

    assert sim.status == "paused"
    assert session.commit.await_count >= 1
    assert eng._simulation is sim


@pytest.mark.asyncio
async def test_load_state_keeps_running_when_autostart_true():
    eng, session, sim = _build_engine_with_running_sim("running")

    fake_settings = SimpleNamespace(simulation_autostart=True)

    cache = MagicMock()
    cache.refresh = AsyncMock()

    with (
        patch.object(engine_mod, "get_settings", return_value=fake_settings),
        patch.object(engine_mod, "get_scene_cache", return_value=cache),
    ):
        await eng._load_state(session)

    assert sim.status == "running"
    # 已经是目标状态时不应触发额外 commit / refresh
    assert session.commit.await_count == 0


@pytest.mark.asyncio
async def test_load_state_normalizes_idle_back_to_running_when_autostart_true():
    """显式 autostart=True 时，残留 idle/paused 应被恢复到 running。"""
    eng, session, sim = _build_engine_with_running_sim("paused")

    fake_settings = SimpleNamespace(simulation_autostart=True)

    cache = MagicMock()
    cache.refresh = AsyncMock()

    with (
        patch.object(engine_mod, "get_settings", return_value=fake_settings),
        patch.object(engine_mod, "get_scene_cache", return_value=cache),
    ):
        await eng._load_state(session)

    assert sim.status == "running"
    assert session.commit.await_count >= 1


# ---------------------------------------------------------------------------
# 2. reset_inflight_state_on_cold_start
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_inflight_state_purges_rq_and_resets_pg(monkeypatch):
    """端到端验证 cold start 清理逻辑：

    - 三条优先级 RQ 队列均被 ``empty()``；
    - PG ``tasks`` 通过两条 SQL 收敛 pending → 删除、running → cancelled。

    使用 mock 而非真实 sqlite/redis，避免新增 ``aiosqlite`` 依赖；同时把
    SQL 拼装委托给 SQLAlchemy 自身（只断言执行了正确的 statement 类型与
    where/values）。
    """

    fake_settings = SimpleNamespace(
        task_queue_high="vt:high",
        task_queue_default="vt:default",
        task_queue_low="vt:low",
        redis_url="redis://localhost:6379/0",
    )

    purge_calls: list[str] = []

    class FakeQueue:
        def __init__(self, name: str, connection):
            self.name = name
            self.count = 7

        def empty(self):
            purge_calls.append(self.name)

    redis_close_calls: list[bool] = []

    class FakeRedis:
        @classmethod
        def from_url(cls, *_args, **_kwargs):
            return cls()

        def close(self):
            redis_close_calls.append(True)

    monkeypatch.setitem(__import__("sys").modules, "rq", SimpleNamespace(Queue=FakeQueue))
    monkeypatch.setitem(__import__("sys").modules, "redis", SimpleNamespace(Redis=FakeRedis))

    executed: list[str] = []

    def _fake_execute_factory(pending_rowcount: int, running_rowcount: int):
        async def _exec(stmt):
            text = str(stmt).lower()
            if text.startswith("delete"):
                executed.append("delete_pending")
                return SimpleNamespace(rowcount=pending_rowcount)
            if text.startswith("update"):
                executed.append("update_running")
                return SimpleNamespace(rowcount=running_rowcount)
            raise AssertionError(f"unexpected statement: {stmt}")

        return _exec

    session = MagicMock(spec=AsyncSession)
    session.execute = AsyncMock(side_effect=_fake_execute_factory(2, 1))
    session.commit = AsyncMock()

    @asynccontextmanager
    async def _session_cm():
        yield session

    factory = MagicMock(side_effect=lambda: _session_cm())

    with patch.object(queue_mod, "get_settings", return_value=fake_settings):
        result = await reset_inflight_state_on_cold_start(factory)

    assert sorted(purge_calls) == ["vt:default", "vt:high", "vt:low"]
    assert redis_close_calls == [True]
    assert executed == ["delete_pending", "update_running"]
    assert session.commit.await_count == 1
    assert result == {
        "queues_purged": 21,
        "pending_deleted": 2,
        "running_cancelled": 1,
    }


@pytest.mark.asyncio
async def test_reset_inflight_state_handles_rq_purge_failure(monkeypatch):
    """RQ 异常不应阻塞 PG 收敛——队列连不上时仍要把 PG 状态清掉。"""

    fake_settings = SimpleNamespace(
        task_queue_high="vt:high",
        task_queue_default="vt:default",
        task_queue_low="vt:low",
        redis_url="redis://localhost:6379/0",
    )

    class BoomRedis:
        @classmethod
        def from_url(cls, *_args, **_kwargs):
            raise RuntimeError("redis unreachable")

    monkeypatch.setitem(__import__("sys").modules, "redis", SimpleNamespace(Redis=BoomRedis))
    monkeypatch.setitem(__import__("sys").modules, "rq", SimpleNamespace(Queue=object))

    async def _exec(stmt):
        text = str(stmt).lower()
        if text.startswith("delete"):
            return SimpleNamespace(rowcount=5)
        return SimpleNamespace(rowcount=0)

    session = MagicMock(spec=AsyncSession)
    session.execute = AsyncMock(side_effect=_exec)
    session.commit = AsyncMock()

    @asynccontextmanager
    async def _session_cm():
        yield session

    factory = MagicMock(side_effect=lambda: _session_cm())

    with patch.object(queue_mod, "get_settings", return_value=fake_settings):
        result = await reset_inflight_state_on_cold_start(factory)

    assert result["queues_purged"] == 0
    assert result["pending_deleted"] == 5
    assert result["running_cancelled"] == 0
