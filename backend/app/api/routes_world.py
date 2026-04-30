"""世界 REST 路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import TargetNotFound
from app.db.session import get_session
from app.schemas.world import (
    Location,
    MapScene,
    MapTile,
    PathfindingRequest,
    PathfindingResponse,
    Portal,
    WorldObject,
)
from app.services.world_service import get_world_service

router = APIRouter(prefix="/world", tags=["world"])


@router.get("/scenes", response_model=list[MapScene])
async def list_scenes(session: AsyncSession = Depends(get_session)) -> list[MapScene]:
    return await get_world_service().list_scenes(session)


@router.get("/scenes/{scene_id}", response_model=MapScene)
async def get_scene(scene_id: str, session: AsyncSession = Depends(get_session)) -> MapScene:
    scene = await get_world_service().get_scene(session, scene_id)
    if scene is None:
        raise TargetNotFound(f"scene {scene_id} not found")
    return scene


@router.get("/scenes/{scene_id}/tiles", response_model=list[MapTile])
async def get_scene_tiles(
    scene_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[MapTile]:
    return await get_world_service().list_tiles(session, scene_id)


@router.get("/locations", response_model=list[Location])
async def list_locations(
    scene_id: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[Location]:
    return await get_world_service().list_locations(session, scene_id)


@router.get("/portals", response_model=list[Portal])
async def list_portals(
    scene_id: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[Portal]:
    return await get_world_service().list_portals(session, scene_id)


@router.get("/objects", response_model=list[WorldObject])
async def list_objects(
    scene_id: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[WorldObject]:
    return await get_world_service().list_objects(session, scene_id)


@router.post("/pathfinding", response_model=PathfindingResponse)
async def pathfinding(
    request: PathfindingRequest,
    session: AsyncSession = Depends(get_session),
) -> PathfindingResponse:
    return await get_world_service().find_path(session, request)
