"""
EventRouter（阶段 12 §7 / 14）。

统一出口：``SimulationEngine`` 产生的 ``WorldEvent`` 入库后再经这里分发：

- WebSocket（通过现有 ``ws.broadcast`` 主题，保留向后兼容）
- Observer（记录到观测事件流，category=world_event）
- MemoryService（未来可在此处触发写入 / 反思任务）
- TaskQueue（未来可据事件类型派生任务）

当前实现只做前两项，后两项留钩子给阶段 15/16。
"""

from __future__ import annotations

from typing import Any

from app.core.event_bus import get_event_bus
from app.core.logging import get_logger
from app.services.observer import (
    CATEGORY_WORLD_EVENT,
    get_observer,
)

logger = get_logger(__name__)

WORLD_EVENT_TOPIC = "world.event_persisted"


async def route_world_event(payload: dict[str, Any]) -> None:
    """处理一条已落库的 world event。

    ``payload`` 期望包含：``simulation_id / event_type / actor_entity_id /
    target_entity_id / scene_id / location_id / description / importance /
    payload / created_at``（见 ``WorldEvent`` 模型）。
    """
    event_type = payload.get("event_type") or "world.unknown"
    description = payload.get("description") or ""

    try:
        await get_observer().record_event(
            category=CATEGORY_WORLD_EVENT,
            event_type=event_type,
            title=description[:120] or event_type,
            level="INFO" if (payload.get("importance") or 1) < 5 else "WARNING",
            payload={
                "actor_entity_id": payload.get("actor_entity_id"),
                "target_entity_id": payload.get("target_entity_id"),
                "location_id": payload.get("location_id"),
                "scene_id": payload.get("scene_id"),
                "importance": payload.get("importance"),
                "data": payload.get("payload"),
            },
            entity_id=payload.get("actor_entity_id"),
            simulation_id=payload.get("simulation_id"),
        )
    except Exception:
        logger.exception("route_world_event observer record failed")


def subscribe_event_router() -> None:
    """在应用启动时调用，订阅事件总线的 world event 主题。"""
    bus = get_event_bus()
    bus.subscribe(WORLD_EVENT_TOPIC, _on_world_event)


async def _on_world_event(payload: Any) -> None:
    if isinstance(payload, dict):
        await route_world_event(payload)


__all__ = ["WORLD_EVENT_TOPIC", "route_world_event", "subscribe_event_router"]
