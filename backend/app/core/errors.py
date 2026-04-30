"""
统一错误模型。

所有业务错误都继承 `AppError`，并通过 `register_error_handlers`
转换为固定的 JSON 响应结构：

```
{
  "code": "TARGET_NOT_FOUND",
  "message": "xxx",
  "retryable": false,
  "details": {...}
}
```

前端可以根据 `code` 做稳定的错误处理，避免依赖 message 文案。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    """应用层统一异常基类。"""

    code: str = "INTERNAL_ERROR"
    http_status: int = 500
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        http_status: int | None = None,
        retryable: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        if retryable is not None:
            self.retryable = retryable
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }


class InvalidArguments(AppError):
    code = "INVALID_ARGUMENTS"
    http_status = 400


class PermissionDenied(AppError):
    code = "PERMISSION_DENIED"
    http_status = 403


class TargetNotFound(AppError):
    code = "TARGET_NOT_FOUND"
    http_status = 404


class TargetNotReachable(AppError):
    code = "TARGET_NOT_REACHABLE"
    http_status = 409
    retryable = True


class OutOfRange(AppError):
    code = "OUT_OF_RANGE"
    http_status = 409


class StateConflict(AppError):
    code = "STATE_CONFLICT"
    http_status = 409


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content=exc.to_dict())

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": "INVALID_ARGUMENTS",
                "message": "请求参数不合法",
                "retryable": False,
                "details": {"errors": exc.errors()},
            },
        )
