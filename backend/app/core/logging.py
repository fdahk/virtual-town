"""
结构化日志。

- 统一 JSON 输出（production）或人类可读（dev）。
- 关键链路必须携带 simulation_id / agent_id / event_id / task_id（由业务层通过 extra 注入）。
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import orjson

from app.core.config import get_settings


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


def setup_logging() -> None:
    """初始化根 logger；可重复调用。"""
    settings = get_settings()
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    if settings.app_env == "dev":
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
                datefmt="%H:%M:%S",
            )
        )
    else:
        handler.setFormatter(OrjsonFormatter())
    root.addHandler(handler)
    root.setLevel(settings.app_log_level.upper())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
