"""
统一错误模型 & 错误码治理（14.1）。

所有业务错误都继承 ``AppError``，并通过 ``register_error_handlers`` 转换为
固定 JSON 响应结构：

.. code-block:: json

   {
     "code": "WORLD_TARGET_NOT_REACHABLE",
     "message": "目标地点当前不可达。",
     "retryable": true,
     "details": {"target_location_id": "loc_xx"},
     "trace_id": "trace_..."
   }

错误码按模块前缀分类（见 ``ErrorCode`` 枚举）：
``WORLD_ / AGENT_ / MEMORY_ / LLM_ / TOOL_ / PLAYER_ / WS_ / DB_ / TASK_ / SYSTEM_``

- 前端可根据 ``code`` 做稳定的错误处理，避免依赖 message 文案。
- 生产环境响应中包含 ``trace_id`` 便于排查。
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.trace_context import current as current_trace


class ErrorCode(str, Enum):
    """统一错误码枚举，按模块前缀分类。

    新增错误码必须加入此枚举，并在前端 ``ApiError`` 处理中保持稳定的
    ``code`` 映射。
    """

    # ------------------------------------------------------------------
    # 世界 / 地图（碰撞、危险区、场景切换）
    # ------------------------------------------------------------------
    WORLD_TARGET_NOT_FOUND = "WORLD_TARGET_NOT_FOUND"
    WORLD_TARGET_NOT_REACHABLE = "WORLD_TARGET_NOT_REACHABLE"
    WORLD_OUT_OF_RANGE = "WORLD_OUT_OF_RANGE"
    WORLD_PORTAL_UNAVAILABLE = "WORLD_PORTAL_UNAVAILABLE"
    WORLD_BLOCKED = "WORLD_BLOCKED"
    WORLD_HAZARD_ACTIVE = "WORLD_HAZARD_ACTIVE"

    # ------------------------------------------------------------------
    # Agent 状态 / 行为
    # ------------------------------------------------------------------
    AGENT_NOT_FOUND = "AGENT_NOT_FOUND"
    AGENT_STATE_CONFLICT = "AGENT_STATE_CONFLICT"
    AGENT_DECISION_FAILED = "AGENT_DECISION_FAILED"
    AGENT_ACTION_REJECTED = "AGENT_ACTION_REJECTED"

    # ------------------------------------------------------------------
    # 记忆
    # ------------------------------------------------------------------
    MEMORY_NOT_FOUND = "MEMORY_NOT_FOUND"
    MEMORY_WRITE_FAILED = "MEMORY_WRITE_FAILED"
    MEMORY_SEARCH_FAILED = "MEMORY_SEARCH_FAILED"
    MEMORY_EMBEDDING_FAILED = "MEMORY_EMBEDDING_FAILED"

    # ------------------------------------------------------------------
    # LLM
    # ------------------------------------------------------------------
    LLM_NOT_CONFIGURED = "LLM_NOT_CONFIGURED"
    LLM_TIMEOUT = "LLM_TIMEOUT"
    LLM_RETURN_INVALID = "LLM_RETURN_INVALID"
    LLM_SCHEMA_INVALID = "LLM_SCHEMA_INVALID"
    LLM_RATE_LIMITED = "LLM_RATE_LIMITED"
    LLM_UPSTREAM_ERROR = "LLM_UPSTREAM_ERROR"

    # ------------------------------------------------------------------
    # Tool calling
    # ------------------------------------------------------------------
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    TOOL_INVALID_ARGUMENTS = "TOOL_INVALID_ARGUMENTS"
    TOOL_PERMISSION_DENIED = "TOOL_PERMISSION_DENIED"
    TOOL_WORLD_STATE_INVALID = "TOOL_WORLD_STATE_INVALID"
    TOOL_EXECUTION_FAILED = "TOOL_EXECUTION_FAILED"

    # ------------------------------------------------------------------
    # 玩家 / 交互
    # ------------------------------------------------------------------
    PLAYER_NOT_FOUND = "PLAYER_NOT_FOUND"
    PLAYER_ACTION_REJECTED = "PLAYER_ACTION_REJECTED"
    PLAYER_RATE_LIMITED = "PLAYER_RATE_LIMITED"
    PLAYER_INVALID_INPUT = "PLAYER_INVALID_INPUT"

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------
    WS_CONNECTION_CLOSED = "WS_CONNECTION_CLOSED"
    WS_INVALID_MESSAGE = "WS_INVALID_MESSAGE"

    # ------------------------------------------------------------------
    # 数据库 / 缓存
    # ------------------------------------------------------------------
    DB_UNAVAILABLE = "DB_UNAVAILABLE"
    DB_CONFLICT = "DB_CONFLICT"
    DB_TIMEOUT = "DB_TIMEOUT"

    # ------------------------------------------------------------------
    # 任务队列
    # ------------------------------------------------------------------
    TASK_NOT_FOUND = "TASK_NOT_FOUND"
    TASK_DUPLICATE = "TASK_DUPLICATE"
    TASK_FAILED = "TASK_FAILED"
    TASK_TIMEOUT = "TASK_TIMEOUT"

    # ------------------------------------------------------------------
    # 系统 / 框架（沿用旧枚举，避免破坏性变更）
    # ------------------------------------------------------------------
    SYSTEM_INTERNAL_ERROR = "INTERNAL_ERROR"
    SYSTEM_INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    SYSTEM_PERMISSION_DENIED = "PERMISSION_DENIED"
    SYSTEM_TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    SYSTEM_TARGET_NOT_REACHABLE = "TARGET_NOT_REACHABLE"
    SYSTEM_OUT_OF_RANGE = "OUT_OF_RANGE"
    SYSTEM_STATE_CONFLICT = "STATE_CONFLICT"


class AppError(Exception):
    """应用层统一异常基类。"""

    code: str = ErrorCode.SYSTEM_INTERNAL_ERROR.value
    http_status: int = 500
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        code: str | ErrorCode | None = None,
        http_status: int | None = None,
        retryable: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code.value if isinstance(code, ErrorCode) else code
        if http_status is not None:
            self.http_status = http_status
        if retryable is not None:
            self.retryable = retryable
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        data = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }
        snap = current_trace()
        if snap.trace_id:
            data["trace_id"] = snap.trace_id
        if snap.request_id:
            data["request_id"] = snap.request_id
        return data


class InvalidArguments(AppError):
    code = ErrorCode.SYSTEM_INVALID_ARGUMENTS.value
    http_status = 400


class PermissionDenied(AppError):
    code = ErrorCode.SYSTEM_PERMISSION_DENIED.value
    http_status = 403


class TargetNotFound(AppError):
    code = ErrorCode.SYSTEM_TARGET_NOT_FOUND.value
    http_status = 404


class TargetNotReachable(AppError):
    code = ErrorCode.SYSTEM_TARGET_NOT_REACHABLE.value
    http_status = 409
    retryable = True


class OutOfRange(AppError):
    code = ErrorCode.SYSTEM_OUT_OF_RANGE.value
    http_status = 409


class StateConflict(AppError):
    code = ErrorCode.SYSTEM_STATE_CONFLICT.value
    http_status = 409


# -----------------------------------------------------------------------------
# 新错误基类：用模块前缀替代 SYSTEM_，供新模块使用
# -----------------------------------------------------------------------------


class TaskError(AppError):
    code = ErrorCode.TASK_FAILED.value
    http_status = 500
    retryable = True


class TaskDuplicate(AppError):
    code = ErrorCode.TASK_DUPLICATE.value
    http_status = 409
    retryable = False


class TaskNotFound(AppError):
    code = ErrorCode.TASK_NOT_FOUND.value
    http_status = 404


class LLMUnavailable(AppError):
    code = ErrorCode.LLM_NOT_CONFIGURED.value
    http_status = 503
    retryable = True


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content=exc.to_dict())

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        snap = current_trace()
        payload: dict[str, Any] = {
            "code": ErrorCode.SYSTEM_INVALID_ARGUMENTS.value,
            "message": "请求参数不合法",
            "retryable": False,
            "details": {"errors": exc.errors()},
        }
        if snap.trace_id:
            payload["trace_id"] = snap.trace_id
        if snap.request_id:
            payload["request_id"] = snap.request_id
        return JSONResponse(status_code=422, content=payload)


__all__ = [
    "AppError",
    "ErrorCode",
    "InvalidArguments",
    "LLMUnavailable",
    "OutOfRange",
    "PermissionDenied",
    "StateConflict",
    "TargetNotFound",
    "TargetNotReachable",
    "TaskDuplicate",
    "TaskError",
    "TaskNotFound",
    "register_error_handlers",
]
