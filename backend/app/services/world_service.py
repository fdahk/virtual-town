"""
WorldService：场景、地点、对象的读取与寻路。

写入（碰撞、hazard、portal 切换）由 SimulationEngine/PlayerService 通过内部 API 调用。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Location, MapScene, MapTile, Portal, WorldObject
from app.domain.world.grid import astar
from app.domain.world.scene_cache import get_scene_cache
from app.schemas.world import Bounds, Location as LocationSchema
from app.schemas.world import (
    MapScene as MapSceneSchema,
    MapTile as MapTileSchema,
    PathfindingRequest,
    PathfindingResponse,
    Portal as PortalSchema,
    TilePosition,
    WorldObject as WorldObjectSchema,
)


class WorldService:
    async def list_scenes(self, session: AsyncSession) -> list[MapSceneSchema]:
        rows = (await session.execute(select(MapScene).order_by(MapScene.name))).scalars().all()
        return [MapSceneSchema.model_validate(r) for r in rows]

    async def get_scene(self, session: AsyncSession, scene_id: str) -> MapSceneSchema | None:
        row = await session.get(MapScene, scene_id)
        return MapSceneSchema.model_validate(row) if row else None

    async def list_tiles(self, session: AsyncSession, scene_id: str) -> list[MapTileSchema]:
        rows = (
            await session.execute(select(MapTile).where(MapTile.scene_id == scene_id))
        ).scalars().all()
        return [
            MapTileSchema(
                scene_id=r.scene_id,
                x=r.x,
                y=r.y,
                terrain=r.terrain,
                walkable=r.walkable,
                blocks_movement=r.blocks_movement,
                blocks_vision=r.blocks_vision,
                hazard_type=r.hazard_type,
                hazard_level=r.hazard_level,
                tags=r.tags or [],
            )
            for r in rows
        ]

    async def list_locations(
        self, session: AsyncSession, scene_id: str | None
    ) -> list[LocationSchema]:
        stmt = select(Location)
        if scene_id:
            stmt = stmt.where(Location.scene_id == scene_id)
        rows = (await session.execute(stmt)).scalars().all()
        result: list[LocationSchema] = []
        for r in rows:
            bounds = r.bounds or {}
            result.append(
                LocationSchema(
                    id=r.id,
                    scene_id=r.scene_id,
                    name=r.name,
                    location_type=r.location_type,  # type: ignore[arg-type]
                    bounds=Bounds(**bounds),
                    entry_tiles=[TilePosition(**t) for t in (r.entry_tiles or [])],
                    open_hours=r.open_hours,
                    tags=r.tags or [],
                    description=r.description,
                )
            )
        return result

    async def list_portals(
        self, session: AsyncSession, scene_id: str | None
    ) -> list[PortalSchema]:
        stmt = select(Portal)
        if scene_id:
            stmt = stmt.where(Portal.from_scene_id == scene_id)
        rows = (await session.execute(stmt)).scalars().all()
        return [
            PortalSchema(
                id=r.id,
                from_scene_id=r.from_scene_id,
                from_tile=TilePosition(**r.from_tile),
                to_scene_id=r.to_scene_id,
                to_tile=TilePosition(**r.to_tile),
                interaction_type=r.interaction_type,  # type: ignore[arg-type]
                requires_permission=r.requires_permission,
                name=r.name,
            )
            for r in rows
        ]

    async def list_objects(
        self, session: AsyncSession, scene_id: str | None
    ) -> list[WorldObjectSchema]:
        stmt = select(WorldObject)
        if scene_id:
            stmt = stmt.where(WorldObject.scene_id == scene_id)
        rows = (await session.execute(stmt)).scalars().all()
        return [
            WorldObjectSchema(
                id=r.id,
                scene_id=r.scene_id,
                name=r.name,
                object_type=r.object_type,  # type: ignore[arg-type]
                position=TilePosition(**r.position),
                size=r.size,
                blocks_movement=r.blocks_movement,
                available_interactions=r.available_interactions or [],
                state=r.state or {},
                tags=r.tags or [],
            )
            for r in rows
        ]

    async def find_path(
        self, session: AsyncSession, request: PathfindingRequest
    ) -> PathfindingResponse:
        cache = get_scene_cache()
        if not cache.has(request.scene_id):
            await cache.refresh(session)
        grid = cache.get_grid(request.scene_id)
        if grid is None:
            return PathfindingResponse(found=False)
        path = astar(
            grid,
            (request.start.x, request.start.y),
            (request.end.x, request.end.y),
            avoid_hazards=request.avoid_hazards,
        )
        if not path:
            return PathfindingResponse(found=False)
        return PathfindingResponse(
            found=True,
            path=[TilePosition(x=x, y=y) for x, y in path],
            distance=len(path) - 1,
        )


_world_service: WorldService | None = None


def get_world_service() -> WorldService:
    global _world_service
    if _world_service is None:
        _world_service = WorldService()
    return _world_service
