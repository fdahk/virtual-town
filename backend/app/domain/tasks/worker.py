"""RQ Worker 进程入口（阶段 12）。

按 ``docs/实施方案/MVP开发任务拆分.md §12`` 与
``docs/实施方案/部署发布与运行维护方案.md §1`` 的选型：

- 队列层为 Redis + RQ。
- 本文件提供 ``python -m app.domain.tasks.worker`` 作为 docker-compose
  ``worker`` service 的入口（等价于 ``rq worker -u $REDIS_URL ...``，
  但额外做了本项目的 bootstrap：logging、handlers 注册、Observer、TTL）。

职责：

1. 建立与 FastAPI 进程对等的 bootstrap（通过 ``runner._ensure_worker_bootstrap``）。
2. 启动 RQ Worker 监听 ``task_queue_high / task_queue_default / task_queue_low``
   三个队列，按优先级消费。
3. 启动 TTL 清理 worker（``app.domain.tasks.ttl_worker``）作为 asyncio 线程任务。

信号处理：``SIGINT / SIGTERM`` 触发 RQ worker ``request_stop()`` 优雅退出。
"""

from __future__ import annotations

import asyncio
import signal

from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.db.session import get_session_factory
from app.domain.tasks.runner import _ensure_loop, _ensure_worker_bootstrap
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
    logger.info(
        "rq worker starting env=%s queues=[%s, %s, %s]",
        settings.app_env,
        settings.task_queue_high,
        settings.task_queue_default,
        settings.task_queue_low,
    )

    # 一次性 bootstrap（注册 handlers / Observer / TaskQueue 单例）
    _ensure_worker_bootstrap()

    try:
        from redis import Redis
        from rq import Queue, Worker
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

    # SimpleWorker 不 fork（MVP 单 worker、共享进程内部的 registry / observer），
    # 同时避免 fork 后子进程的 asyncio/logging 状态错乱。扩展时直接加 worker 容器副本。
    try:
        from rq.worker import SimpleWorker  # rq >= 1.16
    except Exception:  # pragma: no cover
        SimpleWorker = Worker  # fallback

    import os
    worker = SimpleWorker(
        queues,
        connection=redis,
        name=f"vt-worker-{os.getpid()}",
    )

    # TTL worker 与 runner 共享 persistent loop
    ttl = _start_ttl_worker_on_shared_loop(interval=60.0)

    def _on_signal(signum, _frame) -> None:
        logger.info("signal %s received, requesting worker stop", signum)
        worker.request_stop(signum, _frame)
        # 停 TTL（在共享 loop 里执行）
        try:
            loop = _ensure_loop()
            asyncio.run_coroutine_threadsafe(ttl.stop(), loop).result(timeout=3)
        except Exception:
            pass

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    try:
        worker.work(
            with_scheduler=True,
            burst=False,
            logging_level=settings.app_log_level,
        )
    finally:
        try:
            redis.close()
        except Exception:
            pass
        logger.info("rq worker shutdown complete")


if __name__ == "__main__":
    main()
