"""
进程内异步事件总线。

- 后端内部跨领域模块通信使用此总线，避免强耦合。
- WebSocket 广播在 `app.websocket.manager` 中订阅本总线。
- 事件对象建议使用 Pydantic 模型，但总线本身对类型不做约束。
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

Handler = Callable[[Any], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._handlers[topic].append(handler)

    def unsubscribe(self, topic: str, handler: Handler) -> None:
        if handler in self._handlers.get(topic, []):
            self._handlers[topic].remove(handler)

    async def publish(self, topic: str, payload: Any) -> None:
        handlers = list(self._handlers.get(topic, []))
        if not handlers:
            return
        results = await asyncio.gather(
            *(handler(payload) for handler in handlers),
            return_exceptions=True,
        )
        # 不让单个订阅者异常影响其他订阅者。错误走日志。
        for result in results:
            if isinstance(result, Exception):
                from app.core.logging import get_logger

                get_logger(__name__).exception("event handler failed", exc_info=result)


_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
