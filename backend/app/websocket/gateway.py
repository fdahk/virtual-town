"""
WebSocket 网关。

- 订阅事件总线的 `ws.broadcast` 主题。
- 按连接广播 simulation.delta / dialogue.message_created / world.* 等事件。
- 所有消息使用统一信封：{version, type, payload, sent_at}。
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

logger = get_logger(__name__)

websocket_router = APIRouter()

WS_BROADCAST_TOPIC = "ws.broadcast"


class ConnectionManager:
    """按 simulation_id 维度管理连接。"""

    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, sim_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[sim_id].add(websocket)
        logger.info("ws connected", extra={"simulation_id": sim_id, "total": self.total(sim_id)})

    async def disconnect(self, sim_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            if websocket in self._connections[sim_id]:
                self._connections[sim_id].discard(websocket)
        logger.info("ws disconnected", extra={"simulation_id": sim_id, "total": self.total(sim_id)})

    def total(self, sim_id: str) -> int:
        return len(self._connections.get(sim_id, set()))

    async def broadcast(self, sim_id: str, message: dict[str, Any]) -> None:
        envelope = {
            "version": "1.0",
            "type": message["type"],
            "payload": message.get("payload", {}),
            "sent_at": iso(utcnow()),
        }
        data = orjson.dumps(envelope).decode()
        async with self._lock:
            conns = list(self._connections.get(sim_id, set()))
        dead: list[WebSocket] = []
        for conn in conns:
            try:
                await conn.send_text(data)
            except Exception as exc:
                logger.warning("ws send failed: %s", exc)
                dead.append(conn)
        for d in dead:
            await self.disconnect(sim_id, d)


_manager: ConnectionManager | None = None


def get_connection_manager() -> ConnectionManager:
    global _manager
    if _manager is None:
        _manager = ConnectionManager()

        async def _on_broadcast(payload: Any) -> None:
            # payload: {"simulation_id": str, "type": str, "payload": dict}
            if not isinstance(payload, dict):
                return
            sim_id = payload.get("simulation_id")
            if not sim_id:
                return
            await _manager.broadcast(
                sim_id,
                {"type": payload["type"], "payload": payload.get("payload", {})},
            )

        get_event_bus().subscribe(WS_BROADCAST_TOPIC, _on_broadcast)
    return _manager


@websocket_router.websocket("/ws/simulations/{simulation_id}")
async def websocket_endpoint(websocket: WebSocket, simulation_id: str) -> None:
    manager = get_connection_manager()
    await manager.connect(simulation_id, websocket)
    try:
        while True:
            # 仅用作 ping/pong；业务推送由服务端发起。
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(simulation_id, websocket)
