"""
场景网格 & A* 寻路。

- 网格从数据库 map_tiles 加载到内存，避免每 tick 查库。
- 网格一旦变更（建筑状态切换、护栏开闭）必须通过 `update_tile` 同步。
- A* 使用 Manhattan 距离；不支持对角（MVP）。
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Iterable


@dataclass(slots=True)
class TileInfo:
    """单格信息。walkable 为最终是否可走（综合 walkable、blocks_movement、对象阻挡）。"""

    x: int
    y: int
    walkable: bool
    blocks_movement: bool
    hazard_type: str | None = None
    hazard_level: int | None = None
    terrain: str = "grass"


@dataclass(slots=True)
class SceneGrid:
    scene_id: str
    width: int
    height: int
    tile_size: int
    tiles: dict[tuple[int, int], TileInfo] = field(default_factory=dict)

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def get(self, x: int, y: int) -> TileInfo | None:
        return self.tiles.get((x, y))

    def is_walkable(self, x: int, y: int, *, avoid_hazards: bool = False) -> bool:
        if not self.in_bounds(x, y):
            return False
        tile = self.tiles.get((x, y))
        if tile is None:
            return True
        if tile.blocks_movement:
            return False
        if not tile.walkable:
            return False
        if avoid_hazards and tile.hazard_type is not None:
            return False
        return True

    def neighbors(self, x: int, y: int) -> Iterable[tuple[int, int]]:
        yield from ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))

    def block(self, x: int, y: int) -> None:
        tile = self.tiles.get((x, y))
        if tile is None:
            self.tiles[(x, y)] = TileInfo(x=x, y=y, walkable=False, blocks_movement=True)
        else:
            tile.blocks_movement = True
            tile.walkable = False

    def update_tile(self, tile: TileInfo) -> None:
        self.tiles[(tile.x, tile.y)] = tile


def manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def astar(
    grid: SceneGrid,
    start: tuple[int, int],
    goal: tuple[int, int],
    *,
    avoid_hazards: bool = True,
) -> list[tuple[int, int]]:
    """
    经典 A*，返回从 start 到 goal 的 tile 序列（含 start 与 goal）。
    - 若 start 不可走，仍允许作为起点（角色可能处于 hazard 中想逃离）。
    - 若 goal 不可走且避开 hazard，则失败返回空列表。
    """
    if start == goal:
        return [start]
    if not grid.in_bounds(*goal):
        return []
    if not grid.is_walkable(*goal, avoid_hazards=avoid_hazards):
        return []

    open_set: list[tuple[int, int, tuple[int, int]]] = []
    heapq.heappush(open_set, (manhattan(start, goal), 0, start))
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    g_score: dict[tuple[int, int], int] = {start: 0}

    while open_set:
        _, current_g, current = heapq.heappop(open_set)
        if current == goal:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            path.reverse()
            return path
        for nx, ny in grid.neighbors(*current):
            if not grid.is_walkable(nx, ny, avoid_hazards=avoid_hazards):
                continue
            tentative = current_g + 1
            if tentative < g_score.get((nx, ny), 1 << 30):
                g_score[(nx, ny)] = tentative
                came_from[(nx, ny)] = current
                f = tentative + manhattan((nx, ny), goal)
                heapq.heappush(open_set, (f, tentative, (nx, ny)))
    return []
