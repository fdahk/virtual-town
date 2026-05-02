"""新游戏 / 存档服务。

封装"创建新游戏"流程：
1. 解析前端传入的 NPC + 玩家配置
2. 调 ``world_gen.generate_world`` 产生 ``WorldPlan``
3. 调 ``world_gen.apply.apply_plan_async`` 写入 DB
4. ``simulation_runtime.reload()`` 让引擎从 DB 加载新世界
5. 把 simulation 状态切到 ``running``

JSON 存档（Phase 2）：
- ``export_save``：把当前 DB 的 6 张世界表 + agents/relationships dump 成 JSON
- ``import_save``：反向写入并 reload 引擎
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Agent, Location, MapScene, MapTile, Portal, Relationship, Simulation, WorldObject
from app.db.session import get_session_factory
from app.db.templates import (
    DEFAULT_ANIMAL_TEMPLATES,
    DEFAULT_HUMAN_TEMPLATES,
    DEFAULT_PLAYER_TEMPLATE,
    AgentTemplate,
)
from app.domain.world_gen import GenerationConfig, apply_plan, generate_world
from app.domain.world_gen.apply import apply_plan_async
from app.services.simulation_runtime import get_simulation_runtime

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 模板 → AgentTemplate 反序列化
# ---------------------------------------------------------------------------


def _coerce_template(payload: dict[str, Any], default_kind: str) -> AgentTemplate:
    """把前端 NPCEditor 提交的 dict 转成 AgentTemplate。"""
    return AgentTemplate(
        id=payload["id"],
        name=payload["name"],
        entity_type=payload.get("entity_type", default_kind),
        age=payload.get("age"),
        gender=payload.get("gender"),
        species=payload.get("species"),
        occupation=payload.get("occupation"),
        personality=list(payload.get("personality") or []),
        background=payload.get("background", ""),
        lifestyle=payload.get("lifestyle"),
        long_term_goals=list(payload.get("long_term_goals") or []),
        schedule_id=payload.get("schedule_id"),
        art=dict(payload.get("art") or {}),
        accent_color=payload.get("accent_color", "#cccccc"),
        portrait_url=payload.get("portrait_url"),
        has_home=bool(payload.get("has_home", True)),
        upstairs_of=payload.get("upstairs_of"),
        cohabits_with=payload.get("cohabits_with"),
        preferred_district=payload.get("preferred_district", "north"),
        owner_id=payload.get("owner_id"),
        notes=payload.get("notes", ""),
    )


# ---------------------------------------------------------------------------
# 新游戏
# ---------------------------------------------------------------------------


async def create_new_game(
    session: AsyncSession,
    *,
    seed: int = 42,
    outdoor_width: int = 120,
    outdoor_height: int = 90,
    humans: list[dict[str, Any]] | None = None,
    animals: list[dict[str, Any]] | None = None,
    player: dict[str, Any] | None = None,
    auto_start: bool = True,
) -> dict[str, Any]:
    """创建新世界并切到运行态。

    若 ``humans`` / ``animals`` / ``player`` 任一为 ``None``，使用默认模板。
    """

    human_templates = (
        [_coerce_template(h, "human") for h in humans]
        if humans is not None else list(DEFAULT_HUMAN_TEMPLATES)
    )
    animal_templates = (
        [_coerce_template(a, "animal") for a in animals]
        if animals is not None else list(DEFAULT_ANIMAL_TEMPLATES)
    )
    player_template = (
        _coerce_template(player, "player") if player else DEFAULT_PLAYER_TEMPLATE
    )

    config = GenerationConfig(
        seed=seed,
        outdoor_width=outdoor_width,
        outdoor_height=outdoor_height,
        humans=human_templates,
        animals=animal_templates,
        player=player_template,
    )

    plan = generate_world(config)
    await apply_plan_async(session, plan)

    # 让引擎重新加载世界
    runtime = get_simulation_runtime()
    await runtime.reload()

    # 状态切到 running（如果 auto_start）
    sim = (await session.execute(select(Simulation).limit(1))).scalar_one_or_none()
    if sim and auto_start:
        sim.status = "running"
        await session.commit()
        runtime.engine.set_status("running")

    logger.info(
        "new game created seed=%s humans=%s animals=%s",
        seed, len(human_templates), len(animal_templates),
    )

    return {
        "simulation_id": sim.id if sim else None,
        "outdoor_scene_id": plan.outdoor_scene_id,
        "agent_count": len(plan.agents),
        "scene_count": len(plan.scenes),
        "object_count": len(plan.world_objects),
        "relationship_count": len(plan.relationships),
    }


# ---------------------------------------------------------------------------
# 同步版（供 seed 脚本使用）
# ---------------------------------------------------------------------------


def create_default_world_sync(session, *, seed: int = 42) -> None:
    """seed.py 入口：构造默认 22 NPC + 默认地图并写入。"""
    config = GenerationConfig.default(seed=seed)
    plan = generate_world(config)
    apply_plan(session, plan)


# ---------------------------------------------------------------------------
# 存档（导出 / 导入）
# ---------------------------------------------------------------------------


async def export_save(session: AsyncSession) -> dict[str, Any]:
    """把当前 DB 的世界状态序列化为 JSON 友好的字典。

    覆盖：scenes / tiles / locations / portals / world_objects /
    agents (含 state) / relationships。
    """
    from app.db.models import AgentState
    from sqlalchemy.orm import selectinload

    scenes = (await session.execute(select(MapScene))).scalars().all()
    tiles = (await session.execute(select(MapTile))).scalars().all()
    locations = (await session.execute(select(Location))).scalars().all()
    portals = (await session.execute(select(Portal))).scalars().all()
    objects = (await session.execute(select(WorldObject))).scalars().all()
    agents = (
        await session.execute(select(Agent).options(selectinload(Agent.state)))
    ).scalars().all()
    rels = (await session.execute(select(Relationship))).scalars().all()

    def scene_d(s: MapScene) -> dict[str, Any]:
        return {"id": s.id, "name": s.name, "scene_type": s.scene_type,
                "width": s.width, "height": s.height, "tile_size": s.tile_size,
                "description": s.description}

    def tile_d(t: MapTile) -> dict[str, Any]:
        return {"scene_id": t.scene_id, "x": t.x, "y": t.y, "terrain": t.terrain,
                "walkable": t.walkable, "blocks_movement": t.blocks_movement,
                "blocks_vision": t.blocks_vision, "hazard_type": t.hazard_type,
                "hazard_level": t.hazard_level, "tags": list(t.tags or [])}

    def loc_d(l: Location) -> dict[str, Any]:
        return {"id": l.id, "scene_id": l.scene_id, "name": l.name,
                "location_type": l.location_type, "bounds": l.bounds,
                "entry_tiles": l.entry_tiles, "open_hours": l.open_hours,
                "tags": list(l.tags or []), "description": l.description}

    def portal_d(p: Portal) -> dict[str, Any]:
        return {"id": p.id, "from_scene_id": p.from_scene_id, "from_tile": p.from_tile,
                "to_scene_id": p.to_scene_id, "to_tile": p.to_tile,
                "interaction_type": p.interaction_type,
                "requires_permission": p.requires_permission, "name": p.name}

    def obj_d(o: WorldObject) -> dict[str, Any]:
        return {"id": o.id, "scene_id": o.scene_id, "name": o.name,
                "object_type": o.object_type, "position": o.position, "size": o.size,
                "blocks_movement": o.blocks_movement,
                "available_interactions": list(o.available_interactions or []),
                "state": dict(o.state or {}), "tags": list(o.tags or [])}

    def agent_d(a: Agent) -> dict[str, Any]:
        st = a.state
        state_dict = None
        if st is not None:
            state_dict = {
                "agent_id": st.agent_id, "scene_id": st.scene_id,
                "x": st.x, "y": st.y, "state": st.state,
                "emotion": st.emotion, "energy": st.energy,
                "hunger": st.hunger, "social_need": st.social_need,
                "fear": st.fear, "facing": st.facing,
            }
        return {"id": a.id, "entity_type": a.entity_type, "name": a.name,
                "age": a.age, "gender": a.gender, "species": a.species,
                "occupation": a.occupation, "personality": list(a.personality or []),
                "background": a.background, "lifestyle": a.lifestyle,
                "appearance": dict(a.appearance or {}),
                "long_term_goals": list(a.long_term_goals or []),
                "home_location_id": a.home_location_id,
                "schedule_template": list(a.schedule_template or []),
                "is_active": a.is_active,
                "state": state_dict}

    def rel_d(r: Relationship) -> dict[str, Any]:
        return {"id": r.id, "from_agent_id": r.from_agent_id,
                "to_entity_id": r.to_entity_id,
                "familiarity": r.familiarity, "trust": r.trust,
                "affection": r.affection, "fear": r.fear,
                "summary": r.summary}

    return {
        "version": "1.0",
        "scenes": [scene_d(s) for s in scenes],
        "tiles": [tile_d(t) for t in tiles],
        "locations": [loc_d(l) for l in locations],
        "portals": [portal_d(p) for p in portals],
        "world_objects": [obj_d(o) for o in objects],
        "agents": [agent_d(a) for a in agents],
        "relationships": [rel_d(r) for r in rels],
    }
