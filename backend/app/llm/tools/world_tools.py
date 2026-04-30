"""
世界类工具：move_to_location / move_to_entity / interact_with_object / avoid_danger。

这些工具负责产生 AgentAction，修改引擎内存中的 path，但不直接改数据库。
数据库持久化由 SimulationEngine 的 tick 循环统一处理。
"""

from __future__ import annotations

import uuid
from typing import Any, Iterable

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from app.core.time import utcnow
from app.db.models import AgentAction, AgentState, Location, WorldObject
from app.domain.world.grid import astar
from app.domain.world.scene_cache import get_scene_cache
from app.llm.tools.base import Tool, ToolCall, ToolContext, ToolResult, ToolSpec


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _entry_tile(location: Location) -> tuple[int, int] | None:
    entries: list[dict[str, int]] = location.entry_tiles or []
    if entries:
        e = entries[0]
        return int(e["x"]), int(e["y"])
    bounds = location.bounds or {}
    if not bounds:
        return None
    cx = int(bounds["x"]) + int(bounds["width"]) // 2
    cy = int(bounds["y"]) + int(bounds["height"]) // 2
    return cx, cy


async def _apply_path_to_engine(
    ctx: ToolContext,
    path: list[tuple[int, int]],
    description: str,
    action_type: str,
    *,
    target_location_id: str | None = None,
    target_entity_id: str | None = None,
    target_object_id: str | None = None,
) -> str:
    """
    把路径应用到引擎内存态，并写入 agent_actions 一条记录。
    返回 action_id。
    """
    from app.services.simulation_runtime import get_simulation_runtime

    engine = get_simulation_runtime().engine
    agent = engine.get_agent(ctx.agent_id)
    if agent is not None:
        # 首元素如果等于当前位置则剔除
        trimmed = path
        if trimmed and trimmed[0] == (agent.x, agent.y):
            trimmed = trimmed[1:]
        agent.path = list(trimmed)
        agent.state = "MOVING" if trimmed else "INTERACTING"
        agent.current_goal = description
        agent.last_decision_at = ctx.world_time
        agent.dirty = True

    action = AgentAction(
        id=str(uuid.uuid4()),
        agent_id=ctx.agent_id,
        action_type=action_type,
        description=description,
        target_location_id=target_location_id,
        target_entity_id=target_entity_id,
        target_object_id=target_object_id,
        status="running" if path else "finished",
        action_metadata={"path_len": len(path)},
    )
    ctx.session.add(action)
    if agent is not None:
        agent.current_action_id = action.id
    await ctx.session.flush()
    return action.id


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


class _MoveToLocationArgs(BaseModel):
    location_id: str = Field(..., description="目标地点 id")
    reason: str = Field(default="", description="内心原因")


class MoveToLocationTool(Tool):
    name = "move_to_location"

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="让当前 Agent 按地图寻路，走到指定 Location。",
            owner_module="world",
            allowed_entity_types=["human", "animal", "player"],
            parameters_schema={
                "type": "object",
                "properties": {
                    "location_id": {"type": "string", "description": "目标地点 id"},
                    "reason": {"type": "string", "description": "原因或目的"},
                },
                "required": ["location_id"],
            },
        )

    async def execute(self, ctx: ToolContext, call: ToolCall) -> ToolResult:
        try:
            args = _MoveToLocationArgs.model_validate(call.arguments)
        except ValidationError as e:
            return self.fail(call, "INVALID_ARGUMENTS", str(e))

        loc = await ctx.session.get(Location, args.location_id)
        if loc is None:
            return self.fail(call, "TARGET_NOT_FOUND", f"location {args.location_id} not found")
        entry = _entry_tile(loc)
        if entry is None:
            return self.fail(call, "TARGET_NOT_REACHABLE", "location has no entry tile")

        cache = get_scene_cache()
        if loc.scene_id != ctx.scene_id:
            # 跨场景：AI 决策先走到最近的 portal；MVP 做法：直接更新 agent 场景 id 由移动时通过 portal 自动切换。
            # 为简化：请先在同场景内选择建筑入口。若跨场景，引擎会通过 portal 触发切换。
            # 这里直接让 agent 在当前场景继续寻路到本场景的 portal from_tile（以 portal 最小匹配）。
            portal_from = await _nearest_portal_to(ctx, loc.scene_id)
            if portal_from is None:
                return self.fail(
                    call,
                    "TARGET_NOT_REACHABLE",
                    "no portal from current scene to target scene",
                    retryable=False,
                )
            entry = portal_from
            target_scene_id = ctx.scene_id
        else:
            target_scene_id = ctx.scene_id

        grid = cache.get_grid(target_scene_id)
        if grid is None:
            return self.fail(call, "INTERNAL_ERROR", "scene grid not loaded")
        path = astar(grid, ctx.position, entry, avoid_hazards=True)
        if not path:
            return self.fail(call, "TARGET_NOT_REACHABLE", "no valid path", retryable=True)

        action_id = await _apply_path_to_engine(
            ctx,
            path,
            description=args.reason or f"前往 {loc.name}",
            action_type="move_to_location",
            target_location_id=args.location_id,
        )
        return self.ok(
            call,
            {
                "action_id": action_id,
                "path_len": max(len(path) - 1, 0),
                "target_tile": {"x": entry[0], "y": entry[1]},
            },
        )


async def _nearest_portal_to(
    ctx: ToolContext, target_scene_id: str
) -> tuple[int, int] | None:
    from app.db.models import Portal

    rows = (
        await ctx.session.execute(
            select(Portal).where(
                Portal.from_scene_id == ctx.scene_id,
                Portal.to_scene_id == target_scene_id,
            )
        )
    ).scalars().all()
    if not rows:
        return None
    r0 = rows[0]
    ft = r0.from_tile or {}
    return int(ft.get("x", 0)), int(ft.get("y", 0))


class _MoveToEntityArgs(BaseModel):
    target_entity_id: str
    distance: int = Field(default=1, ge=1, le=5)
    reason: str = ""


class MoveToEntityTool(Tool):
    name = "move_to_entity"

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="走到另一个实体附近（玩家/NPC/动物）。",
            owner_module="world",
            allowed_entity_types=["human", "animal", "player"],
            parameters_schema={
                "type": "object",
                "properties": {
                    "target_entity_id": {"type": "string"},
                    "distance": {"type": "integer", "minimum": 1, "maximum": 5},
                    "reason": {"type": "string"},
                },
                "required": ["target_entity_id"],
            },
        )

    async def execute(self, ctx: ToolContext, call: ToolCall) -> ToolResult:
        try:
            args = _MoveToEntityArgs.model_validate(call.arguments)
        except ValidationError as e:
            return self.fail(call, "INVALID_ARGUMENTS", str(e))

        target = await ctx.session.get(AgentState, args.target_entity_id)
        if target is None:
            return self.fail(call, "TARGET_NOT_FOUND", "entity not found")
        if target.scene_id != ctx.scene_id:
            return self.fail(
                call,
                "TARGET_NOT_REACHABLE",
                "target in another scene",
                retryable=False,
            )

        grid = get_scene_cache().get_grid(ctx.scene_id)
        if grid is None:
            return self.fail(call, "INTERNAL_ERROR", "scene grid not loaded")

        # 取靠近 target 的可走 tile
        goal = _find_free_adjacent(grid, (target.x, target.y), args.distance)
        if goal is None:
            return self.fail(call, "TARGET_NOT_REACHABLE", "no free tile near target")
        path = astar(grid, ctx.position, goal, avoid_hazards=True)
        if not path:
            return self.fail(call, "TARGET_NOT_REACHABLE", "no valid path", retryable=True)
        action_id = await _apply_path_to_engine(
            ctx,
            path,
            description=args.reason or f"走向 {args.target_entity_id}",
            action_type="move_to_entity",
            target_entity_id=args.target_entity_id,
        )
        return self.ok(call, {"action_id": action_id, "path_len": len(path) - 1})


def _find_free_adjacent(
    grid, center: tuple[int, int], radius: int
) -> tuple[int, int] | None:
    from itertools import product

    for r in range(radius, 0, -1):
        for dx, dy in product(range(-r, r + 1), range(-r, r + 1)):
            if abs(dx) + abs(dy) != r:
                continue
            nx, ny = center[0] + dx, center[1] + dy
            if grid.is_walkable(nx, ny, avoid_hazards=True):
                return nx, ny
    return None


class _InteractObjectArgs(BaseModel):
    object_id: str
    interaction_type: str
    reason: str = ""


class InteractWithObjectTool(Tool):
    name = "interact_with_object"

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="对某个 WorldObject 执行指定交互。",
            owner_module="world",
            allowed_entity_types=["human", "player"],
            parameters_schema={
                "type": "object",
                "properties": {
                    "object_id": {"type": "string"},
                    "interaction_type": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["object_id", "interaction_type"],
            },
        )

    async def execute(self, ctx: ToolContext, call: ToolCall) -> ToolResult:
        try:
            args = _InteractObjectArgs.model_validate(call.arguments)
        except ValidationError as e:
            return self.fail(call, "INVALID_ARGUMENTS", str(e))

        obj = await ctx.session.get(WorldObject, args.object_id)
        if obj is None:
            return self.fail(call, "TARGET_NOT_FOUND", "object not found")
        if args.interaction_type not in (obj.available_interactions or []):
            return self.fail(
                call,
                "INVALID_ARGUMENTS",
                f"{args.interaction_type} not in {obj.available_interactions}",
            )
        pos = obj.position or {}
        ox, oy = int(pos.get("x", 0)), int(pos.get("y", 0))
        if abs(ox - ctx.position[0]) + abs(oy - ctx.position[1]) > 2:
            return self.fail(call, "OUT_OF_RANGE", "too far", retryable=True)

        # 写事件（由 Executor 将候选事件转换为 WorldEvent）
        return ToolResult(
            tool=call.tool,
            success=True,
            result={"object_id": obj.id, "interaction": args.interaction_type},
            memory_candidates=[
                {
                    "memory_type": "event",
                    "scope": "short_term",
                    "description": f"使用了 {obj.name} 执行 {args.interaction_type}",
                    "importance": 3,
                    "keywords": [obj.name, args.interaction_type],
                }
            ],
        )


class _AvoidDangerArgs(BaseModel):
    hazard_type: str = "deep_water"
    reason: str = ""


class AvoidDangerTool(Tool):
    name = "avoid_danger"

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="离开当前危险区域，移动到最近的安全 tile。",
            owner_module="world",
            allowed_entity_types=["human", "animal", "player"],
            parameters_schema={
                "type": "object",
                "properties": {
                    "hazard_type": {"type": "string"},
                    "reason": {"type": "string"},
                },
            },
        )

    async def execute(self, ctx: ToolContext, call: ToolCall) -> ToolResult:
        try:
            args = _AvoidDangerArgs.model_validate(call.arguments)
        except ValidationError as e:
            return self.fail(call, "INVALID_ARGUMENTS", str(e))

        grid = get_scene_cache().get_grid(ctx.scene_id)
        if grid is None:
            return self.fail(call, "INTERNAL_ERROR", "scene grid not loaded")

        safe = _breadth_first_safe_tile(grid, ctx.position)
        if safe is None:
            return self.fail(call, "TARGET_NOT_REACHABLE", "no safe tile nearby")
        path = astar(grid, ctx.position, safe, avoid_hazards=True)
        if not path:
            return self.fail(call, "TARGET_NOT_REACHABLE", "no valid path")
        action_id = await _apply_path_to_engine(
            ctx,
            path,
            description=args.reason or "逃离危险",
            action_type="avoid_danger",
        )
        return self.ok(
            call,
            {"action_id": action_id, "safe_tile": {"x": safe[0], "y": safe[1]}},
        )


def _breadth_first_safe_tile(
    grid, start: tuple[int, int], *, max_radius: int = 20
) -> tuple[int, int] | None:
    from collections import deque

    visited: set[tuple[int, int]] = {start}
    q: deque[tuple[int, int]] = deque([start])
    while q:
        cur = q.popleft()
        tile = grid.get(*cur)
        if cur != start and grid.is_walkable(*cur, avoid_hazards=True) and (
            tile is None or tile.hazard_type is None
        ):
            return cur
        for nx, ny in grid.neighbors(*cur):
            if (nx, ny) in visited:
                continue
            if abs(nx - start[0]) + abs(ny - start[1]) > max_radius:
                continue
            if not grid.in_bounds(nx, ny):
                continue
            visited.add((nx, ny))
            q.append((nx, ny))
    return None


def build_tools() -> Iterable[Tool]:
    return [
        MoveToLocationTool(),
        MoveToEntityTool(),
        InteractWithObjectTool(),
        AvoidDangerTool(),
    ]
