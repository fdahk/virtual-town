"""新游戏 / 存档服务。

封装"创建新游戏"流程：
1. 解析前端传入的 NPC + 玩家配置
2. 调 ``world_gen.generate_world`` 产生 ``WorldPlan``
3. 调 ``world_gen.apply.apply_plan_async`` 写入 DB
4. ``simulation_runtime.reload()`` 让引擎从 DB 加载新世界
5. 把 simulation 状态切到 ``running``

JSON 存档（阶段 21）：
- ``export_save``：把当前 DB 的世界表 + agents/state + relationships +
  memories（不含 embedding）+ simulation 元 dump 成 JSON。
- ``import_save``：清空 DB 后反向写入，并触发 ``simulation_runtime.reload()``
  让引擎从 DB 重新加载。

存档不包含的内容（设计取舍，避免 JSON 体积爆炸 + LLM/embedding 副作用）：
- ``memories.embedding`` 列：导入后由 ``write_memory_embedding`` 任务异步重建
- ``world_events`` / ``agent_actions`` / ``dialogue_messages`` 等流式叙事
- ``observability_events`` / ``llm_call_records`` 等可观测性审计
- 任务队列状态（``tasks`` / ``task_status_log``）
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import (
    Agent,
    AgentState,
    Location,
    MapScene,
    MapTile,
    Memory,
    Portal,
    Relationship,
    Simulation,
    WorldObject,
)
from app.db.templates import (
    DEFAULT_ANIMAL_TEMPLATES,
    DEFAULT_HUMAN_TEMPLATES,
    DEFAULT_PLAYER_TEMPLATE,
    AgentTemplate,
)
from app.domain.world_gen import GenerationConfig, apply_plan, generate_world
from app.domain.world_gen.apply import CLEAR_TABLES, apply_plan_async
from app.schemas.simulation import normalize_simulation_speed_multiplier
from app.services.simulation_runtime import get_simulation_runtime

logger = get_logger(__name__)


SAVE_FORMAT_VERSION = "1.1"
"""存档文件版本号。改动数据契约时同步 bump。

向后兼容策略（语义化版本）：
- 主版本号（1.x → 2.x）：契约破坏性变更，旧 save 不能被新 ``import_save`` 接受。
- 次版本号（1.0 → 1.1）：新增字段（如本次的 simulation / memories）。
  导入时缺失的新字段允许使用默认值。
"""


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
    outdoor_width: int | None = None,
    outdoor_height: int | None = None,
    humans: list[dict[str, Any]] | None = None,
    animals: list[dict[str, Any]] | None = None,
    player: dict[str, Any] | None = None,
    auto_start: bool = True,
) -> dict[str, Any]:
    """创建新世界并切到运行态。

    若 ``humans`` / ``animals`` / ``player`` 任一为 ``None``，使用默认模板。
    """
    settings = get_settings()
    ow = outdoor_width if outdoor_width is not None else settings.world_gen_outdoor_default_width
    oh = outdoor_height if outdoor_height is not None else settings.world_gen_outdoor_default_height

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
        outdoor_width=ow,
        outdoor_height=oh,
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

    覆盖：simulation 元 / scenes / tiles / locations / portals / world_objects /
    agents（含 state）/ relationships / memories（不含 embedding）。

    返回字典结构与 SAVE_FORMAT_VERSION 对齐，可直接 ``json.dumps`` 后写入文件。
    """
    scenes = (await session.execute(select(MapScene))).scalars().all()
    tiles = (await session.execute(select(MapTile))).scalars().all()
    locations = (await session.execute(select(Location))).scalars().all()
    portals = (await session.execute(select(Portal))).scalars().all()
    objects = (await session.execute(select(WorldObject))).scalars().all()
    agents = (
        await session.execute(select(Agent).options(selectinload(Agent.state)))
    ).scalars().all()
    rels = (await session.execute(select(Relationship))).scalars().all()
    memories = (await session.execute(select(Memory))).scalars().all()
    simulation = (await session.execute(select(Simulation).limit(1))).scalar_one_or_none()

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

    def memory_d(m: Memory) -> dict[str, Any]:
        # 故意丢弃 embedding 列：1) 1024 维向量让 JSON 体积膨胀 ~10x；
        # 2) 嵌入模型可能升级，导入后由 write_memory_embedding 任务重建更稳健。
        return {
            "id": m.id, "agent_id": m.agent_id,
            "memory_type": m.memory_type, "scope": m.scope,
            "subject": m.subject, "predicate": m.predicate, "object": m.object,
            "description": m.description, "importance": m.importance,
            "importance_detail": dict(m.importance_detail or {}),
            "emotional_valence": m.emotional_valence,
            "keywords": list(m.keywords or []),
            "evidence_memory_ids": list(m.evidence_memory_ids or []),
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "last_accessed_at": m.last_accessed_at.isoformat() if m.last_accessed_at else None,
            "ttl_expires_at": m.ttl_expires_at.isoformat() if m.ttl_expires_at else None,
            "summarized_into_id": m.summarized_into_id,
        }

    def sim_d(s: Simulation | None) -> dict[str, Any] | None:
        if s is None:
            return None
        return {
            "id": s.id, "status": s.status,
            "world_time": s.world_time.isoformat() if s.world_time else None,
            "world_tick_hz": s.world_tick_hz,
            "ai_tick_minutes": s.ai_tick_minutes,
            "speed_multiplier": s.speed_multiplier,
            "current_step": s.current_step,
        }

    payload: dict[str, Any] = {
        "version": SAVE_FORMAT_VERSION,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "simulation": sim_d(simulation),
        "scenes": [scene_d(s) for s in scenes],
        "tiles": [tile_d(t) for t in tiles],
        "locations": [loc_d(l) for l in locations],
        "portals": [portal_d(p) for p in portals],
        "world_objects": [obj_d(o) for o in objects],
        "agents": [agent_d(a) for a in agents],
        "relationships": [rel_d(r) for r in rels],
        "memories": [memory_d(m) for m in memories],
    }
    # checksum 写在 payload 之外（计算时排除自己），导入时用于检测人工乱改。
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    payload["checksum"] = hashlib.sha256(canonical).hexdigest()
    payload["counts"] = {
        "scenes": len(scenes), "tiles": len(tiles),
        "locations": len(locations), "portals": len(portals),
        "world_objects": len(objects), "agents": len(agents),
        "relationships": len(rels), "memories": len(memories),
    }
    return payload


# ---------------------------------------------------------------------------
# 存档导入
# ---------------------------------------------------------------------------


class SaveValidationError(ValueError):
    """存档不合法：版本号缺失 / 主版本号不兼容 / 必需字段缺失。

    走 400 而非 500，让前端展示具体错误而不是"服务器内部错误"。
    """


def _validate_save(payload: dict[str, Any]) -> None:
    """对导入 payload 做最小可用性校验。

    不做 schema 全字段验证：导入时若某条记忆字段缺失，靠 ORM 列默认值兜底；
    完整 schema 验证由 ``WorldObjectSchema`` 等在生成阶段已经做过的链路代劳。
    """
    if not isinstance(payload, dict):
        raise SaveValidationError("save 必须是 JSON 对象")
    version = payload.get("version")
    if not isinstance(version, str):
        raise SaveValidationError("save 缺少 'version' 字段")
    major = version.split(".")[0]
    expected_major = SAVE_FORMAT_VERSION.split(".")[0]
    if major != expected_major:
        raise SaveValidationError(
            f"存档主版本号 {version} 与当前服务端 {SAVE_FORMAT_VERSION} 不兼容；"
            f"请使用同主版本的客户端导出/导入"
        )
    for key in ("scenes", "tiles", "locations", "agents"):
        if key not in payload:
            raise SaveValidationError(f"save 缺少必需字段 '{key}'")
    if not isinstance(payload["agents"], list) or not payload["agents"]:
        raise SaveValidationError("save 的 agents 至少需要 1 个 entry（玩家）")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def import_save(session: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    """把 JSON 快照写回数据库，并触发引擎重载。

    流程与 ``apply_plan_async`` 类似：
    1. 校验 version + 必需字段（任一不通过就 ``SaveValidationError``）
    2. 按 FK 倒序清空旧数据
    3. 按 FK 顺序插入：scenes → tiles → locations → portals → world_objects
       → agents → agent_states → relationships → memories → simulation
    4. ``simulation_runtime.reload()`` 让引擎重新加载世界
    """
    _validate_save(payload)

    # 1. 清空（与 world_gen.apply.CLEAR_TABLES 复用，确保删除顺序与 FK 一致）
    for model in CLEAR_TABLES:
        await session.execute(delete(model))
    await session.commit()

    # 2. 写入静态世界
    scenes = payload.get("scenes") or []
    session.add_all([
        MapScene(
            id=s["id"], name=s["name"], scene_type=s["scene_type"],
            width=s["width"], height=s["height"],
            tile_size=s.get("tile_size", 32),
            description=s.get("description"),
        )
        for s in scenes
    ])
    await session.flush()

    for t in payload.get("tiles") or []:
        session.add(MapTile(
            scene_id=t["scene_id"], x=t["x"], y=t["y"], terrain=t["terrain"],
            walkable=t["walkable"], blocks_movement=t["blocks_movement"],
            blocks_vision=t["blocks_vision"],
            hazard_type=t.get("hazard_type"), hazard_level=t.get("hazard_level"),
            tags=list(t.get("tags") or []),
        ))

    session.add_all([
        Location(
            id=l["id"], scene_id=l["scene_id"], name=l["name"],
            location_type=l["location_type"], bounds=l["bounds"],
            entry_tiles=l["entry_tiles"], open_hours=l.get("open_hours"),
            tags=list(l.get("tags") or []),
            description=l.get("description"),
        )
        for l in payload.get("locations") or []
    ])
    await session.flush()

    session.add_all([
        Portal(
            id=p["id"], from_scene_id=p["from_scene_id"], from_tile=p["from_tile"],
            to_scene_id=p["to_scene_id"], to_tile=p["to_tile"],
            interaction_type=p.get("interaction_type", "auto_enter"),
            requires_permission=p.get("requires_permission", False),
            name=p.get("name"),
        )
        for p in payload.get("portals") or []
    ])
    session.add_all([
        WorldObject(
            id=o["id"], scene_id=o["scene_id"], name=o["name"],
            object_type=o["object_type"], position=o["position"],
            size=o.get("size", {"width": 1, "height": 1}),
            blocks_movement=o.get("blocks_movement", False),
            available_interactions=list(o.get("available_interactions") or []),
            state=dict(o.get("state") or {}),
            tags=list(o.get("tags") or []),
        )
        for o in payload.get("world_objects") or []
    ])

    # 3. agents + state + relationships
    for a in payload.get("agents") or []:
        session.add(Agent(
            id=a["id"], entity_type=a["entity_type"], name=a["name"],
            age=a.get("age"), gender=a.get("gender"), species=a.get("species"),
            occupation=a.get("occupation"),
            personality=list(a.get("personality") or []),
            background=a.get("background", ""),
            lifestyle=a.get("lifestyle"),
            appearance=dict(a.get("appearance") or {}),
            long_term_goals=list(a.get("long_term_goals") or []),
            home_location_id=a.get("home_location_id"),
            schedule_template=list(a.get("schedule_template") or []),
            is_active=a.get("is_active", True),
        ))
    await session.flush()

    for a in payload.get("agents") or []:
        st = a.get("state")
        if st is None:
            continue
        session.add(AgentState(
            agent_id=st.get("agent_id", a["id"]),
            scene_id=st["scene_id"],
            x=st["x"], y=st["y"],
            state=st.get("state", "IDLE"),
            emotion=st.get("emotion"),
            energy=st.get("energy", 1.0),
            hunger=st.get("hunger", 0.2),
            social_need=st.get("social_need", 0.3),
            fear=st.get("fear", 0.0),
            facing=st.get("facing", "down"),
        ))

    session.add_all([
        Relationship(
            id=r["id"],
            from_agent_id=r["from_agent_id"],
            to_entity_id=r["to_entity_id"],
            familiarity=r.get("familiarity", 0.0),
            trust=r.get("trust", 0.0),
            affection=r.get("affection", 0.0),
            fear=r.get("fear", 0.0),
            summary=r.get("summary"),
        )
        for r in payload.get("relationships") or []
    ])

    # 4. memories（embedding 故意置 NULL，由后台任务重算）
    for m in payload.get("memories") or []:
        session.add(Memory(
            id=m["id"], agent_id=m["agent_id"],
            memory_type=m["memory_type"], scope=m.get("scope", "short_term"),
            subject=m.get("subject"), predicate=m.get("predicate"), object=m.get("object"),
            description=m["description"],
            importance=m.get("importance", 3),
            importance_detail=dict(m.get("importance_detail") or {}),
            emotional_valence=m.get("emotional_valence", 0.0),
            keywords=list(m.get("keywords") or []),
            evidence_memory_ids=list(m.get("evidence_memory_ids") or []),
            embedding=None,  # 重新计算
            created_at=_parse_iso(m.get("created_at")) or datetime.now(timezone.utc),
            last_accessed_at=_parse_iso(m.get("last_accessed_at")),
            ttl_expires_at=_parse_iso(m.get("ttl_expires_at")),
            summarized_into_id=m.get("summarized_into_id"),
        ))

    # 5. simulation 元（缺失则用默认值起一个新 simulation 行）
    sim_payload = payload.get("simulation") or {}
    sim = Simulation(
        id=sim_payload.get("id") or _new_sim_id(),
        status=sim_payload.get("status", "paused"),
        world_time=_parse_iso(sim_payload.get("world_time")) or datetime.now(timezone.utc),
        world_tick_hz=sim_payload.get("world_tick_hz", 2.0),
        ai_tick_minutes=sim_payload.get("ai_tick_minutes", 5),
        speed_multiplier=normalize_simulation_speed_multiplier(
            float(sim_payload.get("speed_multiplier", 1.0))
        ),
        current_step=sim_payload.get("current_step", 0),
    )
    session.add(sim)
    await session.commit()

    # 6. 让引擎重新从 DB 加载世界（与 create_new_game 复用同一通道）
    runtime = get_simulation_runtime()
    await runtime.reload()
    # 默认 paused：用户从前端「继续 / 解除暂停」按钮显式切到 running，
    # 避免一进入游戏就开始烧 LLM token。
    runtime.engine.set_status(sim.status)

    counts = payload.get("counts") or {}
    logger.info(
        "save imported version=%s simulation_id=%s agents=%s memories=%s",
        payload.get("version"), sim.id,
        len(payload.get("agents") or []),
        len(payload.get("memories") or []),
    )
    return {
        "simulation_id": sim.id,
        "status": sim.status,
        "current_step": sim.current_step,
        "imported_counts": counts,
    }


def _new_sim_id() -> str:
    """没存档 simulation.id 时给一个新的 UUID。"""
    import uuid
    return str(uuid.uuid4())
