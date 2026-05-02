"""把 ``WorldPlan`` 写入数据库。

支持：
- 同步上下文（``SeedSession``）：用于 seed 脚本 / 单元测试
- 异步上下文（``AsyncSession``）：用于 ``POST /api/games/new`` API

写入顺序：
1. 清空旧数据（按 FK 倒序）
2. 写入 scenes / tiles / locations / portals / world_objects
3. 写入 agents / agent_states
4. 写入 relationships
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.db.models import (
    Agent,
    AgentAction,
    AgentState,
    DialogueMessage,
    InteractionRequest,
    Location,
    MapScene,
    MapTile,
    Memory,
    Portal,
    Relationship,
    Simulation,
    WorldEvent,
    WorldObject,
)
from app.domain.world_gen.types import WorldPlan


# 清空表的顺序（按 FK 倒序，避免外键违反）
CLEAR_TABLES = (
    DialogueMessage,
    InteractionRequest,
    WorldEvent,
    AgentAction,
    Memory,
    Relationship,
    AgentState,
    Agent,
    WorldObject,
    Portal,
    Location,
    MapTile,
    MapScene,
    Simulation,
)


def _scene_orm(row: dict[str, Any]) -> MapScene:
    return MapScene(
        id=row["id"],
        name=row["name"],
        scene_type=row["scene_type"],
        width=row["width"],
        height=row["height"],
        tile_size=row.get("tile_size", 32),
        description=row.get("description"),
    )


def _tile_orm(scene_id: str, row: dict[str, Any]) -> MapTile:
    return MapTile(
        scene_id=scene_id,
        x=row["x"], y=row["y"], terrain=row["terrain"],
        walkable=row["walkable"], blocks_movement=row["blocks_movement"],
        blocks_vision=row["blocks_vision"],
        hazard_type=row.get("hazard_type"),
        hazard_level=row.get("hazard_level"),
        tags=list(row.get("tags") or []),
    )


def _location_orm(row: dict[str, Any]) -> Location:
    return Location(
        id=row["id"], scene_id=row["scene_id"], name=row["name"],
        location_type=row["location_type"], bounds=row["bounds"],
        entry_tiles=row["entry_tiles"], open_hours=row.get("open_hours"),
        tags=list(row.get("tags") or []),
        description=row.get("description"),
    )


def _portal_orm(row: dict[str, Any]) -> Portal:
    return Portal(
        id=row["id"],
        from_scene_id=row["from_scene_id"], from_tile=row["from_tile"],
        to_scene_id=row["to_scene_id"], to_tile=row["to_tile"],
        interaction_type=row.get("interaction_type", "auto_enter"),
        requires_permission=row.get("requires_permission", False),
        name=row.get("name"),
    )


_VALID_OBJECT_TYPES = {
    "furniture",
    "facility",
    "barrier",
    "plant",
    "decoration",
    "item",
    "nature_spot",
}


def _object_orm(row: dict[str, Any]) -> WorldObject:
    obj_type = row["object_type"]
    if obj_type not in _VALID_OBJECT_TYPES:
        # 防御性校验：world_gen 早期版本把河岸围栏写成 ``fence``、把蘑菇/水果/钓点
        # 写成 ``natural``，此时 ``WorldObjectSchema`` 的 Literal 校验会让
        # ``GET /api/world/objects`` 整页 500，前端 TownScene 卡在 75% 加载。
        # 这里不再静默写入垃圾数据，而是抛错让生成阶段就崩，逼迫开发者用 schema
        # 允许的 7 个值之一（见 ``schemas/world.py::WorldObject.object_type``）。
        raise ValueError(
            f"world_gen 产生了非法 object_type={obj_type!r}（name={row.get('name')!r}）；"
            f"必须是 {sorted(_VALID_OBJECT_TYPES)} 之一"
        )
    return WorldObject(
        id=row["id"], scene_id=row["scene_id"], name=row["name"],
        object_type=obj_type, position=row["position"],
        size=row.get("size", {"width": 1, "height": 1}),
        blocks_movement=row.get("blocks_movement", False),
        available_interactions=list(row.get("available_interactions") or []),
        state=dict(row.get("state") or {}),
        tags=list(row.get("tags") or []),
    )


def _agent_orm(row: dict[str, Any]) -> Agent:
    return Agent(
        id=row["id"], entity_type=row["entity_type"], name=row["name"],
        age=row.get("age"), gender=row.get("gender"), species=row.get("species"),
        occupation=row.get("occupation"),
        personality=list(row.get("personality") or []),
        background=row.get("background", ""),
        lifestyle=row.get("lifestyle"),
        appearance=dict(row.get("appearance") or {}),
        long_term_goals=list(row.get("long_term_goals") or []),
        home_location_id=row.get("home_location_id"),
        schedule_template=list(row.get("schedule_template") or []),
        is_active=row.get("is_active", True),
    )


def _agent_state_orm(row: dict[str, Any]) -> AgentState:
    return AgentState(
        agent_id=row["agent_id"],
        scene_id=row["scene_id"],
        x=row["x"], y=row["y"],
        state=row.get("state", "IDLE"),
        emotion=row.get("emotion"),
        energy=row.get("energy", 1.0),
        hunger=row.get("hunger", 0.2),
        social_need=row.get("social_need", 0.3),
        fear=row.get("fear", 0.0),
        facing=row.get("facing", "down"),
    )


def _relationship_orm(row: dict[str, Any]) -> Relationship:
    return Relationship(
        id=row["id"],
        from_agent_id=row["from_agent_id"],
        to_entity_id=row["to_entity_id"],
        familiarity=row.get("familiarity", 0.0),
        trust=row.get("trust", 0.0),
        affection=row.get("affection", 0.0),
        fear=row.get("fear", 0.0),
        summary=row.get("summary"),
    )


def apply_plan(session: Session, plan: WorldPlan) -> None:
    """同步写入（供 seed 脚本使用）。"""
    for model in CLEAR_TABLES:
        session.execute(delete(model))
    session.commit()

    # Scenes
    session.add_all([_scene_orm(s) for s in plan.scenes])
    session.flush()

    # Tiles
    for scene_id, tiles in plan.tiles_by_scene.items():
        session.add_all([_tile_orm(scene_id, t) for t in tiles])

    # Locations / Portals / Objects
    session.add_all([_location_orm(l) for l in plan.locations])
    session.flush()
    session.add_all([_portal_orm(p) for p in plan.portals])
    session.add_all([_object_orm(o) for o in plan.world_objects])

    # Agents
    session.add_all([_agent_orm(a) for a in plan.agents])
    session.flush()
    session.add_all([_agent_state_orm(s) for s in plan.agent_states])

    # Relationships
    session.add_all([_relationship_orm(r) for r in plan.relationships])

    session.commit()


async def apply_plan_async(session: AsyncSession, plan: WorldPlan) -> None:
    """异步写入（供 API 使用）。"""
    for model in CLEAR_TABLES:
        await session.execute(delete(model))
    await session.commit()

    session.add_all([_scene_orm(s) for s in plan.scenes])
    await session.flush()
    for scene_id, tiles in plan.tiles_by_scene.items():
        session.add_all([_tile_orm(scene_id, t) for t in tiles])
    session.add_all([_location_orm(l) for l in plan.locations])
    await session.flush()
    session.add_all([_portal_orm(p) for p in plan.portals])
    session.add_all([_object_orm(o) for o in plan.world_objects])
    session.add_all([_agent_orm(a) for a in plan.agents])
    await session.flush()
    session.add_all([_agent_state_orm(s) for s in plan.agent_states])
    session.add_all([_relationship_orm(r) for r in plan.relationships])
    await session.commit()
