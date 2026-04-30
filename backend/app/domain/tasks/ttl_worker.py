"""
TTL 清理 worker（阶段 13 §4）。

职责：
- 定期扫描 Redis 短期记忆键（``agent:{id}:short_memory`` 等），清理已失效条目。
- 把高重要度（``importance >= 7``）的过期短期记忆**升级为长期**（写入 Postgres）。
- 真实时间 + 游戏内时间双触发：默认每 60 秒真实时间触发一次。

实现细节：
- Redis 列表天然有 TTL（写入时已设置），这里更多是兜底清理 + 升级逻辑。
- 不依赖 Redis 存在；Redis 不可用时该 worker 直接跳过循环。
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.logging import get_logger
from app.core.redis_client import get_redis
from app.core.time import utcnow
from app.db.models import Memory
from app.services.memory_service import get_memory_service

logger = get_logger(__name__)

SHORT_TERM_IMPORTANCE_PROMOTE_THRESHOLD = 7


class TTLCleanupWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        *,
        interval_seconds: float = 60.0,
    ) -> None:
        self._session_factory = session_factory
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop = False

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop = False
        self._task = asyncio.create_task(self._loop(), name="ttl-cleanup")

    async def stop(self) -> None:
        self._stop = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        try:
            while not self._stop:
                try:
                    await self._run_once()
                except Exception:
                    logger.exception("ttl cleanup run failed")
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            raise

    async def _run_once(self) -> None:
        # 扫描所有 agent 的短期记忆键，让 Redis 自身 TTL 生效；
        # 同时把已入库但 ttl_expires_at 已过的短期记忆状态流转。
        now = utcnow()
        promoted = 0
        expired = 0
        async with self._session_factory() as session:
            stmt = (
                select(Memory)
                .where(
                    Memory.scope == "short_term",
                    Memory.ttl_expires_at.is_not(None),
                    Memory.ttl_expires_at < now,
                )
                .limit(200)
            )
            rows = (await session.execute(stmt)).scalars().all()
            for mem in rows:
                if (mem.importance or 0) >= SHORT_TERM_IMPORTANCE_PROMOTE_THRESHOLD:
                    mem.scope = "long_term"
                    mem.ttl_expires_at = None
                    promoted += 1
                else:
                    # 短期记忆已过期，置 scope = archived 由业务决定是否清理
                    mem.scope = "archived"
                    expired += 1
            if rows:
                await session.commit()
        if promoted or expired:
            logger.info(
                "ttl cleanup: promoted=%d expired=%d",
                promoted,
                expired,
                extra={"promoted": promoted, "expired": expired},
            )

        # Redis 端 smembers 扫描：只保留还在活跃的键（靠 Redis TTL 自动过期；扫描仅用于暴露指标）。
        redis = get_redis()
        try:
            keys = await redis.scan_keys("agent:*:short_memory", count=500)
            if keys:
                logger.debug("active short-memory keys: %d", len(keys))
        except Exception:
            pass


_worker: TTLCleanupWorker | None = None


def init_ttl_worker(
    session_factory: async_sessionmaker,
    *,
    interval_seconds: float = 60.0,
) -> TTLCleanupWorker:
    global _worker
    if _worker is None:
        _worker = TTLCleanupWorker(session_factory, interval_seconds=interval_seconds)
    return _worker


def get_ttl_worker() -> TTLCleanupWorker | None:
    return _worker


__all__ = ["TTLCleanupWorker", "get_ttl_worker", "init_ttl_worker"]
