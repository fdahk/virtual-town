"""
可观测性 REST API（阶段 14.3）。

所有路径挂在 ``/api/observability`` 下。普通玩家 UI 不访问这里。

- ``GET  /dashboard``                     — 实时聚合指标
- ``GET  /events``                         — 观测事件（可筛 category / trace_id / level）
- ``GET  /world-events``                   — 世界事件流（可筛类型 / 实体 / 场景）
- ``GET  /agents/{agent_id}/runtime``      — Agent 运行视图（状态 + 最近事件 + 最近记忆）
- ``GET  /traces/{trace_id}``              — trace 完整 span 树
- ``GET  /llm-calls``                      — LLM 调用列表
- ``GET  /tool-calls``                     — Tool 调用列表
- ``GET  /tasks``                          — 任务队列列表
- ``GET  /memories``                       — 记忆列表
- ``GET  /errors``                         — 错误中心聚合

健康检查：``GET /health/db``、``/health/redis``、``/health/llm``
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import TargetNotFound
from app.core.logging import get_logger
from app.core.redis_client import get_redis
from app.core.time import utcnow
from app.db.models import (
    Agent,
    AgentState,
    LLMCallRecord,
    Memory,
    ObservabilityEvent,
    Task,
    ToolCallRecord,
    WorldEvent,
)
from app.db.session import get_session
from app.services.observer import (
    CATEGORY_AGENT_DECISION,
    CATEGORY_ERROR,
    CATEGORY_LLM_CALL,
    CATEGORY_PLAYER_QUERY,
    CATEGORY_TOOL_CALL,
    CATEGORY_WORLD_EVENT,
)
from app.services.simulation_runtime import get_simulation_runtime
from app.websocket.gateway import get_connection_manager
from app.websocket.observability import get_observability_manager

logger = get_logger(__name__)

router = APIRouter(prefix="/observability", tags=["observability"])


# -----------------------------------------------------------------------------
# Dashboard
# -----------------------------------------------------------------------------


@router.get("/dashboard")
async def get_dashboard(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    now = utcnow()
    window_start = now - timedelta(minutes=5)
    sim = get_simulation_runtime().engine.get_simulation()

    # LLM 聚合
    llm_stmt = select(
        func.count(LLMCallRecord.id),
        func.avg(LLMCallRecord.latency_ms),
        func.sum(func.cast(LLMCallRecord.success == False, type_=__import__("sqlalchemy").Integer)),  # noqa: E712
    ).where(LLMCallRecord.created_at >= window_start)
    total_llm, avg_latency, failed_llm = (await session.execute(llm_stmt)).one()

    # 任务聚合
    task_stmt = select(Task.status, func.count(Task.id)).group_by(Task.status)
    task_counts = {
        row[0]: int(row[1]) for row in (await session.execute(task_stmt)).all()
    }

    # 错误聚合（最近 5 分钟）
    err_stmt = select(func.count(ObservabilityEvent.id)).where(
        and_(
            ObservabilityEvent.category == CATEGORY_ERROR,
            ObservabilityEvent.created_at >= window_start,
        )
    )
    error_count = int((await session.execute(err_stmt)).scalar() or 0)

    # WS
    sim_id = sim.id if sim else None
    online = get_connection_manager().total(sim_id) if sim_id else 0
    obs_online = get_observability_manager().total(sim_id) if sim_id else 0

    return {
        "simulation": {
            "id": sim_id,
            "status": sim.status if sim else None,
            "step": sim.current_step if sim else None,
            "speed": sim.speed_multiplier if sim else None,
            "world_time": sim.world_time.isoformat() if sim else None,
        },
        "websocket": {
            "simulation_clients": online,
            "observability_clients": obs_online,
        },
        "llm_calls_last_5m": {
            "total": int(total_llm or 0),
            "avg_latency_ms": float(avg_latency or 0.0),
            "failed": int(failed_llm or 0),
            "error_rate": (
                float(failed_llm or 0) / float(total_llm)
                if total_llm
                else 0.0
            ),
        },
        "tasks": task_counts,
        "errors_last_5m": error_count,
        "generated_at": now.isoformat(),
    }


# -----------------------------------------------------------------------------
# Events
# -----------------------------------------------------------------------------


@router.get("/events")
async def list_events(
    session: AsyncSession = Depends(get_session),
    category: str | None = Query(None),
    trace_id: str | None = Query(None),
    level: str | None = Query(None),
    simulation_id: str | None = Query(None),
    entity_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    stmt = select(ObservabilityEvent).order_by(desc(ObservabilityEvent.created_at)).limit(limit)
    if category:
        stmt = stmt.where(ObservabilityEvent.category == category)
    if trace_id:
        stmt = stmt.where(ObservabilityEvent.trace_id == trace_id)
    if level:
        stmt = stmt.where(ObservabilityEvent.level == level)
    if simulation_id:
        stmt = stmt.where(ObservabilityEvent.simulation_id == simulation_id)
    if entity_id:
        stmt = stmt.where(ObservabilityEvent.entity_id == entity_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [_serialize_obs_event(r) for r in rows]


@router.get("/world-events")
async def list_world_events(
    session: AsyncSession = Depends(get_session),
    simulation_id: str | None = Query(None),
    event_type: str | None = Query(None),
    scene_id: str | None = Query(None),
    entity_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    stmt = select(WorldEvent).order_by(desc(WorldEvent.created_at)).limit(limit)
    if simulation_id:
        stmt = stmt.where(WorldEvent.simulation_id == simulation_id)
    if event_type:
        stmt = stmt.where(WorldEvent.event_type == event_type)
    if scene_id:
        stmt = stmt.where(WorldEvent.scene_id == scene_id)
    if entity_id:
        stmt = stmt.where(
            (WorldEvent.actor_entity_id == entity_id)
            | (WorldEvent.target_entity_id == entity_id)
        )
    rows = (await session.execute(stmt)).scalars().all()
    return [_serialize_world_event(r) for r in rows]


@router.get("/agents/{agent_id}/runtime")
async def agent_runtime(
    agent_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise TargetNotFound(f"agent {agent_id} not found")
    state = await session.get(AgentState, agent_id)

    # 最近 20 条事件
    events_stmt = (
        select(ObservabilityEvent)
        .where(ObservabilityEvent.entity_id == agent_id)
        .order_by(desc(ObservabilityEvent.created_at))
        .limit(20)
    )
    events = [_serialize_obs_event(r) for r in (await session.execute(events_stmt)).scalars().all()]

    # 最近 10 条记忆
    mem_stmt = (
        select(Memory)
        .where(Memory.agent_id == agent_id)
        .order_by(desc(Memory.created_at))
        .limit(10)
    )
    memories = [_serialize_memory(r) for r in (await session.execute(mem_stmt)).scalars().all()]

    # Redis runtime 热数据
    from app.core.redis_client import key_agent_runtime

    cache = await get_redis().get_json(key_agent_runtime(agent_id))

    return {
        "agent": {
            "id": agent.id,
            "name": agent.name,
            "entity_type": agent.entity_type,
            "personality": agent.personality,
            "occupation": agent.occupation,
        },
        "state": (
            {
                "scene_id": state.scene_id,
                "x": state.x,
                "y": state.y,
                "state": state.state,
                "emotion": state.emotion,
                "energy": state.energy,
                "hunger": state.hunger,
                "social_need": state.social_need,
                "fear": state.fear,
                "status_effects": state.status_effects,
                "current_goal": state.current_goal,
                "facing": state.facing,
                "path": state.path,
            }
            if state is not None
            else None
        ),
        "redis_runtime": cache,
        "recent_events": events,
        "recent_memories": memories,
    }


# -----------------------------------------------------------------------------
# Path debug：复盘"NPC 走不到目标"的一站式诊断
# -----------------------------------------------------------------------------


@router.get("/agents/{agent_id}/path-debug")
async def agent_path_debug(
    agent_id: str,
    target_location_id: str | None = Query(
        None,
        description=(
            "可选。不传则用 NPC 当前 schedule 的 slot.location_id；"
            "传入则强制以该 location 为目标排查。"
        ),
    ),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """一次返回排查"NPC 路径不可达"所需的全部信息：

    - NPC 当前位置 + schedule 当前 slot；
    - 目标 location 的 entry tile + walkable 状态（含/不含 hazard 两种）；
    - 跨场景 portal 链；
    - 多段 A* 实际结果（含/不含 hazard 两种）；
    - Redis 不可达黑名单镜像；
    - 自动诊断结论。

    用法：``GET /api/observability/agents/{npc_id}/path-debug``
    """
    from app.core.redis_client import key_agent_unreachable
    from app.core.time import utcnow as _utcnow
    from app.db.models import Location, Portal
    from app.domain.simulation.rule_agent import (
        _choose_goal_tile_for_slot,
        _find_inter_scene_route,
        _find_portal_from_to,
    )
    from app.domain.simulation.schedule import pick_current_slot
    from app.domain.world.grid import astar
    from app.domain.world.scene_cache import get_scene_cache

    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise TargetNotFound(f"agent {agent_id} not found")
    state = await session.get(AgentState, agent_id)
    if state is None:
        raise TargetNotFound(f"agent_state {agent_id} not found")

    sim = get_simulation_runtime().engine.get_simulation()
    world_time = sim.world_time if sim else _utcnow()

    schedule = agent.schedule_template or []
    slot = pick_current_slot(schedule, world_time.time())
    slot_dict = (
        {
            "start": slot.start.isoformat(),
            "end": slot.end.isoformat(),
            "activity": slot.activity,
            "location_id": slot.location_id,
            "description": slot.description,
        }
        if slot is not None
        else None
    )

    target_loc_id = target_location_id or (slot.location_id if slot else None)

    locations: dict[str, dict[str, Any]] = {}
    for loc in (await session.execute(select(Location))).scalars().all():
        locations[loc.id] = {
            "id": loc.id,
            "scene_id": loc.scene_id,
            "bounds": loc.bounds,
            "entry_tiles": loc.entry_tiles or [],
        }

    portals_rows = (await session.execute(select(Portal))).scalars().all()
    portals_by_scene: dict[str, list[dict[str, Any]]] = {}
    for p in portals_rows:
        portals_by_scene.setdefault(p.from_scene_id, []).append(
            {
                "id": p.id,
                "from_scene_id": p.from_scene_id,
                "to_scene_id": p.to_scene_id,
                "from_tile": p.from_tile,
                "to_tile": p.to_tile,
            }
        )

    # 把 portals 包成 rule_agent 期望的结构（带 from_scene_id / to_scene_id /
    # from_tile / to_tile 属性的对象）
    from types import SimpleNamespace

    def _to_ns(p: dict[str, Any]) -> SimpleNamespace:
        return SimpleNamespace(**p)

    portals_ns_by_scene: dict[str, list[Any]] = {
        sid: [_to_ns(p) for p in items] for sid, items in portals_by_scene.items()
    }

    cache = get_scene_cache()

    def _walkable_pair(scene_id: str, x: int, y: int) -> dict[str, Any]:
        """返回该 (scene, x, y) 的 walkable 状态（含 / 不含 hazard 两版）。"""
        grid = cache.get_grid(scene_id)
        if grid is None:
            return {"scene_loaded": False}
        tile = grid.get(x, y)
        return {
            "scene_loaded": True,
            "in_bounds": grid.in_bounds(x, y),
            "tile_present": tile is not None,
            "walkable_no_hazard": grid.is_walkable(x, y, avoid_hazards=False),
            "walkable_npc_view": grid.is_walkable(x, y, avoid_hazards=True),
            "tile_info": (
                {
                    "walkable": tile.walkable,
                    "blocks_movement": tile.blocks_movement,
                    "hazard_type": tile.hazard_type,
                    "hazard_level": tile.hazard_level,
                    "terrain": tile.terrain,
                }
                if tile is not None
                else None
            ),
        }

    target_info: dict[str, Any] = {"id": target_loc_id}
    chosen_entry: tuple[int, int] | None = None
    target_scene_id: str | None = None
    if target_loc_id and target_loc_id in locations:
        loc = locations[target_loc_id]
        target_info.update(
            {
                "scene_id": loc["scene_id"],
                "entry_tiles": loc["entry_tiles"],
                "bounds": loc["bounds"],
            }
        )
        chosen = _choose_goal_tile_for_slot(target_loc_id, locations)
        if chosen is not None:
            target_scene_id, chosen_entry = chosen
            target_info["chosen_target"] = {
                "scene_id": target_scene_id,
                "x": chosen_entry[0],
                "y": chosen_entry[1],
                "walkability": _walkable_pair(
                    target_scene_id, chosen_entry[0], chosen_entry[1]
                ),
            }
    elif target_loc_id:
        target_info["error"] = "location_not_found"

    # 当前 NPC 黑名单（Redis）
    blacklist: list[str] = []
    try:
        members = await get_redis().set_members(key_agent_unreachable(agent_id))
        blacklist = sorted(members)
    except Exception:
        logger.debug("read unreachable blacklist failed", exc_info=True)

    # 多段 pathfinding
    pathfinding: dict[str, Any] = {}
    if chosen_entry is not None and target_scene_id is not None:
        cur = (state.x, state.y)
        cur_scene = state.scene_id
        if cur_scene == target_scene_id:
            grid = cache.get_grid(cur_scene)
            if grid is not None:
                p_safe = astar(grid, cur, chosen_entry, avoid_hazards=True)
                p_raw = astar(grid, cur, chosen_entry, avoid_hazards=False)
                pathfinding["leg1"] = {
                    "scene_id": cur_scene,
                    "from": list(cur),
                    "to": list(chosen_entry),
                    "found_npc_view": bool(p_safe),
                    "found_no_hazard": bool(p_raw),
                    "length_npc_view": len(p_safe) if p_safe else 0,
                }
        else:
            portal_tile = _find_portal_from_to(
                portals_ns_by_scene, cur_scene, target_scene_id
            )
            next_scene = target_scene_id
            route_used = "direct"
            if portal_tile is None:
                route = _find_inter_scene_route(
                    portals_ns_by_scene, cur_scene, target_scene_id
                )
                if route:
                    next_scene = route[0]
                    portal_tile = _find_portal_from_to(
                        portals_ns_by_scene, cur_scene, next_scene
                    )
                    route_used = "multi_hop:" + " → ".join([cur_scene, *route])
                else:
                    route_used = "no_route"
            pathfinding["scene_chain"] = {
                "from_scene": cur_scene,
                "to_scene": target_scene_id,
                "first_hop_scene": next_scene,
                "route": route_used,
                "first_hop_portal_tile": list(portal_tile) if portal_tile else None,
                "first_hop_portal_walkability": (
                    _walkable_pair(cur_scene, portal_tile[0], portal_tile[1])
                    if portal_tile
                    else None
                ),
            }
            grid = cache.get_grid(cur_scene)
            if grid is not None and portal_tile is not None:
                p_safe = astar(grid, cur, portal_tile, avoid_hazards=True)
                p_raw = astar(grid, cur, portal_tile, avoid_hazards=False)
                pathfinding["leg1"] = {
                    "scene_id": cur_scene,
                    "from": list(cur),
                    "to": list(portal_tile),
                    "found_npc_view": bool(p_safe),
                    "found_no_hazard": bool(p_raw),
                    "length_npc_view": len(p_safe) if p_safe else 0,
                }

    # 自动诊断
    diagnosis: list[str] = []
    if target_loc_id and target_loc_id in blacklist:
        diagnosis.append(
            f"[BLACKLISTED] {target_loc_id} 已在 5 分钟黑名单内，"
            "近期一次寻路失败导致；以下分析针对那次失败的根因，而非「当前一刻」。"
        )
    if "chosen_target" in target_info:
        wk = target_info["chosen_target"]["walkability"]
        if not wk.get("scene_loaded"):
            diagnosis.append(
                f"[CRITICAL] 目标 scene_id={target_info['scene_id']} 未加载到 SceneCache，"
                "可能是新建场景没刷 cache。"
            )
        elif not wk.get("walkable_no_hazard"):
            diagnosis.append(
                f"[ENTRY_BLOCKED] entry_tile {target_info['chosen_target']['x']},"
                f"{target_info['chosen_target']['y']} 在 grid 中不可走"
                f"（tile={wk.get('tile_info')}）。世界生成器 bug：location 入口"
                "落在不可走 tile 上。"
            )
        elif not wk.get("walkable_npc_view") and wk.get("walkable_no_hazard"):
            diagnosis.append(
                "[ENTRY_HAZARD] entry_tile 玩家视角可走，但 NPC 因 hazard 视为不可走"
                "（NPC 默认 avoid_hazards=True）。"
            )
    if "leg1" in pathfinding:
        leg = pathfinding["leg1"]
        if not leg["found_npc_view"] and leg["found_no_hazard"]:
            diagnosis.append(
                "[PATH_HAZARD] 第一段 A* 在不避 hazard 时可达、避 hazard 时不可达——"
                "意味着路径必须穿过 hazard tile（如河水/火），NPC 拒走。"
                "这就是「看着能走但 NPC 走不到」的最常见原因。"
            )
        elif not leg["found_npc_view"] and not leg["found_no_hazard"]:
            diagnosis.append(
                "[PATH_BLOCKED] 第一段 A* 即使忽略 hazard 也不可达——"
                "拓扑上被 blocks_movement 物体或 walkable=False 的 tile 完全切断。"
            )
    if "scene_chain" in pathfinding:
        sc = pathfinding["scene_chain"]
        if sc["route"] == "no_route":
            diagnosis.append(
                f"[NO_PORTAL] {sc['from_scene']} → {sc['to_scene']} 没有任何 portal 链，"
                "Portal 表数据缺失或 from_scene/to_scene 写反。"
            )
        if sc.get("first_hop_portal_walkability") and not sc[
            "first_hop_portal_walkability"
        ].get("walkable_no_hazard"):
            diagnosis.append(
                f"[PORTAL_TILE_BLOCKED] 通往 {sc['first_hop_scene']} 的 portal "
                f"from_tile={sc['first_hop_portal_tile']} 自身不可走，"
                "Portal 数据 / 世界生成器 bug。"
            )
    if not diagnosis:
        diagnosis.append("[OK] 诊断未发现明显问题；建议结合 leg1.length_npc_view 与人工肉眼对照。")

    return {
        "agent": {
            "id": agent.id,
            "name": agent.name,
            "schedule_template": schedule,
        },
        "world_time": world_time.isoformat(),
        "current_position": {
            "scene_id": state.scene_id,
            "x": state.x,
            "y": state.y,
            "state": state.state,
            "current_goal": state.current_goal,
            "scene_walkability_here": _walkable_pair(state.scene_id, state.x, state.y),
        },
        "current_slot": slot_dict,
        "target_location": target_info,
        "blacklist": blacklist,
        "pathfinding": pathfinding,
        "diagnosis": diagnosis,
    }


# -----------------------------------------------------------------------------
# Trace
# -----------------------------------------------------------------------------


@router.get("/traces/{trace_id}")
async def get_trace(
    trace_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    events_stmt = (
        select(ObservabilityEvent)
        .where(ObservabilityEvent.trace_id == trace_id)
        .order_by(ObservabilityEvent.created_at.asc())
    )
    events = [_serialize_obs_event(r) for r in (await session.execute(events_stmt)).scalars().all()]

    llm_stmt = (
        select(LLMCallRecord)
        .where(LLMCallRecord.trace_id == trace_id)
        .order_by(LLMCallRecord.created_at.asc())
    )
    llm_calls = [_serialize_llm_call(r) for r in (await session.execute(llm_stmt)).scalars().all()]

    tool_stmt = (
        select(ToolCallRecord)
        .where(ToolCallRecord.trace_id == trace_id)
        .order_by(ToolCallRecord.created_at.asc())
    )
    tool_calls = [_serialize_tool_call(r) for r in (await session.execute(tool_stmt)).scalars().all()]

    task_stmt = select(Task).where(Task.trace_id == trace_id).limit(20)
    tasks = [_serialize_task(r) for r in (await session.execute(task_stmt)).scalars().all()]

    if not events and not llm_calls and not tool_calls:
        raise TargetNotFound(f"trace {trace_id} not found")

    return {
        "trace_id": trace_id,
        "events": events,
        "llm_calls": llm_calls,
        "tool_calls": tool_calls,
        "tasks": tasks,
    }


# -----------------------------------------------------------------------------
# LLM / Tool / Task / Memory / Error 列表
# -----------------------------------------------------------------------------


@router.get("/llm-calls")
async def list_llm_calls(
    session: AsyncSession = Depends(get_session),
    success: bool | None = Query(None),
    model: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    stmt = select(LLMCallRecord).order_by(desc(LLMCallRecord.created_at)).limit(limit)
    if success is not None:
        stmt = stmt.where(LLMCallRecord.success == success)
    if model:
        stmt = stmt.where(LLMCallRecord.model == model)
    rows = (await session.execute(stmt)).scalars().all()
    return [_serialize_llm_call(r) for r in rows]


@router.get("/tool-calls")
async def list_tool_calls(
    session: AsyncSession = Depends(get_session),
    tool: str | None = Query(None),
    success: bool | None = Query(None),
    agent_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    stmt = select(ToolCallRecord).order_by(desc(ToolCallRecord.created_at)).limit(limit)
    if tool:
        stmt = stmt.where(ToolCallRecord.tool == tool)
    if success is not None:
        stmt = stmt.where(ToolCallRecord.success == success)
    if agent_id:
        stmt = stmt.where(ToolCallRecord.caller_agent_id == agent_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [_serialize_tool_call(r) for r in rows]


@router.get("/tasks")
async def list_tasks(
    session: AsyncSession = Depends(get_session),
    status: str | None = Query(None),
    task_type: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    stmt = select(Task).order_by(desc(Task.enqueued_at)).limit(limit)
    if status:
        stmt = stmt.where(Task.status == status)
    if task_type:
        stmt = stmt.where(Task.task_type == task_type)
    rows = (await session.execute(stmt)).scalars().all()
    return [_serialize_task(r) for r in rows]


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    task = await session.get(Task, task_id)
    if task is None:
        raise TargetNotFound(f"task {task_id} not found")
    return _serialize_task(task)


@router.get("/memories")
async def list_memories(
    session: AsyncSession = Depends(get_session),
    agent_id: str | None = Query(None),
    scope: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    stmt = select(Memory).order_by(desc(Memory.created_at)).limit(limit)
    if agent_id:
        stmt = stmt.where(Memory.agent_id == agent_id)
    if scope:
        stmt = stmt.where(Memory.scope == scope)
    rows = (await session.execute(stmt)).scalars().all()
    return [_serialize_memory(r) for r in rows]


@router.get("/errors")
async def list_errors(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    stmt = (
        select(ObservabilityEvent)
        .where(ObservabilityEvent.level.in_(["ERROR", "WARNING"]))
        .order_by(desc(ObservabilityEvent.created_at))
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_serialize_obs_event(r) for r in rows]


# -----------------------------------------------------------------------------
# Health checks
# -----------------------------------------------------------------------------


health_router = APIRouter(prefix="/health", tags=["observability"])


@health_router.get("/db")
async def health_db(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    try:
        await session.execute(select(1))
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}


@health_router.get("/redis")
async def health_redis() -> dict[str, Any]:
    return await get_redis().health_check()


@health_router.get("/llm")
async def health_llm() -> dict[str, Any]:
    settings = get_settings()
    return {
        "ok": bool(settings.llm_is_configured),
        "provider": settings.llm_provider,
        "model": settings.llm_chat_model,
        "base_url": settings.llm_base_url,
        "enabled": settings.llm_enabled,
        "has_api_key": bool(settings.llm_api_key),
    }


# -----------------------------------------------------------------------------
# 序列化辅助
# -----------------------------------------------------------------------------


def _serialize_obs_event(r: ObservabilityEvent) -> dict[str, Any]:
    return {
        "id": r.id,
        "simulation_id": r.simulation_id,
        "trace_id": r.trace_id,
        "span_id": r.span_id,
        "parent_span_id": r.parent_span_id,
        "category": r.category,
        "event_type": r.event_type,
        "level": r.level,
        "title": r.title,
        "entity_id": r.entity_id,
        "player_id": r.player_id,
        "task_id": r.task_id,
        "world_step": r.world_step,
        "duration_ms": r.duration_ms,
        "payload": r.payload,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _serialize_world_event(r: WorldEvent) -> dict[str, Any]:
    return {
        "id": r.id,
        "simulation_id": r.simulation_id,
        "event_type": r.event_type,
        "source": r.source,
        "actor_entity_id": r.actor_entity_id,
        "target_entity_id": r.target_entity_id,
        "location_id": r.location_id,
        "scene_id": r.scene_id,
        "description": r.description,
        "importance": r.importance,
        "payload": r.payload,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _serialize_llm_call(r: LLMCallRecord) -> dict[str, Any]:
    return {
        "id": r.id,
        "trace_id": r.trace_id,
        "span_id": r.span_id,
        "simulation_id": r.simulation_id,
        "agent_id": r.agent_id,
        "provider": r.provider,
        "model": r.model,
        "prompt_template_id": r.prompt_template_id,
        "prompt_version": r.prompt_version,
        "caller_module": r.caller_module,
        "input_summary": r.input_summary,
        "token_estimate": r.token_estimate,
        "latency_ms": r.latency_ms,
        "retry_count": r.retry_count,
        "success": r.success,
        "schema_valid": r.schema_valid,
        "fallback_used": r.fallback_used,
        "error_code": r.error_code,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _serialize_tool_call(r: ToolCallRecord) -> dict[str, Any]:
    return {
        "id": r.id,
        "trace_id": r.trace_id,
        "span_id": r.span_id,
        "simulation_id": r.simulation_id,
        "tool": r.tool,
        "caller_agent_id": r.caller_agent_id,
        "entity_type": r.entity_type,
        "arguments": r.arguments,
        "schema_valid": r.schema_valid,
        "permission_valid": r.permission_valid,
        "world_state_valid": r.world_state_valid,
        "success": r.success,
        "duration_ms": r.duration_ms,
        "result_summary": r.result_summary,
        "error_code": r.error_code,
        "source": r.source,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _serialize_task(r: Task) -> dict[str, Any]:
    trace_meta: dict[str, Any] = {}
    if isinstance(r.payload, dict) and "__trace__" in r.payload:
        trace_meta = r.payload["__trace__"] or {}
    return {
        "id": r.id,
        "task_type": r.task_type,
        "status": r.status,
        "priority": r.priority,
        "entity_id": r.entity_id,
        "simulation_id": r.simulation_id,
        "simulation_step": r.simulation_step,
        "idempotency_key": r.idempotency_key,
        "retry_count": r.retry_count,
        "max_retries": r.max_retries,
        "trace_id": r.trace_id,
        "parent_trace_id": trace_meta.get("parent_trace_id"),
        "last_error": r.last_error,
        "payload": {k: v for k, v in (r.payload or {}).items() if k != "__trace__"},
        "result": r.result,
        "enqueued_at": r.enqueued_at.isoformat() if r.enqueued_at else None,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
    }


def _serialize_memory(r: Memory) -> dict[str, Any]:
    return {
        "id": r.id,
        "agent_id": r.agent_id,
        "memory_type": r.memory_type,
        "scope": r.scope,
        "description": r.description,
        "importance": r.importance,
        "emotional_valence": r.emotional_valence,
        "keywords": r.keywords,
        "evidence_memory_ids": r.evidence_memory_ids,
        "has_embedding": r.embedding is not None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "last_accessed_at": r.last_accessed_at.isoformat() if r.last_accessed_at else None,
        "ttl_expires_at": r.ttl_expires_at.isoformat() if r.ttl_expires_at else None,
    }


__all__ = ["router", "health_router"]
