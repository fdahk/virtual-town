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
from app.db.session import dispose_engine
from app.services.simulation_runtime import get_simulation_runtime
from app.websocket.gateway import websocket_router

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

    runtime = get_simulation_runtime()
    await runtime.start()

    try:
        yield
    finally:
        await runtime.stop()
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

    @app.get("/healthz", tags=["system"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
