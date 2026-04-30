"""TaskQueue / Observer 基础单元测试（阶段 12 / 14.2）。

这些测试使用内存 sqlite 代替 Postgres，聚焦于：

- 任务表的插入、幂等 key 去重、状态变迁
- Observer 的批量缓冲与 flush
- EventRouter 的 world event → Observer 记录
"""

from __future__ import annotations

from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.base import Base
from app.db.models import (
    LLMCallRecord,
    ObservabilityEvent,
    Task,
    TaskStatusLog,
    ToolCallRecord,
)
from app.domain.tasks.queue import (
    PENDING,
    SUCCEEDED,
    TaskQueue,
    build_idempotency_key,
)
from app.domain.tasks.registry import TaskContext, TaskHandler, TaskResult, get_task_registry
from app.services.event_router import route_world_event
from app.services.observer import (
    CATEGORY_WORLD_EVENT,
    Observer,
    get_observer,
)


# sqlite 不支持 pgvector / JSONB，但对 tasks + 观测表足够。
# 需要把 JSONB / Vector 替换为更通用的类型：我们用一个简化的 metadata clone。


@pytest_asyncio.fixture
async def session_factory() -> async_sessionmaker:
    """使用 ``TEST_DATABASE_URL`` 指定一个**无其它消费者**的测试库。

    默认跳过，避免与正在运行的后端 worker 争抢任务导致假阳性。
    要跑这些测试，可在空库上执行：

    .. code-block:: bash

       createdb virtual_town_test
       TEST_DATABASE_URL_SYNC=postgresql+psycopg://.../virtual_town_test alembic upgrade head
       TEST_DATABASE_URL=postgresql+asyncpg://.../virtual_town_test pytest tests/test_tasks_and_observer.py
    """
    import os

    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip(
            "TEST_DATABASE_URL not set; skip tasks/observer integration tests "
            "(requires an isolated DB without other task consumers)"
        )

    engine = create_async_engine(url, future=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception:
        pytest.skip("postgres unavailable; skip tasks/observer integration tests")

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()


# -----------------------------------------------------------------------------
# handler
# -----------------------------------------------------------------------------


class EchoHandler(TaskHandler):
    task_type = "__test_echo__"
    default_timeout_seconds = 5.0
    default_max_retries = 1

    async def handle(self, ctx: TaskContext) -> TaskResult:
        return TaskResult(success=True, result={"echo": ctx.payload})


class FailOnceHandler(TaskHandler):
    task_type = "__test_fail_once__"
    default_timeout_seconds = 5.0
    default_max_retries = 1
    calls: list[int] = []

    async def handle(self, ctx: TaskContext) -> TaskResult:
        self.calls.append(ctx.retry_count)
        if ctx.retry_count == 0:
            return TaskResult(
                success=False,
                error_code="TOOL_EXECUTION_FAILED",
                error_message="fail first attempt",
                retryable=True,
            )
        return TaskResult(success=True, result={"ok": True})


@pytest.mark.asyncio
async def test_idempotency_key_builder() -> None:
    k1 = build_idempotency_key("agent_decision", entity_id="npc_a", simulation_step=10)
    k2 = build_idempotency_key("agent_decision", entity_id="npc_a", simulation_step=10)
    assert k1 == k2 == "agent_decision:npc_a:10"
    k3 = build_idempotency_key("agent_decision", entity_id="npc_a", simulation_step=11)
    assert k3 != k1


@pytest.mark.asyncio
async def test_task_enqueue_is_idempotent(session_factory) -> None:
    get_task_registry().register(EchoHandler())

    observer = Observer(flush_interval=0.1, batch_size=5)
    observer.bind_session_factory(session_factory)
    await observer.start()

    # 确保单例 Observer 被替换
    import app.services.observer as obs_mod

    old = obs_mod._observer
    obs_mod._observer = observer

    try:
        queue = TaskQueue(session_factory)
        t1 = await queue.enqueue(
            task_type="__test_echo__",
            payload={"x": 1},
            entity_id="npc_test_a",
            simulation_step=123,
        )
        t2 = await queue.enqueue(
            task_type="__test_echo__",
            payload={"x": 2},
            entity_id="npc_test_a",
            simulation_step=123,
        )
        assert t1.id == t2.id, "same idempotency key should yield same task row"
        assert t2.payload.get("x") == 1, "existing payload not overwritten by duplicate"
    finally:
        await observer.stop()
        obs_mod._observer = old


@pytest.mark.asyncio
async def test_task_lifecycle_run(session_factory, monkeypatch) -> None:
    """验证 pending → running → succeeded 生命周期与审计轨迹。

    新架构下 worker 是独立进程（``python -m app.domain.tasks.worker``），
    测试里不拉 RQ worker，而是直接调用 runner 的 ``_execute_task`` 模拟消费。
    """
    from app.db.session import _session_factory_singleton, get_session_factory  # noqa: F401
    import app.db.session as db_session_mod
    from app.domain.tasks import runner
    from app.domain.tasks.queue import init_task_queue

    reg = get_task_registry()
    reg.register(EchoHandler())

    observer = Observer(flush_interval=0.1, batch_size=5)
    observer.bind_session_factory(session_factory)
    await observer.start()
    import app.services.observer as obs_mod

    old = obs_mod._observer
    obs_mod._observer = observer

    # runner 内部通过 ``get_session_factory()`` 拿 factory；测试期间指向 test DB
    monkeypatch.setattr(db_session_mod, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(runner, "_initialized", True)  # 跳过 bootstrap（handlers 已手动注册）

    try:
        init_task_queue(session_factory)
        queue = TaskQueue(session_factory)
        # 避开真实 Redis：把 enqueue 的 RQ 推送短路成 no-op
        monkeypatch.setattr(queue, "_push_to_rq", lambda _task, _handler: None)
        task = await queue.enqueue(
            task_type="__test_echo__",
            payload={"msg": "hi"},
            entity_id="npc_test_b",
            simulation_step=777,
        )
        assert task.status == PENDING

        # 模拟 worker 消费一次
        await runner._execute_task(task.id)

        got = await queue.get_task(task.id)
        assert got is not None
        assert got.status == SUCCEEDED
        assert got.result == {"echo": {"msg": "hi"}}

        # 状态日志审计
        await observer._flush_now()
        async with session_factory() as session:
            logs = (
                await session.execute(
                    select(TaskStatusLog)
                    .where(TaskStatusLog.task_id == task.id)
                    .order_by(TaskStatusLog.id.asc())
                )
            ).scalars().all()
            statuses = [log.to_status for log in logs]
            assert "pending" in statuses
            assert "running" in statuses
            assert "succeeded" in statuses
    finally:
        await observer.stop()
        obs_mod._observer = old


@pytest.mark.asyncio
async def test_task_retry_then_succeed(session_factory, monkeypatch) -> None:
    """失败一次后 runner 应把任务重置为 pending、retry_count+1，并回推 RQ。"""
    import app.db.session as db_session_mod
    from app.domain.tasks import runner
    from app.domain.tasks.queue import init_task_queue

    reg = get_task_registry()
    handler = FailOnceHandler()
    handler.calls.clear()
    reg.register(handler)

    observer = Observer(flush_interval=0.1, batch_size=5)
    observer.bind_session_factory(session_factory)
    await observer.start()
    import app.services.observer as obs_mod

    old = obs_mod._observer
    obs_mod._observer = observer

    monkeypatch.setattr(db_session_mod, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(runner, "_initialized", True)

    try:
        init_task_queue(session_factory)
        queue = TaskQueue(session_factory)
        monkeypatch.setattr(queue, "_push_to_rq", lambda _task, _handler: None)
        # 也不真的推回 RQ：测试侧手动再跑一次
        async def _noop_retry(_task_id, delay_seconds=0.0):
            return None

        monkeypatch.setattr(queue, "enqueue_retry", _noop_retry)

        task = await queue.enqueue(
            task_type="__test_fail_once__",
            payload={},
            entity_id="npc_test_c",
            simulation_step=999,
        )

        # 第一次：失败、重置为 pending、retry_count=1
        await runner._execute_task(task.id)
        got = await queue.get_task(task.id)
        assert got is not None
        assert got.status == PENDING
        assert got.retry_count == 1

        # 第二次：成功
        await runner._execute_task(task.id)
        got = await queue.get_task(task.id)
        assert got is not None
        assert got.status == SUCCEEDED
        assert got.retry_count >= 1
        assert len(handler.calls) >= 2
    finally:
        await observer.stop()
        obs_mod._observer = old


@pytest.mark.asyncio
async def test_observer_event_router_records_world_event(session_factory) -> None:
    observer = Observer(flush_interval=0.1, batch_size=1)
    observer.bind_session_factory(session_factory)
    await observer.start()
    import app.services.observer as obs_mod

    old = obs_mod._observer
    obs_mod._observer = observer

    try:
        await route_world_event(
            {
                "id": "evt_test",
                "simulation_id": "sim_test_router",
                "event_type": "agent.moved",
                "source": "world",
                "actor_entity_id": "npc_a",
                "description": "test world event",
                "importance": 2,
                "payload": {"x": 1, "y": 2},
                "created_at": datetime.utcnow().isoformat(),
            }
        )
        # 等待一次 flush
        await observer._flush_now()

        async with session_factory() as session:
            stmt = (
                select(ObservabilityEvent)
                .where(
                    ObservabilityEvent.simulation_id == "sim_test_router",
                    ObservabilityEvent.category == CATEGORY_WORLD_EVENT,
                )
                .limit(5)
            )
            rows = (await session.execute(stmt)).scalars().all()
            assert rows, "world event should be recorded into observability_events"
            assert any(r.event_type == "agent.moved" for r in rows)
    finally:
        await observer.stop()
        obs_mod._observer = old
