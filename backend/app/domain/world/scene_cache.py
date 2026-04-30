"""
场景缓存：启动时将每个 scene 的网格从数据库加载到内存。

World Service 和 SimulationEngine 都从此缓存读取网格进行碰撞/寻路。
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MapScene, MapTile, WorldObject
from app.domain.world.grid import SceneGrid, TileInfo


class SceneCache:
    def __init__(self) -> None:
        self._grids: dict[str, SceneGrid] = {}
        self._scenes: dict[str, MapScene] = {}
        self._lock = asyncio.Lock()

    def has(self, scene_id: str) -> bool:
        return scene_id in self._grids

    def get_grid(self, scene_id: str) -> SceneGrid | None:
        return self._grids.get(scene_id)

    def get_scene_meta(self, scene_id: str) -> dict[str, Any] | None:
        scene = self._scenes.get(scene_id)
        if scene is None:
            return None
        return {
            "id": scene.id,
            "name": scene.name,
            "scene_type": scene.scene_type,
            "width": scene.width,
            "height": scene.height,
            "tile_size": scene.tile_size,
        }

    def all_scene_ids(self) -> list[str]:
        return list(self._scenes.keys())

    async def refresh(self, session: AsyncSession) -> None:
        async with self._lock:
            scenes = (await session.execute(select(MapScene))).scalars().all()
            self._scenes = {s.id: s for s in scenes}
            for scene in scenes:
                await self._load_scene(session, scene)

    async def _load_scene(self, session: AsyncSession, scene: MapScene) -> None:
        grid = SceneGrid(
            scene_id=scene.id,
            width=scene.width,
            height=scene.height,
            tile_size=scene.tile_size,
        )
        tiles_rows = (
            await session.execute(select(MapTile).where(MapTile.scene_id == scene.id))
        ).scalars().all()
        for row in tiles_rows:
            grid.tiles[(row.x, row.y)] = TileInfo(
                x=row.x,
                y=row.y,
                walkable=row.walkable,
                blocks_movement=row.blocks_movement,
                hazard_type=row.hazard_type,
                hazard_level=row.hazard_level,
                terrain=row.terrain,
            )
        # 把 blocks_movement 的物体应用到网格
        objects = (
            await session.execute(
                select(WorldObject).where(
                    WorldObject.scene_id == scene.id,
                    WorldObject.blocks_movement == True,  # noqa: E712
                )
            )
        ).scalars().all()
        for obj in objects:
            width = int(obj.size.get("width", 1))
            height = int(obj.size.get("height", 1))
            px = int(obj.position.get("x", 0))
            py = int(obj.position.get("y", 0))
            for dx in range(width):
                for dy in range(height):
                    grid.block(px + dx, py + dy)
        self._grids[scene.id] = grid


_cache: SceneCache | None = None


def get_scene_cache() -> SceneCache:
    global _cache
    if _cache is None:
        _cache = SceneCache()
    return _cache
