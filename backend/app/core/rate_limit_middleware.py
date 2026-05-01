"""
FastAPI 中间件：按 IP 做公共 API 限流（阶段 18.3）。

- 只拦截 ``/api/`` 前缀下的请求（WS / /healthz 不受影响）。
- 超限返回 429，错误码 ``PLAYER_RATE_LIMITED``，并附 ``Retry-After`` 头。
- 静态 / OpenAPI（``/api/openapi.json`` 等文档）不拦截。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.errors import ErrorCode
from app.core.logging import get_logger
from app.core.trace_context import current as current_trace
from app.domain.safety.rate_limit import check_ip_limit

logger = get_logger(__name__)


_EXCLUDE_PATHS = {
    "/api/openapi.json",
    "/healthz",
}


def _extract_client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip() or "unknown"
    client = request.client
    return client.host if client else "unknown"


def register_rate_limit_middleware(app: FastAPI) -> None:
    settings = get_settings()

    @app.middleware("http")
    async def _rl_middleware(request: Request, call_next):  # type: ignore[no-redef]
        path = request.url.path
        if not path.startswith("/api/") or path in _EXCLUDE_PATHS:
            return await call_next(request)

        ip = _extract_client_ip(request)
        rl = await check_ip_limit(
            ip,
            limit=settings.security_ip_rate_limit,
            window_seconds=settings.security_ip_rate_window_seconds,
        )
        if not rl.allowed:
            snap = current_trace()
            payload: dict[str, Any] = {
                "code": ErrorCode.PLAYER_RATE_LIMITED.value,
                "message": "请求过于频繁，请稍后再试",
                "retryable": True,
                "details": {"retry_after_seconds": rl.retry_after},
            }
            if snap.trace_id:
                payload["trace_id"] = snap.trace_id
            return JSONResponse(
                status_code=429,
                content=payload,
                headers={"Retry-After": str(int(max(rl.retry_after, 1)))},
            )
        return await call_next(request)


__all__ = ["register_rate_limit_middleware"]
