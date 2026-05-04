"""
异步 SQLAlchemy session 管理。

- 只在 HTTP / WebSocket 请求入口处创建 session。
- 领域层接受 AsyncSession 作为依赖，不自行创建。
- Seed 脚本使用同步 session，避免事件循环复杂度。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine, _session_factory
    if _engine is None:
        settings = get_settings()
        # 阶段 21+：pool 与 ``task_queue_worker_concurrency``（默认 18）对齐：
        # 单任务峰值约 2 条并发 session → 36，余量给 ttl / mini-scheduler / API。
        # 多 worker 副本时每进程各有一份池 —— 扩 ``--scale worker=N`` 时请核对
        # Postgres ``max_connections``（默认 100），必要时下调 concurrency 或池上限。
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            pool_pre_ping=True,
            pool_size=22,
            max_overflow=22,
        )
        _session_factory = async_sessionmaker(
            _engine, expire_on_commit=False, class_=AsyncSession
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖注入入口。"""
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
