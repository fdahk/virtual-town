"""
Task runner（阶段 12）：RQ worker 进程里执行单个任务的入口。

RQ 调用的是同步函数（``run_task_job(task_id)``）——因为 RQ 协议按
``import_path(args, kwargs)`` 序列化到 Redis，再在 worker 进程里反序列化调用。
我们在这个同步入口内部用 ``asyncio.run`` 起一个临时 event loop，
调用 async handler，完成后关闭 loop。这样可以继续复用现有的
AsyncSession / Observer / LLM client（它们都是 asyncio 的）。

处理流程：

1. 加载 ``tasks`` 表中对应行，若已经处于终态直接返回。
2. 从 payload 恢复 ``TraceContext``。
3. 将状态置为 ``running``，审计一次状态变迁。
4. 调用 ``TaskHandler.handle``，带 ``default_timeout_seconds`` 超时。
5. 结果落表：

   - 成功 → ``succeeded``
   - 失败 + retryable + retry_count < max_retries → 重置为 ``pending`` 并
     通过 ``TaskQueue.enqueue_retry`` 再次 push 到 RQ（指数退避）
   - 其它失败 → ``failed`` / ``timeout``

Handler 只需返回 ``TaskResult``；不需要感知 RQ / Redis。
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from sqlalchemy import update

from app.core.logging import get_logger
from app.core.time import utcnow
from app.core.trace_context import TraceContext, TraceSnapshot
from app.db.models import Task
from app.domain.tasks.queue import (
    FAILED,
    PENDING,
    RUNNING,
    SUCCEEDED,
    TIMEOUT,
)
from app.domain.tasks.registry import (
    TaskContext,
    TaskResult,
    get_task_registry,
)
from app.services.observer import get_observer

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Worker 进程级懒初始化
# ---------------------------------------------------------------------------


_initialized: bool = False

# 持久的 asyncio event loop（跑在独立线程里）。
# 关键设计：SQLAlchemy AsyncEngine / asyncpg Future 会绑定在创建它的 loop 上；
# 如果每个 RQ job 都 ``asyncio.run`` 造一个新 loop，跨 loop 的 Future 会抛
# ``got Future attached to a different loop``。所以 worker 进程里用**同一个
# 长驻 loop**，所有 job 通过 ``run_coroutine_threadsafe`` 投递进来。
_LOOP: asyncio.AbstractEventLoop | None = None
_LOOP_THREAD: threading.Thread | None = None
_LOOP_READY = threading.Event()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    """启动（幂等）worker 进程的持久 event loop。"""
    global _LOOP, _LOOP_THREAD
    if _LOOP is not None and _LOOP.is_running():
        return _LOOP

    def _run() -> None:
        global _LOOP
        loop = asyncio.new_event_loop()
        _LOOP = loop
        asyncio.set_event_loop(loop)
        _LOOP_READY.set()
        try:
            loop.run_forever()
        finally:
            loop.close()

    _LOOP_READY.clear()
    _LOOP_THREAD = threading.Thread(target=_run, name="vt-worker-loop", daemon=True)
    _LOOP_THREAD.start()
    _LOOP_READY.wait(timeout=5)
    assert _LOOP is not None
    return _LOOP


def _ensure_worker_bootstrap() -> None:
    """RQ worker 进程首次调用 job 时做一次性初始化。

    在 FastAPI 主进程里 handlers/observer 已经在 lifespan 中注册好了；
    但 RQ worker 是独立进程，需要自己再走一遍：

    - register_default_handlers
    - Observer.bind_session_factory + start（在持久 loop 里）
    - EventRouter 订阅
    - init_task_queue（runner 内部重试路径用得到）
    """
    global _initialized
    if _initialized:
        return

    from app.core.logging import setup_logging
    from app.db.session import get_session_factory
    from app.domain.tasks.handlers import register_default_handlers
    from app.domain.tasks.queue import init_task_queue
    from app.services.event_router import subscribe_event_router

    setup_logging()
    register_default_handlers()
    subscribe_event_router()

    session_factory = get_session_factory()
    init_task_queue(session_factory)

    observer = get_observer()
    observer.bind_session_factory(session_factory)

    # 在持久 loop 里启动 observer 的 flush 循环
    loop = _ensure_loop()

    async def _start_observer() -> None:
        if not observer.is_running():
            await observer.start()

    fut = asyncio.run_coroutine_threadsafe(_start_observer(), loop)
    fut.result(timeout=5)

    _initialized = True


# ---------------------------------------------------------------------------
# 同步入口（RQ 调用）
# ---------------------------------------------------------------------------


def run_task_job(task_id: str) -> dict[str, Any]:
    """RQ worker 调用的同步入口。必须是模块级可 import 的函数。

    投递协程到 worker 的持久 loop 执行，阻塞等待结果返回。
    """
    _ensure_worker_bootstrap()
    loop = _ensure_loop()
    future = asyncio.run_coroutine_threadsafe(_execute_task(task_id), loop)
    try:
        return future.result()
    except Exception:
        logger.exception("run_task_job crashed task_id=%s", task_id)
        raise


# ---------------------------------------------------------------------------
# 异步执行体
# ---------------------------------------------------------------------------


async def _execute_task(task_id: str) -> dict[str, Any]:
    from app.db.session import get_session_factory
    from app.domain.tasks.queue import get_task_queue

    session_factory = get_session_factory()
    registry = get_task_registry()

    async with session_factory() as session:
        task = await session.get(Task, task_id)

    if task is None:
        logger.warning("task_id=%s not found (may have been purged)", task_id)
        return {"status": "missing"}

    if task.status in {SUCCEEDED, FAILED, TIMEOUT}:
        # 已终态：可能是重复投递/幂等 short-circuit
        return {"status": task.status, "idempotent": True}

    # deadline 检查：任务在 RQ 队列等待期间若已超过 deadline_at，直接丢弃
    # （不执行，标记 failed，避免执行过期的 agent_decision 等时效性任务）
    now_ts = utcnow()
    if task.deadline_at is not None and task.deadline_at.tzinfo is None:
        from datetime import timezone
        task_deadline = task.deadline_at.replace(tzinfo=timezone.utc)
    else:
        task_deadline = task.deadline_at

    if task_deadline is not None and now_ts > task_deadline:
        waited_secs = (now_ts - task.created_at).total_seconds() if task.created_at else 0
        logger.warning(
            "task_id=%s type=%s DEADLINE_EXCEEDED waited=%.1fs deadline=%s",
            task.id,
            task.task_type,
            waited_secs,
            task_deadline.isoformat(),
        )
        async with get_session_factory()() as _sess:
            await _sess.execute(
                update(Task)
                .where(Task.id == task.id)
                .values(
                    status=FAILED,
                    finished_at=now_ts,
                    last_error=f"TASK_DEADLINE_EXCEEDED: waited {waited_secs:.1f}s",
                )
            )
            await _sess.commit()
        await get_observer().record_task_status(
            task_id=task.id,
            from_status=PENDING,
            to_status=FAILED,
            retry_count=task.retry_count,
            message=f"deadline exceeded after {waited_secs:.1f}s in queue",
        )
        return {"status": FAILED, "error": "TASK_DEADLINE_EXCEEDED"}

    handler = registry.get(task.task_type)
    if handler is None:
        await _mark_failed(
            task_id=task.id,
            retry_count=task.retry_count,
            error_code="TASK_HANDLER_MISSING",
            message=f"no handler for {task.task_type}",
        )
        return {"status": FAILED, "error": "TASK_HANDLER_MISSING"}

    trace_payload = task.payload.get("__trace__") if isinstance(task.payload, dict) else {}
    trace_payload = trace_payload or {}
    snapshot = TraceSnapshot(
        request_id=trace_payload.get("request_id"),
        trace_id=trace_payload.get("trace_id") or task.trace_id,
        span_id=trace_payload.get("span_id"),
        parent_span_id=trace_payload.get("parent_span_id"),
        parent_trace_id=trace_payload.get("parent_trace_id"),  # 链回触发方 trace
        simulation_id=trace_payload.get("simulation_id") or task.simulation_id,
        agent_id=trace_payload.get("agent_id") or task.entity_id,
        player_id=trace_payload.get("player_id"),
        task_id=task.id,
        category=trace_payload.get("category") or f"task.{task.task_type}",
    )

    # PENDING → RUNNING
    async with session_factory() as session:
        await session.execute(
            update(Task)
            .where(Task.id == task.id)
            .values(status=RUNNING, started_at=utcnow())
        )
        await session.commit()
    await get_observer().record_task_status(
        task_id=task.id,
        from_status=PENDING,
        to_status=RUNNING,
        retry_count=task.retry_count,
    )

    timeout = float(handler.default_timeout_seconds)
    started_wall = utcnow()

    result_payload: dict[str, Any] = {}
    final_status: str
    error_code: str | None = None
    error_message: str | None = None
    success = False
    retryable = False

    with TraceContext.apply(snapshot):
        try:
            async with session_factory() as session:
                ctx = TaskContext(
                    task_id=task.id,
                    task_type=task.task_type,
                    payload={
                        k: v for k, v in (task.payload or {}).items() if k != "__trace__"
                    },
                    session=session,
                    retry_count=task.retry_count,
                    max_retries=task.max_retries,
                    enqueued_at=task.enqueued_at,
                    simulation_id=task.simulation_id,
                    entity_id=task.entity_id,
                    simulation_step=task.simulation_step,
                    trace_id=task.trace_id,
                )
                try:
                    result: TaskResult = await asyncio.wait_for(
                        handler.handle(ctx), timeout=timeout
                    )
                except asyncio.TimeoutError:
                    await session.rollback()
                    error_code = "TASK_TIMEOUT"
                    error_message = f"timeout after {timeout}s"
                    retryable = True
                    success = False
                    result = TaskResult(success=False, retryable=True)
                else:
                    try:
                        await session.commit()
                    except Exception:
                        await session.rollback()
                    success = result.success
                    if not success:
                        error_code = result.error_code or "TASK_FAILED"
                        error_message = result.error_message or result.fallback_hint
                        retryable = result.retryable
                    result_payload = result.result or {}
        except Exception as exc:
            logger.exception("task %s (%s) crashed", task.id, task.task_type)
            success = False
            error_code = "TASK_EXECUTION_ERROR"
            error_message = str(exc) or type(exc).__name__
            retryable = True

    duration_ms = int((utcnow() - started_wall).total_seconds() * 1000)

    if success:
        await _mark_succeeded(
            task_id=task.id,
            retry_count=task.retry_count,
            result=result_payload,
            duration_ms=duration_ms,
        )
        final_status = SUCCEEDED
    else:
        next_retry = task.retry_count + 1
        can_retry = retryable and next_retry <= task.max_retries
        if can_retry:
            # 回到 pending，推回 RQ（指数退避）
            delay = min(30.0, 2.0 ** task.retry_count)
            await _requeue_for_retry(
                task_id=task.id,
                next_retry_count=next_retry,
                error_code=error_code or "TASK_FAILED",
                message=error_message,
            )
            queue = get_task_queue()
            if queue is not None:
                try:
                    await queue.enqueue_retry(task.id, delay_seconds=delay)
                except Exception:
                    logger.exception("enqueue_retry failed task_id=%s", task.id)
            final_status = PENDING
        else:
            final_status = TIMEOUT if error_code == "TASK_TIMEOUT" else FAILED
            await _mark_failed(
                task_id=task.id,
                retry_count=task.retry_count,
                error_code=error_code or "TASK_FAILED",
                message=error_message,
                result=result_payload or None,
                status=final_status,
            )

    # flush observer（worker 进程每个 job 独立 loop；不 flush 会丢批次）
    try:
        await get_observer()._flush_now()
    except Exception:
        pass

    return {
        "status": final_status,
        "task_id": task.id,
        "duration_ms": duration_ms,
        "error_code": error_code,
        "error_message": error_message,
    }


async def _mark_succeeded(
    *,
    task_id: str,
    retry_count: int,
    result: dict[str, Any],
    duration_ms: int,
) -> None:
    from app.db.session import get_session_factory

    async with get_session_factory()() as session:
        await session.execute(
            update(Task)
            .where(Task.id == task_id)
            .values(
                status=SUCCEEDED,
                finished_at=utcnow(),
                result=result,
                last_error=None,
            )
        )
        await session.commit()
    await get_observer().record_task_status(
        task_id=task_id,
        from_status=RUNNING,
        to_status=SUCCEEDED,
        retry_count=retry_count,
        message=f"duration_ms={duration_ms}",
    )


async def _mark_failed(
    *,
    task_id: str,
    retry_count: int,
    error_code: str,
    message: str | None,
    result: dict[str, Any] | None = None,
    status: str = FAILED,
) -> None:
    from app.db.session import get_session_factory

    async with get_session_factory()() as session:
        values: dict[str, Any] = {
            "status": status,
            "finished_at": utcnow(),
            "last_error": f"{error_code}: {message}" if message else error_code,
        }
        if result is not None:
            values["result"] = result
        await session.execute(update(Task).where(Task.id == task_id).values(**values))
        await session.commit()
    await get_observer().record_task_status(
        task_id=task_id,
        from_status=RUNNING,
        to_status=status,
        retry_count=retry_count,
        message=message,
    )


async def _requeue_for_retry(
    *,
    task_id: str,
    next_retry_count: int,
    error_code: str,
    message: str | None,
) -> None:
    """重试路径：重置 tasks 行为 pending，retry_count +1。"""
    from app.db.session import get_session_factory

    async with get_session_factory()() as session:
        await session.execute(
            update(Task)
            .where(Task.id == task_id)
            .values(
                status=PENDING,
                started_at=None,
                retry_count=next_retry_count,
                last_error=f"{error_code}: {message}" if message else error_code,
            )
        )
        await session.commit()
    await get_observer().record_task_status(
        task_id=task_id,
        from_status=RUNNING,
        to_status=PENDING,
        retry_count=next_retry_count,
        message=f"retry:{error_code}",
    )


__all__ = ["run_task_job"]
