"""世界层 schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class TilePosition(BaseModel):
    x: int
    y: int


class Bounds(BaseModel):
    x: int
    y: int
    width: int
    height: int


class MapScene(BaseModel):
    id: str
    name: str
    scene_type: Literal["outdoor", "indoor"]
    width: int
    height: int
    tile_size: int
    tiled_map_url: str | None = None
    description: str | None = None

    class Config:
        from_attributes = True


class MapTile(BaseModel):
    scene_id: str
    x: int
    y: int
    terrain: str
    walkable: bool
    blocks_movement: bool
    blocks_vision: bool
    hazard_type: str | None = None
    hazard_level: int | None = None
    tags: list[str] = Field(default_factory=list)

    class Config:
        from_attributes = True


class Location(BaseModel):
    id: str
    scene_id: str
    name: str
    location_type: Literal["building", "room", "outdoor_area", "facility"]
    bounds: Bounds
    entry_tiles: list[TilePosition] = Field(default_factory=list)
    open_hours: dict[str, str] | None = None
    tags: list[str] = Field(default_factory=list)
    description: str | None = None


class Portal(BaseModel):
    id: str
    from_scene_id: str
    from_tile: TilePosition
    to_scene_id: str
    to_tile: TilePosition
    interaction_type: Literal["auto_enter", "click_enter"] = "auto_enter"
    requires_permission: bool = False
    name: str | None = None


class WorldObject(BaseModel):
    id: str
    scene_id: str
    name: str
    object_type: Literal["furniture", "facility", "barrier", "plant", "decoration", "item"]
    position: TilePosition
    size: dict[str, int]
    blocks_movement: bool
    available_interactions: list[str] = Field(default_factory=list)
    state: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


class PathfindingRequest(BaseModel):
    scene_id: str
    start: TilePosition
    end: TilePosition
    allow_diagonal: bool = False
    avoid_hazards: bool = True


class PathfindingResponse(BaseModel):
    found: bool
    path: list[TilePosition] = Field(default_factory=list)
    distance: int = 0


class DialogueMessage(BaseModel):
    id: str
    conversation_id: str
    speaker_id: str
    target_id: str | None
    text: str
    emotion: str | None = None
    animation: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True
