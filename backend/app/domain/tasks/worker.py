"""RQ Worker 进程入口（阶段 12 / 阶段 21+ 并发改造）。

历史：阶段 12 落地时使用 ``rq.worker.SimpleWorker.work()``，单线程串行
消费。22 NPC × 每 0.6 真实秒入队一次的 ``agent_decision`` 任务直接把
单 worker 打成 0.1-0.2 jobs/s 的吞吐瓶颈，绝大多数任务在
``deadline_seconds`` 内被丢弃，导致玩家观察到"只有少数 NPC 真正决策、
其它 NPC 反复同样行为且没有记忆"。

阶段 21+ 改造：自定义并发 worker（仍跑在独立 ``worker`` 容器 / 进程内）。

设计要点：

- **单进程 + N 个并发 asyncio 任务**：复用 ``runner._ensure_loop`` 暴露的
  常驻 event loop，避免跨 loop 的 Future 归属问题（参见
  ``docs/开发手册/debug/20260501-task-queue-rq-asyncio-loop.md``）。
- **直接 BLPOP RQ 队列**：跳过 ``Worker.dequeue`` / fork，所有 job 都通过
  asyncio 协程并发执行。``Semaphore`` 控制最大并发数。
- **mini scheduler**：自定义 worker 不再调用 ``Worker.work(with_scheduler=True)``，
  RQ 自带的 scheduler 不会启动。这里手写一个 1s tick 的最小调度器，
  负责把 ``ScheduledJobRegistry`` 中到期的延迟任务（``enqueue_in``）回推到
  普通队列，让 ``TaskQueue.enqueue_retry(delay_seconds>0)`` 仍然可用。
- **graceful drain**：收到 SIGINT/SIGTERM 后停止 BLPOP，再等待已在跑的
  并发任务最多 30s 完成，避免 in-flight 任务被强切。

并发数通过 ``settings.task_queue_worker_concurrency`` 配置（默认 18）。
若需扩到多机，仍可像以前那样 ``docker compose up --scale worker=N``，
每个副本各自跑 N 个并发槽，互不冲突。
"""

from __future__ import annotations

import asyncio
import signal
import threading

from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.db.session import get_session_factory
from app.domain.tasks.runner import (
    _ensure_loop,
    _ensure_worker_bootstrap,
    _execute_task,
)
from app.domain.tasks.ttl_worker import init_ttl_worker

logger = get_logger(__name__)


def _start_ttl_worker_on_shared_loop(interval: float = 60.0):
    """在 runner 的持久 loop 里启动 TTL worker。

    关键：TTL worker 和 runner 必须共用同一个 event loop。SQLAlchemy AsyncEngine
    的 asyncpg 连接池会绑定到首次使用它的 loop；若两边用不同 loop，第二个 loop
    调用会抛 ``got Future attached to a different loop``。
    """
    loop = _ensure_loop()
    session_factory = get_session_factory()
    ttl = init_ttl_worker(session_factory, interval_seconds=interval)

    fut = asyncio.run_coroutine_threadsafe(ttl.start(), loop)
    fut.result(timeout=5)
    return ttl


def main() -> None:
    setup_logging()
    settings = get_settings()
    concurrency = max(1, int(settings.task_queue_worker_concurrency))
    logger.info(
        "rq worker starting env=%s queues=[%s, %s, %s] concurrency=%d",
        settings.app_env,
        settings.task_queue_high,
        settings.task_queue_default,
        settings.task_queue_low,
        concurrency,
    )

    _ensure_worker_bootstrap()

    try:
        from redis import Redis
        from rq import Queue
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "rq/redis missing; ensure `rq>=1.16` is installed in backend image"
        ) from exc

    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    queues = [
        Queue(settings.task_queue_high, connection=redis),
        Queue(settings.task_queue_default, connection=redis),
        Queue(settings.task_queue_low, connection=redis),
    ]

    loop = _ensure_loop()
    ttl = _start_ttl_worker_on_shared_loop(interval=60.0)

    stopping = threading.Event()

    def _on_signal(signum, _frame) -> None:
        logger.info("signal %s received, stopping worker", signum)
        stopping.set()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    try:
        fut = asyncio.run_coroutine_threadsafe(
            _async_worker_main(queues, redis, concurrency, stopping),
            loop,
        )
        fut.result()
    finally:
        try:
            asyncio.run_coroutine_threadsafe(ttl.stop(), loop).result(timeout=3)
        except Exception:
            pass
        try:
            redis.close()
        except Exception:
            pass
        logger.info("rq worker shutdown complete")


# ---------------------------------------------------------------------------
# 异步并发主循环
# ---------------------------------------------------------------------------


async def _async_worker_main(
    queues, redis, concurrency: int, stopping: threading.Event
) -> None:
    """并发消费 RQ 队列。

    单线程的 SimpleWorker 把 22 NPC 决策卡成 0.2 jobs/s；改成 N 个 asyncio 并发
    任务共享一个进程后，吞吐 ≈ N / avg_latency，对 LLM-bound 任务（绝大部分时间
    都在等 HTTP 响应）特别友好。

    优先级排序：``queue_keys`` 列表顺序就是 BLPOP 的优先级。把 ``vt:high`` 排前，
    保证决策任务永远先于 embedding / reflection 被消费。
    """
    from rq.job import Job

    sem = asyncio.Semaphore(concurrency)
    pending: set[asyncio.Task] = set()
    queue_keys = [q.key for q in queues]

    logger.info(
        "concurrent rq worker ready concurrency=%d queues=[%s]",
        concurrency,
        ", ".join(q.name for q in queues),
    )

    scheduler_task = asyncio.create_task(
        _mini_scheduler_loop(redis, queues, stopping),
        name="rq-mini-scheduler",
    )

    try:
        while not stopping.is_set():
            # 清理已完成任务（不阻塞）
            if pending:
                done, pending = await asyncio.wait(
                    pending,
                    timeout=0.0,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in done:
                    exc = t.exception()
                    if exc is not None:
                        logger.error(
                            "concurrent task crashed: %s", exc, exc_info=exc
                        )

            if len(pending) >= concurrency:
                # 满载：等待至少一个任务完成
                done, pending = await asyncio.wait(
                    pending,
                    timeout=1.0,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in done:
                    exc = t.exception()
                    if exc is not None:
                        logger.error(
                            "concurrent task crashed: %s", exc, exc_info=exc
                        )
                continue

            try:
                payload = await asyncio.to_thread(
                    _blpop_one, redis, queue_keys, 1
                )
            except Exception:
                logger.exception("rq blpop failed")
                await asyncio.sleep(1)
                continue

            if payload is None:
                continue

            _, job_id_bytes = payload
            job_id = (
                job_id_bytes.decode()
                if isinstance(job_id_bytes, bytes)
                else str(job_id_bytes)
            )
            try:
                job = await asyncio.to_thread(
                    Job.fetch, job_id, connection=redis
                )
            except Exception:
                logger.debug("job fetch failed id=%s", job_id, exc_info=True)
                continue

            if not job.args:
                logger.debug("job %s has no args, skip", job_id)
                continue

            task_id = job.args[0]
            t = asyncio.create_task(
                _execute_one(task_id, job, sem),
                name=f"task:{task_id}",
            )
            pending.add(t)
    finally:
        scheduler_task.cancel()
        if pending:
            logger.info("draining %d in-flight tasks (up to 30s)", len(pending))
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True),
                    timeout=30,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "drain timeout, %d tasks still running", len(pending)
                )


def _blpop_one(redis, queue_keys: list, timeout: int):
    """阻塞从多队列拉一个 job_id。返回 (queue_key_bytes, job_id_bytes) 或 None。

    ``redis-py`` 的 ``blpop`` 接受 list[bytes|str]，返回元组或 None。timeout=1
    让 stop 信号能在最长 1 秒内被响应。
    """
    return redis.blpop(queue_keys, timeout=timeout)


async def _execute_one(task_id: str, job, sem: asyncio.Semaphore) -> None:
    """单个 RQ job 的执行体：复用 runner._execute_task，外加 RQ Job 清理。"""
    async with sem:
        try:
            await _execute_task(task_id)
        except Exception:
            logger.exception("task %s crashed inside worker", task_id)
        # 业务状态以 ``tasks`` 表为权威源，RQ Job hash 对我们没用，删除即可
        try:
            await asyncio.to_thread(job.delete)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Mini Scheduler：把延迟入队的 retry job 回推主队列
# ---------------------------------------------------------------------------


async def _mini_scheduler_loop(redis, queues, stopping: threading.Event) -> None:
    """周期性把 ``ScheduledJobRegistry`` 里到期的 job 推回普通队列。

    ``TaskQueue.enqueue_retry(delay_seconds>0)`` 通过 ``Queue.enqueue_in`` 把任务
    放到 ScheduledJobRegistry。RQ 自带的 scheduler 通常依赖
    ``Worker.work(with_scheduler=True)``，但我们已经不再调用该方法。这里用最小
    实现接管 scheduler 职责，1 秒 tick 一次。
    """
    from rq.job import Job
    from rq.registry import ScheduledJobRegistry

    while not stopping.is_set():
        try:
            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            return

        try:
            for queue in queues:
                registry = ScheduledJobRegistry(queue=queue)
                try:
                    job_ids = await asyncio.to_thread(
                        registry.get_jobs_to_schedule
                    )
                except Exception:
                    continue
                for jid in job_ids or []:
                    jid_str = (
                        jid.decode() if isinstance(jid, bytes) else str(jid)
                    )
                    try:
                        job = await asyncio.to_thread(
                            Job.fetch, jid_str, connection=redis
                        )
                        await asyncio.to_thread(queue.enqueue_job, job)
                        await asyncio.to_thread(
                            redis.zrem, registry.key, jid_str
                        )
                    except Exception:
                        logger.debug(
                            "mini scheduler: enqueue scheduled %s failed",
                            jid_str,
                            exc_info=True,
                        )
        except Exception:
            logger.debug("mini scheduler tick failed", exc_info=True)


if __name__ == "__main__":
    main()
