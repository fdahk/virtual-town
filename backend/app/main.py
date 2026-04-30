"""
FastAPI 入口。

职责：
- 加载配置和日志。
- 注册统一错误处理。
- 挂载 REST 路由与 WebSocket。
- 管理仿真引擎生命周期。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import api_router
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.logging import get_logger, setup_logging
from app.core.redis_client import get_redis
from app.db.session import dispose_engine, get_session_factory
from app.domain.tasks.handlers import register_default_handlers
from app.domain.tasks.queue import init_task_queue
from app.domain.tasks.ttl_worker import init_ttl_worker
from app.services.event_router import subscribe_event_router
from app.services.observer import get_observer
from app.services.simulation_runtime import get_simulation_runtime
from app.websocket.gateway import websocket_router
from app.websocket.observability import observability_ws_router

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings = get_settings()
    logger.info(
        "starting %s env=%s debug=%s llm_enabled=%s",
        settings.app_name,
        settings.app_env,
        settings.app_debug,
        settings.llm_is_configured,
    )

    # 观测基础设施（阶段 14.1/14.2）
    session_factory = get_session_factory()
    observer = get_observer()
    observer.bind_session_factory(session_factory)
    await observer.start()

    # 世界事件 → Observer / 业务订阅者（阶段 12 §7）
    subscribe_event_router()

    # 异步任务队列（阶段 12：Redis + RQ）
    # API 进程只做 enqueue；消费由独立 ``worker`` 容器执行
    # （docker-compose worker service / `python -m app.domain.tasks.worker`）。
    register_default_handlers()
    queue = init_task_queue(session_factory)

    # TTL 清理 worker（阶段 13 §4）：轻量级 asyncio 任务，保留在 API 进程。
    ttl_worker = init_ttl_worker(session_factory, interval_seconds=60.0)
    await ttl_worker.start()

    runtime = get_simulation_runtime()
    await runtime.start()

    try:
        yield
    finally:
        await runtime.stop()
        await ttl_worker.stop()
        await queue.close()
        await observer.stop()
        try:
            await get_redis().close()
        except Exception:
            pass
        await dispose_engine()
        logger.info("shutdown complete")


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging()
    app = FastAPI(
        title="AI 小镇 API",
        description="Virtual Town backend (FastAPI + PostgreSQL/pgvector + Redis + WebSocket)",
        version="0.1.0",
        lifespan=lifespan,
        debug=settings.app_debug,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)

    app.include_router(api_router, prefix="/api")
    app.include_router(websocket_router)
    app.include_router(observability_ws_router)

    # REST 入口：为每个请求注入 request_id + trace_id（§14.1）
    from app.core.trace_context import TraceContext

    @app.middleware("http")
    async def _trace_middleware(request, call_next):  # type: ignore[no-redef]
        request_id = request.headers.get("x-request-id")
        with TraceContext.start_trace(
            f"http.{request.method.lower()}",
            request_id=request_id,
        ) as snap:
            response = await call_next(request)
            if snap.request_id:
                response.headers.setdefault("x-request-id", snap.request_id)
            if snap.trace_id:
                response.headers.setdefault("x-trace-id", snap.trace_id)
            return response

    @app.get("/healthz", tags=["system"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
