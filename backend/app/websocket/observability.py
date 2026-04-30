"""
Observability WebSocket 网关（阶段 14.3）。

独立于普通仿真 WS 的观测推送通道。订阅 ``observability.event`` 主题，
向订阅端广播统一信封：

.. code-block:: json

   {
     "version": "1.0",
     "type": "observability.event_created",
     "payload": { ... },
     "sent_at": "..."
   }
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

import orjson
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.event_bus import get_event_bus
from app.core.logging import get_logger
from app.core.time import iso, utcnow
from app.services.observer import OBSERVABILITY_TOPIC

logger = get_logger(__name__)

observability_ws_router = APIRouter()


class ObservabilityConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, sim_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[sim_id].add(websocket)
        logger.info(
            "observability ws connected",
            extra={"simulation_id": sim_id, "total": self.total(sim_id)},
        )

    async def disconnect(self, sim_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections[sim_id].discard(websocket)

    def total(self, sim_id: str | None) -> int:
        if sim_id is None:
            return sum(len(v) for v in self._connections.values())
        return len(self._connections.get(sim_id, set()))

    async def broadcast(self, sim_id: str | None, message: dict[str, Any]) -> None:
        envelope = {
            "version": "1.0",
            "type": message["type"],
            "payload": message.get("payload", {}),
            "sent_at": iso(utcnow()),
        }
        data = orjson.dumps(envelope).decode()
        async with self._lock:
            targets: list[WebSocket] = []
            if sim_id is None:
                for conns in self._connections.values():
                    targets.extend(conns)
            else:
                targets.extend(list(self._connections.get(sim_id, set())))
                # 同时广播给订阅 "*" 的通用连接
                targets.extend(list(self._connections.get("*", set())))
        dead: list[WebSocket] = []
        for conn in targets:
            try:
                await conn.send_text(data)
            except Exception as exc:
                logger.debug("obs ws send failed: %s", exc)
                dead.append(conn)
        for d in dead:
            for sid, conns in self._connections.items():
                if d in conns:
                    conns.discard(d)


_manager: ObservabilityConnectionManager | None = None


def get_observability_manager() -> ObservabilityConnectionManager:
    global _manager
    if _manager is None:
        _manager = ObservabilityConnectionManager()

        async def _on_obs_event(payload: Any) -> None:
            if not isinstance(payload, dict):
                return
            sim_id = payload.get("simulation_id")
            await _manager.broadcast(
                sim_id,
                {"type": "observability.event_created", "payload": payload},
            )

        get_event_bus().subscribe(OBSERVABILITY_TOPIC, _on_obs_event)
    return _manager


@observability_ws_router.websocket("/ws/observability/{simulation_id}")
async def ws_observability(websocket: WebSocket, simulation_id: str) -> None:
    manager = get_observability_manager()
    await manager.connect(simulation_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(simulation_id, websocket)


__all__ = [
    "ObservabilityConnectionManager",
    "get_observability_manager",
    "observability_ws_router",
]
