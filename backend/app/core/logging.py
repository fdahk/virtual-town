"""
结构化日志。

- 统一 JSON 输出（production）或人类可读（dev）。
- 关键链路通过 ``TraceContext`` 自动携带 ``trace_id / span_id / simulation_id /
  agent_id / player_id / task_id / event_id``；业务代码也可通过 ``logger.xxx(..., extra={...})``
  覆盖特定字段。
- 所有日志都会被 ``TraceContextFilter`` 额外注入当前 trace 上下文。
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import orjson

from app.core.config import get_settings
from app.core.trace_context import current as current_trace


class TraceContextFilter(logging.Filter):
    """把当前 TraceContext 的字段注入到每条日志记录。

    - 注入的字段以 ``setattr(record, key, value)`` 形式存在；
    - 如果业务代码已经通过 ``extra=...`` 赋值，则不覆盖。
    """

    _FIELDS = (
        "request_id",
        "trace_id",
        "span_id",
        "parent_span_id",
        "simulation_id",
        "agent_id",
        "player_id",
        "task_id",
        "event_id",
        "category",
    )

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        snapshot = current_trace().as_log_extra()
        for key in self._FIELDS:
            if key in snapshot and not hasattr(record, key):
                setattr(record, key, snapshot[key])
        return True


class OrjsonFormatter(logging.Formatter):
    """简洁的 JSON formatter，方便在容器日志与 ELK 栈里消费。"""

    RESERVED_FIELDS = {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "asctime",
        "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key in self.RESERVED_FIELDS:
                continue
            if key.startswith("_"):
                continue
            try:
                orjson.dumps(value)
            except (TypeError, orjson.JSONEncodeError):
                value = repr(value)
            payload[key] = value
        return orjson.dumps(payload).decode("utf-8")


class DevTraceFormatter(logging.Formatter):
    """dev 模式下人类可读 + 关键 trace 字段 inline。"""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%H:%M:%S",
        )

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        trace_hint: list[str] = []
        for key in ("trace_id", "simulation_id", "agent_id", "task_id"):
            value = getattr(record, key, None)
            if value:
                trace_hint.append(f"{key}={value}")
        if trace_hint:
            base = f"{base}  [{', '.join(trace_hint)}]"
        return base


def setup_logging() -> None:
    """初始化根 logger；可重复调用。"""
    settings = get_settings()
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    # 清理残留的 filter 再统一安装
    for f in list(root.filters):
        root.removeFilter(f)

    handler = logging.StreamHandler(sys.stdout)
    if settings.app_env == "dev":
        handler.setFormatter(DevTraceFormatter())
    else:
        handler.setFormatter(OrjsonFormatter())
    root.addHandler(handler)
    root.addFilter(TraceContextFilter())
    root.setLevel(settings.app_log_level.upper())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
