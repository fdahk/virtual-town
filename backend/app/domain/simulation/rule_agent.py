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
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.domain.simulation.schedule import pick_current_slot
from app.domain.world.grid import SceneGrid, astar


@dataclass
class NaturalWorldContext:
    """
    自然环境上下文，由引擎从 EngineObject 缓存中构建后传给规则决策。
    使用简单类型避免循环依赖（rule_agent 不导入 engine）。
    """

    weather: str = "sunny"
    storm_active: bool = False
    # (x, y, name) 列表
    nearby_fires: list[tuple[int, int, str]] = field(default_factory=list)
    nearby_fishing_spots: list[tuple[int, int, str]] = field(default_factory=list)
    nearby_benches: list[tuple[int, int, str]] = field(default_factory=list)
    nearby_ripe_objects: list[tuple[int, int, str]] = field(default_factory=list)
    # (x, y, id, notice_text)
    nearby_signs: list[tuple[int, int, str, str]] = field(default_factory=list)


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
    """找到从 from_scene 到 to_scene 的直连 portal，返回 from_tile 坐标。"""
    for portal in portals_by_scene.get(from_scene, []):
        if portal.to_scene_id == to_scene:
            ft = portal.from_tile or {}
            return int(ft.get("x", 0)), int(ft.get("y", 0))
    return None


def _find_inter_scene_route(
    portals_by_scene: dict[str, list[Any]],
    from_scene: str,
    to_scene: str,
    *,
    max_hops: int = 4,
) -> list[str] | None:
    """BFS 跨场景路由：返回 ``[next_scene, ..., to_scene]``（不含起点）。

    针对"室内场景 → 通过 outdoor 中转 → 另一室内场景"这种典型布局：
    - `school_inside → outdoor → cafe_inside` 时，单跳查找会失败导致 NPC 僵死，
      BFS 能找到完整链路并返回 `[outdoor, cafe_inside]`。
    - 没有可达路径时返回 None。
    """
    if from_scene == to_scene:
        return []
    visited: set[str] = {from_scene}
    queue: deque[tuple[str, list[str]]] = deque([(from_scene, [])])
    while queue:
        cur, path = queue.popleft()
        if len(path) >= max_hops:
            continue
        for portal in portals_by_scene.get(cur, []):
            nxt = portal.to_scene_id
            if nxt in visited:
                continue
            new_path = path + [nxt]
            if nxt == to_scene:
                return new_path
            visited.add(nxt)
            queue.append((nxt, new_path))
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
    max_radius: int = 8,
) -> tuple[int, int] | None:
    """BFS 找到离 origin 最近的可走格，用于将卡住的 NPC 移出死区。

    ``max_radius`` 默认 8（曾经 5）：5 在围栏 / 大型障碍物背后的角色容易
    BFS 不到任何可走格，被迫返回 stuck=True 的 wait，导致 NPC 站在
    河岸 / 农田边缘僵住。8 给了足够余量绕过单条围栏 / 一道墙体。
    """
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
    natural_ctx: NaturalWorldContext | None = None,
    unreachable_location_ids: set[str] | None = None,
) -> AgentDecision:
    """
    人类 NPC 规则决策。

    优先级：
    1. 暴风雨自保（storm_active）→ 跑向最近室内
    2. 火灾响应（nearby_fires）→ 按性格决定救援/逃离/围观
    3. 正常日程（schedule_template）

    参数 ``unreachable_location_ids``：引擎传入本 agent 当前的不可达目标黑名单
    （由近期 stuck 决策聚合而成，TTL 5 分钟）。命中黑名单时会跳过该日程槽位，
    转为在当前场景漫游，避免反复回到同一个无法到达的目标。
    """
    _portals = portals_by_scene or {}
    nat = natural_ctx or NaturalWorldContext()
    blacklist = unreachable_location_ids or set()

    # ── 1. 暴风雨优先逃入建筑 ────────────────────────────────────────────────
    if nat.storm_active:
        override = _decide_storm_shelter(agent_row, state_row, locations, grids, _portals)
        if override is not None:
            return override

    # ── 2. 火灾响应 ───────────────────────────────────────────────────────────
    if nat.nearby_fires:
        override = _decide_fire_response(agent_row, state_row, nat, grids)
        if override is not None:
            return override

    tpl: list[dict[str, Any]] = agent_row.schedule_template or []
    slot = pick_current_slot(tpl, world_time.time())
    slot_loc = slot.location_id if slot else None
    if slot_loc and slot_loc in blacklist:
        # 当前日程目标被标记为不可达 → 不再尝试，转为现场漫游
        return _wander_within_scene_or_wait(
            state_row, grids, rng=random.Random(state_row.x * 31 + state_row.y),
            description=f"目标 {slot_loc} 暂时不可达，先在附近闲逛",
        )
    goal = _choose_goal_tile_for_slot(slot_loc, locations)
    if goal is None:
        # 没有日程目标：在当前场景漫游一会儿，避免长期僵直
        return _wander_within_scene_or_wait(
            state_row, grids, rng=random.Random(state_row.x * 31 + state_row.y),
            description="日程间隙，随便走走",
        )

    target_scene, target_tile = goal
    grid_current = grids.get(state_row.scene_id)

    # ------------------------------------------------------------------
    # 跨场景：先走到当前场景的 portal 入口（支持多跳中转）
    # ------------------------------------------------------------------
    if target_scene != state_row.scene_id:
        # 1) 直连优先，2) 直连不存在 → BFS 找多跳路径
        portal_tile = _find_portal_from_to(_portals, state_row.scene_id, target_scene)
        next_scene_for_portal = target_scene
        if portal_tile is None:
            route = _find_inter_scene_route(_portals, state_row.scene_id, target_scene)
            if route:
                next_scene_for_portal = route[0]
                portal_tile = _find_portal_from_to(
                    _portals, state_row.scene_id, next_scene_for_portal
                )

        if portal_tile is None:
            # 完全没路（异常拓扑 / 缺数据）→ 不再死等：标记不可达 + 在当前场景漫游
            return _wander_within_scene_or_wait(
                state_row, grids,
                rng=random.Random(state_row.x * 31 + state_row.y),
                description="目标场景暂时无路可达，先在附近转转",
                stuck_target_location_id=slot.location_id if slot else None,
            )

        if grid_current is None:
            return AgentDecision(
                action_type="wait",
                description="等待世界加载",
                metadata={"stuck": True},
            )

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
                wait_description="无法抵达建筑入口，先在附近转转",
                stuck_target_location_id=slot.location_id if slot else None,
            )
        return AgentDecision(
            action_type="move_to_location",
            description=slot.description if slot else "前往目的地",
            target_scene_id=next_scene_for_portal,
            target_position=target_tile,
            target_location_id=slot.location_id if slot else None,
            path=path,
            duration_ticks=len(path),
        )

    # ------------------------------------------------------------------
    # 同场景
    # ------------------------------------------------------------------
    if grid_current is None:
        return AgentDecision(
            action_type="wait", description="等待世界加载",
            metadata={"stuck": True},
        )

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
            wait_description="路径暂时不可达，先在附近转转",
            stuck_target_location_id=slot.location_id if slot else None,
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


def _decide_fire_response(
    agent_row: Any,
    state_row: Any,
    nat: NaturalWorldContext,
    grids: dict[str, SceneGrid],
) -> AgentDecision | None:
    """
    根据性格决定火灾响应：
    - 勇敢(brave/courageous/hero)   → 冲向最近的火源帮助救火
    - 胆小(cowardly/fearful/timid)  → 向反方向逃跑
    - 其他                          → 围观（原地不动，交还日程决策）
    """
    personality: list[str] = agent_row.personality or []
    brave_kw = {"brave", "courageous", "hero", "勇敢", "英雄"}
    timid_kw = {"cowardly", "fearful", "timid", "scared", "胆小", "懦弱", "怕事"}

    is_brave = any(p.lower() in brave_kw for p in personality)
    is_timid = any(p.lower() in timid_kw for p in personality)

    grid = grids.get(state_row.scene_id)
    start = (state_row.x, state_row.y)
    fx, fy, fname = nat.nearby_fires[0]  # 最近火源

    if is_brave:
        # 试图接近火源（勇敢角色）
        if grid is not None:
            path = _astar_with_fallback(grid, start, (fx, fy), fallback_radius=3)
            if path:
                return AgentDecision(
                    action_type="move_to_location",
                    description=f"勇敢地冲向 {fname} 救火",
                    target_position=(fx, fy),
                    path=path,
                    duration_ticks=len(path),
                )
    elif is_timid:
        # 向反方向逃跑
        if grid is not None:
            dx = state_row.x - fx
            dy = state_row.y - fy
            mag = max(abs(dx) + abs(dy), 1)
            flee_x = state_row.x + int(dx / mag * 6)
            flee_y = state_row.y + int(dy / mag * 6)
            path = _astar_with_fallback(grid, start, (flee_x, flee_y), fallback_radius=4)
            if path:
                return AgentDecision(
                    action_type="move_to_location",
                    description=f"被 {fname} 的火焰吓到，拼命逃跑",
                    target_position=(flee_x, flee_y),
                    path=path,
                    duration_ticks=len(path),
                )
    # 中立性格：围观，交还给日程决策
    return None


def _decide_storm_shelter(
    agent_row: Any,
    state_row: Any,
    locations: dict[str, dict[str, Any]],
    grids: dict[str, SceneGrid],
    portals_by_scene: dict[str, list[Any]],
) -> AgentDecision | None:
    """暴风雨时找最近的室内地点。"""
    indoor_locs = [
        loc for loc in locations.values()
        if loc.get("scene_id") != state_row.scene_id  # 在其他（室内）场景
        and loc.get("location_type") in ("indoor", "building", "home", "cafe", "school")
    ]
    if not indoor_locs:
        return None
    # 找到 portal 通向室内
    portal_tile = None
    target_scene = None
    for loc in indoor_locs:
        portal_tile = _find_portal_from_to(portals_by_scene, state_row.scene_id, loc["scene_id"])
        if portal_tile is not None:
            target_scene = loc["scene_id"]
            break
    if portal_tile is None:
        return None
    grid = grids.get(state_row.scene_id)
    if grid is None:
        return None
    start = (state_row.x, state_row.y)
    path = _astar_with_fallback(grid, start, portal_tile)
    if not path:
        return None
    return AgentDecision(
        action_type="move_to_location",
        description="暴风雨来袭，赶快躲进建筑里",
        target_scene_id=target_scene,
        target_position=portal_tile,
        path=path,
        duration_ticks=len(path),
    )


def _make_recovery_or_wait(
    grid: SceneGrid,
    origin: tuple[int, int],
    *,
    description: str,
    wait_description: str,
    stuck_target_location_id: str | None = None,
) -> AgentDecision:
    """
    尝试 BFS 找一个可走的邻格让 NPC 移出死区；
    完全走不通时才返回 wait。

    无论走 wander 还是 wait 分支：只要 ``stuck_target_location_id`` 给出，
    都会通过 ``metadata.unreachable_location_id`` 把目标加入引擎的 5 分钟
    不可达黑名单，避免下个 ai_tick 又选同一个目标再次失败。

    - **wait 分支**额外打 ``metadata.stuck=True``：完全卡死 → 引擎清
      ``last_decision_at`` 立即重决策，避免长期僵在 WAITING。
    - **wander 分支**不打 ``stuck`` 标记：NPC 已经在动，按正常 ai_tick
      节奏重决策即可，黑名单已经会让下次决策避开该目标。
    """
    recovery = _recovery_tile_bfs(grid, origin, max_radius=8)
    if recovery is not None:
        path = astar(grid, origin, recovery, avoid_hazards=True)
        if path:
            metadata: dict[str, Any] = {}
            if stuck_target_location_id:
                # 没找到通往真正目标的路径，但能就近兜个圈 ——
                # 标记目标 location 不可达，避免下个 ai_tick 又原样失败
                metadata["unreachable_location_id"] = stuck_target_location_id
            return AgentDecision(
                action_type="wander",
                description=description,
                target_position=recovery,
                path=path,
                duration_ticks=len(path),
                metadata=metadata,
            )
    metadata = {"stuck": True}
    if stuck_target_location_id:
        metadata["unreachable_location_id"] = stuck_target_location_id
    return AgentDecision(
        action_type="wait", description=wait_description, metadata=metadata,
    )


def _wander_within_scene_or_wait(
    state_row: Any,
    grids: dict[str, SceneGrid],
    *,
    rng: random.Random,
    description: str,
    stuck_target_location_id: str | None = None,
    radius: int = 5,
) -> AgentDecision:
    """日程目标暂时不可达时的兜底：在当前场景内挑一个可走的随机格漫步。

    相比 ``wait``，至少 NPC 在动 → 可能撞见别人触发邂逅、进入新位置触发感知事件。
    """
    grid = grids.get(state_row.scene_id)
    if grid is None:
        metadata = {"stuck": True}
        if stuck_target_location_id:
            metadata["unreachable_location_id"] = stuck_target_location_id
        return AgentDecision(
            action_type="wait", description="等待世界加载", metadata=metadata,
        )
    origin = (state_row.x, state_row.y)
    goal = _random_walkable(grid, rng, radius=radius, origin=origin)
    if goal is None or goal == origin:
        metadata = {"stuck": True}
        if stuck_target_location_id:
            metadata["unreachable_location_id"] = stuck_target_location_id
        return AgentDecision(
            action_type="wait", description=description, metadata=metadata,
        )
    path = astar(grid, origin, goal, avoid_hazards=True)
    if not path:
        return _make_recovery_or_wait(
            grid, origin,
            description=description, wait_description=description,
            stuck_target_location_id=stuck_target_location_id,
        )
    return AgentDecision(
        action_type="wander",
        description=description,
        target_position=goal,
        path=path,
        duration_ticks=len(path),
        metadata=(
            {"unreachable_location_id": stuck_target_location_id}
            if stuck_target_location_id
            else {}
        ),
    )


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
