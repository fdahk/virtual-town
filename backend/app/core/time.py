"""时间工具：统一时区与 ISO 8601 格式化。"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """带时区的当前 UTC 时间。避免使用 `datetime.utcnow()`。"""
    return datetime.now(tz=timezone.utc)


def iso(dt: datetime) -> str:
    """确保输出 ISO 8601 字符串，含时区。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()
