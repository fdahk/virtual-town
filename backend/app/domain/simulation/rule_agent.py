"""
规则版 Agent 行为。

职责：
- 每个 AI tick 为每个 Agent 决定下一步目标（如无 LLM 或 LLM 失败时）。
- 核心逻辑：按日程片段选择 location → 用 WorldService 寻路 → 生成 MOVE 动作。
- 动物：wander 随机目标；看到玩家靠近根据性格反应。

该模块只负责"生成动作意图"，不直接写数据库。
真正的世界推进由 SimulationEngine 执行。
"""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.domain.simulation.schedule import pick_current_slot
from app.domain.world.grid import SceneGrid, astar


@dataclass(slots=True)
class AgentDecision:
    action_type: str
    description: str
    target_position: tuple[int, int] | None = None
    target_scene_id: str | None = None
    target_location_id: str | None = None
    target_entity_id: str | None = None
    path: list[tuple[int, int]] | None = None
    duration_ticks: int = 1
    metadata: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.metadata is None:
            self.metadata = {}


# ---------------------------------------------------------------------------
# 辅助：目标格选取
# ---------------------------------------------------------------------------


def _choose_goal_tile_for_slot(
    slot_location_id: str | None,
    locations: dict[str, dict[str, Any]],
) -> tuple[str, tuple[int, int]] | None:
    if slot_location_id is None:
        return None
    loc = locations.get(slot_location_id)
    if loc is None:
        return None
    entries = loc.get("entry_tiles") or []
    if entries:
        entry = entries[0]
        return loc["scene_id"], (int(entry["x"]), int(entry["y"]))
    bounds = loc.get("bounds") or {}
    if not bounds:
        return None
    cx = int(bounds.get("x", 0) + bounds.get("width", 1) // 2)
    cy = int(bounds.get("y", 0) + bounds.get("height", 1) // 2)
    return loc["scene_id"], (cx, cy)


# ---------------------------------------------------------------------------
# 辅助：Portal 查找
# ---------------------------------------------------------------------------


def _find_portal_from_to(
    portals_by_scene: dict[str, list[Any]],
    from_scene: str,
    to_scene: str,
) -> tuple[int, int] | None:
    """找到从 from_scene 到 to_scene 的 portal，返回 from_tile 坐标。"""
    for portal in portals_by_scene.get(from_scene, []):
        if portal.to_scene_id == to_scene:
            ft = portal.from_tile or {}
            return int(ft.get("x", 0)), int(ft.get("y", 0))
    return None


# ---------------------------------------------------------------------------
# 辅助：寻路容错
# ---------------------------------------------------------------------------


def _nearest_walkable_near(
    grid: SceneGrid,
    center: tuple[int, int],
    *,
    radius: int = 4,
) -> tuple[int, int] | None:
    """曼哈顿距离由近到远，找第一个可行走格（避开危险）。"""
    for r in range(1, radius + 1):
        for dx in range(-r, r + 1):
            dy_abs = r - abs(dx)
            for dy in (dy_abs, -dy_abs) if dy_abs != 0 else (0,):
                nx, ny = center[0] + dx, center[1] + dy
                if grid.is_walkable(nx, ny, avoid_hazards=True):
                    return nx, ny
    return None


def _recovery_tile_bfs(
    grid: SceneGrid,
    origin: tuple[int, int],
    *,
    max_radius: int = 5,
) -> tuple[int, int] | None:
    """BFS 找到离 origin 最近的可走格，用于将卡住的 NPC 移出死区。"""
    visited: set[tuple[int, int]] = {origin}
    q: deque[tuple[int, int]] = deque([origin])
    while q:
        cur = q.popleft()
        if cur != origin and grid.is_walkable(*cur, avoid_hazards=True):
            return cur
        if abs(cur[0] - origin[0]) + abs(cur[1] - origin[1]) >= max_radius:
            continue
        for nx, ny in grid.neighbors(*cur):
            if (nx, ny) not in visited:
                visited.add((nx, ny))
                q.append((nx, ny))
    return None


def _astar_with_fallback(
    grid: SceneGrid,
    start: tuple[int, int],
    goal: tuple[int, int],
    *,
    fallback_radius: int = 4,
) -> list[tuple[int, int]]:
    """
    先尝试 A* 到 goal；若失败：
    1. 尝试 goal 周围 fallback_radius 格内最近可走格作为替代目标。
    2. 若仍失败返回空列表。
    """
    path = astar(grid, start, goal, avoid_hazards=True)
    if path:
        return path

    # 目标格本身不可走 —— 找最近可走邻格
    alt_goal = _nearest_walkable_near(grid, goal, radius=fallback_radius)
    if alt_goal is None or alt_goal == goal:
        return []
    return astar(grid, start, alt_goal, avoid_hazards=True)


# ---------------------------------------------------------------------------
# 人类 NPC 决策
# ---------------------------------------------------------------------------


def decide_human_action(
    *,
    agent_row: Any,
    state_row: Any,
    world_time: datetime,
    locations: dict[str, dict[str, Any]],
    grids: dict[str, SceneGrid],
    portals_by_scene: dict[str, list[Any]] | None = None,
) -> AgentDecision:
    """
    人类 NPC 规则决策。
    - 优先跟随日程。
    - 目标在同场景：A* 寻路，找不到路则尝试目标邻格，仍失败则小范围漫游找出路。
    - 目标在其他场景：先寻路到当前场景的对应 portal，抵达后引擎自动传送。
    - 无日程则待在原地。
    """
    _portals = portals_by_scene or {}

    tpl: list[dict[str, Any]] = agent_row.schedule_template or []
    slot = pick_current_slot(tpl, world_time.time())
    goal = _choose_goal_tile_for_slot(slot.location_id if slot else None, locations)
    if goal is None:
        return AgentDecision(action_type="wait", description="闲着")

    target_scene, target_tile = goal
    grid_current = grids.get(state_row.scene_id)

    # ------------------------------------------------------------------
    # 跨场景：先走到当前场景的 portal 入口
    # ------------------------------------------------------------------
    if target_scene != state_row.scene_id:
        portal_tile = _find_portal_from_to(_portals, state_row.scene_id, target_scene)
        if portal_tile is None:
            return AgentDecision(action_type="wait", description="无路通往目标场景")

        if grid_current is None:
            return AgentDecision(action_type="wait", description="等待世界加载")

        start = (state_row.x, state_row.y)
        if start == portal_tile:
            # 已站在 portal 上，等引擎传送
            return AgentDecision(
                action_type="interact",
                description=slot.description if slot else "进入建筑",
                target_location_id=slot.location_id if slot else None,
            )

        path = _astar_with_fallback(grid_current, start, portal_tile)
        if not path:
            # 走不到 portal —— 尝试小范围漫游解卡
            return _make_recovery_or_wait(
                grid_current, start,
                description="绕行中，寻找建筑入口",
                wait_description="无法抵达建筑入口，等待",
            )
        return AgentDecision(
            action_type="move_to_location",
            description=slot.description if slot else "前往目的地",
            target_scene_id=target_scene,
            target_position=target_tile,
            target_location_id=slot.location_id if slot else None,
            path=path,
            duration_ticks=len(path),
        )

    # ------------------------------------------------------------------
    # 同场景
    # ------------------------------------------------------------------
    if grid_current is None:
        return AgentDecision(action_type="wait", description="等待世界加载")

    start = (state_row.x, state_row.y)
    if start == target_tile:
        return AgentDecision(
            action_type="interact",
            description=slot.description if slot else "就位",
            target_location_id=slot.location_id if slot else None,
        )

    path = _astar_with_fallback(grid_current, start, target_tile)
    if not path:
        return _make_recovery_or_wait(
            grid_current, start,
            description="绕行寻路中",
            wait_description="路径不可达，等待",
        )
    return AgentDecision(
        action_type="move_to_location",
        description=slot.description if slot else "前往目的地",
        target_scene_id=target_scene,
        target_position=target_tile,
        target_location_id=slot.location_id if slot else None,
        path=path,
        duration_ticks=len(path),
    )


def _make_recovery_or_wait(
    grid: SceneGrid,
    origin: tuple[int, int],
    *,
    description: str,
    wait_description: str,
) -> AgentDecision:
    """
    尝试 BFS 找一个可走的邻格让 NPC 移出死区；
    完全走不通时才返回 wait。
    """
    recovery = _recovery_tile_bfs(grid, origin, max_radius=5)
    if recovery is not None:
        path = astar(grid, origin, recovery, avoid_hazards=True)
        if path:
            return AgentDecision(
                action_type="wander",
                description=description,
                target_position=recovery,
                path=path,
                duration_ticks=len(path),
            )
    return AgentDecision(action_type="wait", description=wait_description)


# ---------------------------------------------------------------------------
# 动物决策
# ---------------------------------------------------------------------------


def decide_animal_action(
    *,
    agent_row: Any,
    state_row: Any,
    grids: dict[str, SceneGrid],
    rng: random.Random,
    nearby_player: tuple[int, int] | None = None,
) -> AgentDecision:
    """动物规则决策：wander / approach / avoid / sleep。"""
    personality = set(agent_row.personality or [])
    grid = grids.get(state_row.scene_id)
    if grid is None:
        return AgentDecision(action_type="wait", description="困在世界外")

    # 休息概率：低精力时更倾向睡觉
    if state_row.energy < 0.2 and rng.random() < 0.6:
        return AgentDecision(action_type="sleep", description="打个盹", duration_ticks=6)

    if nearby_player is not None:
        if "fearful" in personality or "shy" in personality or "胆小" in personality:
            goal = _random_walkable(grid, rng, away_from=nearby_player, radius=5)
            if goal is not None:
                path = astar(grid, (state_row.x, state_row.y), goal, avoid_hazards=True)
                return AgentDecision(
                    action_type="avoid",
                    description="看到陌生身影，躲开",
                    target_position=goal,
                    path=path,
                )
        elif "friendly" in personality or "亲人" in personality:
            path = astar(
                grid, (state_row.x, state_row.y), nearby_player, avoid_hazards=True
            )
            if path:
                return AgentDecision(
                    action_type="approach",
                    description="兴奋地靠近",
                    target_position=nearby_player,
                    path=path[:3],
                )

    goal = _random_walkable(grid, rng, radius=4, origin=(state_row.x, state_row.y))
    if goal is None:
        return AgentDecision(action_type="wait", description="打量四周")
    path = astar(grid, (state_row.x, state_row.y), goal, avoid_hazards=True)
    if not path:
        return AgentDecision(action_type="wait", description="在原地嗅闻")
    return AgentDecision(
        action_type="wander",
        description="闲逛",
        target_position=goal,
        path=path,
        duration_ticks=len(path),
    )


def _random_walkable(
    grid: SceneGrid,
    rng: random.Random,
    *,
    radius: int = 5,
    origin: tuple[int, int] | None = None,
    away_from: tuple[int, int] | None = None,
) -> tuple[int, int] | None:
    for _ in range(16):
        if origin is None:
            x = rng.randrange(grid.width)
            y = rng.randrange(grid.height)
        else:
            x = origin[0] + rng.randint(-radius, radius)
            y = origin[1] + rng.randint(-radius, radius)
        if not grid.is_walkable(x, y, avoid_hazards=True):
            continue
        if away_from is not None:
            if abs(x - away_from[0]) + abs(y - away_from[1]) < radius:
                continue
        return (x, y)
    return None
