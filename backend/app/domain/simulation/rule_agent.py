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


def decide_human_action(
    *,
    agent_row: Any,
    state_row: Any,
    world_time: datetime,
    locations: dict[str, dict[str, Any]],
    grids: dict[str, SceneGrid],
) -> AgentDecision:
    """
    人类 NPC 规则决策。
    - 优先跟随日程。
    - 无日程则待在原地。
    - 已在目标地点附近则 idle。
    """
    tpl: list[dict[str, Any]] = agent_row.schedule_template or []
    slot = pick_current_slot(tpl, world_time.time())
    goal = _choose_goal_tile_for_slot(slot.location_id if slot else None, locations)
    if goal is None:
        return AgentDecision(action_type="wait", description="闲着")

    target_scene, target_tile = goal
    # 跨场景：先回到室外，再寻路到目标建筑入口
    if target_scene != state_row.scene_id:
        return AgentDecision(
            action_type="move_to_location",
            description=f"前往 {slot.description if slot else ''}".strip() or "赶往目标",
            target_scene_id=target_scene,
            target_position=target_tile,
            target_location_id=slot.location_id if slot else None,
            metadata={"cross_scene": True},
        )

    grid = grids.get(target_scene)
    if grid is None:
        return AgentDecision(action_type="wait", description="等待世界加载")
    start = (state_row.x, state_row.y)
    if start == target_tile:
        return AgentDecision(
            action_type="interact",
            description=slot.description if slot else "就位",
            target_location_id=slot.location_id if slot else None,
        )
    path = astar(grid, start, target_tile, avoid_hazards=True)
    if not path:
        return AgentDecision(action_type="wait", description="路径不可达，等待")
    return AgentDecision(
        action_type="move_to_location",
        description=slot.description if slot else "前往目的地",
        target_scene_id=target_scene,
        target_position=target_tile,
        target_location_id=slot.location_id if slot else None,
        path=path,
        duration_ticks=len(path),
    )


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
