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
        # 阶段 21+：把 pool_size 从 5 提升到 20，max_overflow 10 → 20。
        # 自定义并发 worker 默认 8 个槽位 × (1 主 session + 1-2 内部 session)
        # ≈ 16-24 个并发 DB 连接；同时还要给 API 进程留出空间。
        # 上限受 PostgreSQL ``max_connections`` 约束（pgvector/pg16 默认 100）；
        # backend + worker 共 40-50 个连接是安全余量。
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            pool_pre_ping=True,
            pool_size=20,
            max_overflow=20,
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
