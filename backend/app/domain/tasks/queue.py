"""
TaskQueue：基于 ``Redis + RQ`` 的任务队列（阶段 12）。

设计决策（与 ``docs/实施方案/MVP开发任务拆分.md §12``、
``docs/实施方案/事件系统与任务调度模块实施方案.md §2`` 对齐）：

- **队列层**：Redis + `rq <https://python-rq.org/>`_。MVP 选型 RQ，复杂度低；
  后续复杂调度可替换为 Celery，但本模块的 ``enqueue`` 接口保持不变。
- **状态表**：``tasks``（与 ``task_status_log``）保留为**权威状态源**与审计轨迹。
  RQ 仅负责"把 task_id 从 FastAPI 进程送到 worker 进程"；所有业务状态
  （pending / running / succeeded / failed / timeout）都在 ``tasks`` 表上流转。
- **幂等**：``idempotency_key = {task_type}:{entity_id}:{simulation_step}``
  在 ``tasks`` 表上加唯一约束保障。重复入队会返回既有行，不会产生重复 RQ job。
- **重试**：由 worker 侧在失败后重新 enqueue 到 RQ，并递增 ``retry_count``；
  超过 ``max_retries`` 标记为 ``failed`` / ``timeout``，由业务层接管兜底。
- **Trace 透传**：enqueue 时把 ``TraceContext`` 快照打包到 payload，
  worker 在执行前用 ``TraceContext.apply`` 恢复，保证一条 trace 贯穿两个进程。

Worker 入口：``app.domain.tasks.worker`` / ``app.domain.tasks.runner.run_task_job``。
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.time import utcnow
from app.core.trace_context import current as current_trace
from app.db.models import Task
from app.domain.tasks.registry import (
    TaskRegistry,
    get_task_registry,
)
from app.services.observer import get_observer

logger = get_logger(__name__)


PENDING = "pending"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
TIMEOUT = "timeout"
CANCELLED = "cancelled"


# 优先级 → RQ 队列名映射（数字越小越紧急）
_PRIORITY_HIGH_MAX = 3
_PRIORITY_LOW_MIN = 7


def build_idempotency_key(
    task_type: str,
    *,
    entity_id: str | None,
    simulation_step: int | None,
    extra: str | None = None,
) -> str:
    parts = [
        task_type,
        entity_id or "-",
        str(simulation_step) if simulation_step is not None else "-",
    ]
    if extra:
        parts.append(extra)
    return ":".join(parts)


def _queue_name_for_priority(priority: int) -> str:
    settings = get_settings()
    if priority <= _PRIORITY_HIGH_MAX:
        return settings.task_queue_high
    if priority >= _PRIORITY_LOW_MIN:
        return settings.task_queue_low
    return settings.task_queue_default


class TaskQueue:
    """入队接口 + 状态查询。

    Worker 侧的消费逻辑见 ``app.domain.tasks.runner`` 和
    ``app.domain.tasks.worker``。
    """

    # 由 worker 侧（app.domain.tasks.runner）在失败重试时回调，避免绕回模块顶层。
    RUNNER_IMPORT_PATH = "app.domain.tasks.runner.run_task_job"

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory
        self._registry: TaskRegistry = get_task_registry()
        self._rq_queues: dict[str, Any] = {}
        self._redis = None  # 同步 Redis client，lazy 初始化（rq 只支持同步）

    # ------------------------------------------------------------------
    # Redis / RQ 懒加载
    # ------------------------------------------------------------------

    def _ensure_redis(self):
        if self._redis is not None:
            return self._redis
        try:
            from redis import Redis  # sync client
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("redis package missing") from exc
        settings = get_settings()
        self._redis = Redis.from_url(settings.redis_url, decode_responses=False)
        return self._redis

    def _get_rq_queue(self, queue_name: str):
        if queue_name in self._rq_queues:
            return self._rq_queues[queue_name]
        try:
            from rq import Queue
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "rq package missing; add `rq>=1.16` to backend dependencies"
            ) from exc
        redis = self._ensure_redis()
        q = Queue(queue_name, connection=redis)
        self._rq_queues[queue_name] = q
        return q

    # ------------------------------------------------------------------
    # 入队
    # ------------------------------------------------------------------

    async def enqueue(
        self,
        *,
        task_type: str,
        payload: dict[str, Any],
        entity_id: str | None = None,
        simulation_id: str | None = None,
        simulation_step: int | None = None,
        idempotency_extra: str | None = None,
        max_retries: int | None = None,
        priority: int = 5,
        deadline_seconds: float | None = None,
    ) -> Task:
        """投递任务。

        步骤：
        1. 在 ``tasks`` 表创建 ``pending`` 行（idempotency_key 唯一约束去重）。
        2. 把 ``task_id`` 推到对应优先级的 RQ 队列。worker 进程通过
           ``run_task_job(task_id)`` 加载这一行并执行。

        幂等：相同 ``idempotency_key`` 的重复 enqueue 不会插入新行，
        也不会再次入 RQ 队列；返回已存在的 Task 行。
        """
        handler = self._registry.get(task_type)
        if handler is None:
            raise ValueError(f"task_type not registered: {task_type}")

        idem = build_idempotency_key(
            task_type,
            entity_id=entity_id,
            simulation_step=simulation_step,
            extra=idempotency_extra,
        )
        now = utcnow()
        snap = current_trace()
        # 任务拥有独立 trace_id（不继承父 tick/请求的 trace）。
        # 这样任务 trace 的 wall-clock 只反映任务自身的等待+执行耗时，
        # 而不是从父 tick 开始到任务最终完成的跨度（可能数十分钟）。
        # parent_trace_id 保留父 trace 的引用，用于可观测性平台追溯触发链。
        task_trace_id = f"trace_{uuid.uuid4().hex[:12]}"
        enriched_payload = {
            **(payload or {}),
            "__trace__": {
                **snap.as_log_extra(),
                "trace_id": task_trace_id,
                "parent_trace_id": snap.trace_id,  # 链回触发方（world_tick / http 请求）
            },
        }

        deadline_at = None
        if deadline_seconds is not None:
            deadline_at = now + timedelta(seconds=deadline_seconds)

        task = Task(
            task_type=task_type,
            status=PENDING,
            priority=priority,
            entity_id=entity_id,
            simulation_id=simulation_id or snap.simulation_id,
            simulation_step=simulation_step,
            idempotency_key=idem,
            payload=enriched_payload,
            retry_count=0,
            max_retries=max_retries if max_retries is not None else handler.default_max_retries,
            deadline_at=deadline_at,
            enqueued_at=now,
            trace_id=snap.trace_id,
        )

        created = False
        async with self._session_factory() as session:
            session.add(task)
            try:
                await session.commit()
                await session.refresh(task)
                created = True
            except IntegrityError:
                await session.rollback()
                stmt = select(Task).where(Task.idempotency_key == idem)
                existing = (await session.execute(stmt)).scalar_one()
                task = existing

        if created:
            await get_observer().record_task_status(
                task_id=task.id,
                from_status=None,
                to_status=PENDING,
                message=f"enqueue {task_type}",
            )
            # 推到 RQ（同步调用，包在 to_thread 里避免阻塞 event loop）
            try:
                await asyncio.to_thread(self._push_to_rq, task, handler)
            except Exception:
                logger.exception("rq.enqueue failed task_id=%s", task.id)
                # 队列投递失败不回滚 tasks 行——由运维/后台扫描器回捞
                # （阶段 12 MVP 暂不实现扫描器；Redis 只要起着就不会触发这个分支）
                await get_observer().record_task_status(
                    task_id=task.id,
                    from_status=PENDING,
                    to_status=PENDING,
                    message="rq_enqueue_failed",
                )
        return task

    def _push_to_rq(self, task: Task, handler) -> None:
        """同步把 task_id push 到 RQ。运行在 to_thread worker 线程里。"""
        queue_name = _queue_name_for_priority(task.priority)
        q = self._get_rq_queue(queue_name)
        timeout_s = int(max(30.0, handler.default_timeout_seconds * 2))
        q.enqueue(
            self.RUNNER_IMPORT_PATH,
            task.id,
            job_id=f"task:{task.id}",
            job_timeout=timeout_s,
            result_ttl=60,   # RQ 结果 TTL；业务结果写在 tasks.result
            failure_ttl=300,
            description=f"{task.task_type}:{task.entity_id or '-'}",
        )

    async def enqueue_retry(
        self,
        task_id: str,
        *,
        delay_seconds: float = 0.0,
    ) -> None:
        """worker 侧失败重试回调：把一个已存在的 task_id 再推回 RQ。

        ``tasks`` 行的状态更新由 runner 负责（已重置为 PENDING 并 retry_count+1）。
        这里只做队列分发。
        """
        task = await self.get_task(task_id)
        if task is None:
            return
        handler = self._registry.get(task.task_type)
        if handler is None:
            return

        def _push() -> None:
            from datetime import timedelta as _td

            queue_name = _queue_name_for_priority(task.priority)
            q = self._get_rq_queue(queue_name)
            timeout_s = int(max(30.0, handler.default_timeout_seconds * 2))
            job_id = f"task:{task.id}:retry{task.retry_count}"
            if delay_seconds > 0:
                q.enqueue_in(
                    _td(seconds=delay_seconds),
                    self.RUNNER_IMPORT_PATH,
                    task.id,
                    job_id=job_id,
                    job_timeout=timeout_s,
                    result_ttl=60,
                    failure_ttl=300,
                    description=f"{task.task_type}:retry:{task.retry_count}",
                )
            else:
                q.enqueue(
                    self.RUNNER_IMPORT_PATH,
                    task.id,
                    job_id=job_id,
                    job_timeout=timeout_s,
                    result_ttl=60,
                    failure_ttl=300,
                    description=f"{task.task_type}:retry:{task.retry_count}",
                )

        await asyncio.to_thread(_push)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    async def get_task(self, task_id: str) -> Task | None:
        async with self._session_factory() as session:
            return await session.get(Task, task_id)

    async def list_recent(
        self,
        *,
        limit: int = 50,
        status: str | None = None,
        task_type: str | None = None,
    ) -> list[Task]:
        async with self._session_factory() as session:
            stmt = select(Task).order_by(Task.enqueued_at.desc()).limit(limit)
            if status:
                stmt = stmt.where(Task.status == status)
            if task_type:
                stmt = stmt.where(Task.task_type == task_type)
            rows = (await session.execute(stmt)).scalars().all()
            return list(rows)

    # ------------------------------------------------------------------
    # 生命周期占位
    # ------------------------------------------------------------------

    async def start_workers(self, *, concurrency: int = 1) -> None:  # pragma: no cover
        """保留接口以兼容旧调用点；RQ worker 由独立进程启动。

        现在 API 进程**不**在自身 event loop 里跑 worker；请改用
        ``python -m app.domain.tasks.worker`` 或 docker-compose ``worker`` service。
        """
        logger.info(
            "TaskQueue.start_workers is a no-op in rq mode; "
            "start workers via `python -m app.domain.tasks.worker` or docker-compose worker service"
        )

    async def stop_workers(self) -> None:  # pragma: no cover
        return None

    async def close(self) -> None:
        """释放 Redis 连接池。"""
        if self._redis is not None:
            try:
                self._redis.close()
            except Exception:
                pass
            self._redis = None
            self._rq_queues.clear()


_queue: TaskQueue | None = None


def get_task_queue() -> TaskQueue | None:
    """返回当前进程的 TaskQueue 单例（未初始化返回 None）。"""
    return _queue


def init_task_queue(session_factory: async_sessionmaker) -> TaskQueue:
    global _queue
    if _queue is None:
        _queue = TaskQueue(session_factory)
    return _queue


async def reset_inflight_state_on_cold_start(
    session_factory: async_sessionmaker,
) -> dict[str, int]:
    """冷启动收敛：清 RQ 三条优先级队列里的 pending jobs + 把 PG ``tasks``
    表里 ``pending`` / ``running`` 状态收敛到 ``cancelled``。

    背景：``redis_data`` / ``postgres_data`` 在 docker-compose 里都是命名卷
    持久化，单纯重启 ``vt_backend`` / ``vt_worker`` 不会清空它们。上一进程
    入队但 worker 尚未消费的任务（payload 里 ``trace_id`` / ``simulation_step``
    引用的是已经消失的 in-memory 状态）会被新 worker **继续**跑出来，
    不仅浪费 LLM 配额，还制造大量 ``failed`` 指标。

    返回值：``{"queues_purged": N, "pending_deleted": N, "running_cancelled": N}``。
    实现选择：

    - 直接 ``Queue(name).empty()``，不去逐条 cancel——pending 任务在新世界里
      已经没有意义；后续 tick 会用新的 ``simulation_step`` 触发新的入队。
    - PG ``pending`` 行直接 ``DELETE``：``idempotency_key`` 唯一约束保留这些
      行会让下个 tick 的同 key 入队被「返回 existing」屏蔽，无法真正补发。
    - PG ``running`` 行 ``UPDATE`` 为 ``cancelled`` + 写 ``last_error``：
      保留审计行；新 worker 即便误拉到也能在 runner 早期分支安全跳过。
    - 历史 ``succeeded`` / ``failed`` / ``timeout`` 行不动，观测台指标连续。
    """
    from sqlalchemy import delete, update

    from app.db.models import Task

    settings = get_settings()
    purged_total = 0
    queue_names = (
        settings.task_queue_high,
        settings.task_queue_default,
        settings.task_queue_low,
    )

    def _purge_rq_queues() -> int:
        """同步清 RQ 三个优先级队列。运行在线程池里以免阻塞 event loop。"""
        try:
            from redis import Redis
            from rq import Queue
        except Exception:  # pragma: no cover - 容器内必装
            logger.exception("rq/redis import failed; skip RQ purge on cold start")
            return 0
        redis = Redis.from_url(settings.redis_url, decode_responses=False)
        purged = 0
        try:
            for qname in queue_names:
                try:
                    q = Queue(qname, connection=redis)
                    n = q.count
                    q.empty()
                    purged += n
                    logger.info(
                        "rq queue purged on cold start: %s pending=%s",
                        qname,
                        n,
                    )
                except Exception:
                    logger.exception("rq queue purge failed: %s", qname)
        finally:
            with contextlib.suppress(Exception):
                redis.close()
        return purged

    try:
        purged_total = await asyncio.to_thread(_purge_rq_queues)
    except Exception:
        logger.exception("RQ purge thread failed on cold start")

    pending_deleted = 0
    running_cancelled = 0
    try:
        async with session_factory() as session:
            res_pending = await session.execute(
                delete(Task).where(Task.status == PENDING)
            )
            pending_deleted = int(res_pending.rowcount or 0)
            res_running = await session.execute(
                update(Task)
                .where(Task.status == RUNNING)
                .values(
                    status=CANCELLED,
                    last_error="cold_start: backend restarted; in-flight task abandoned",
                    finished_at=utcnow(),
                )
            )
            running_cancelled = int(res_running.rowcount or 0)
            await session.commit()
    except Exception:
        logger.exception("PG tasks reset failed on cold start")

    logger.info(
        "cold start tasks reset: rq_purged=%s pending_deleted=%s running_cancelled=%s",
        purged_total,
        pending_deleted,
        running_cancelled,
    )
    return {
        "queues_purged": purged_total,
        "pending_deleted": pending_deleted,
        "running_cancelled": running_cancelled,
    }


__all__ = [
    "CANCELLED",
    "FAILED",
    "PENDING",
    "RUNNING",
    "SUCCEEDED",
    "TIMEOUT",
    "TaskQueue",
    "build_idempotency_key",
    "get_task_queue",
    "init_task_queue",
    "reset_inflight_state_on_cold_start",
]
