"""世界生成编排器。

公共入口：``generate_world(config) -> WorldPlan``。
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.core.config import get_settings
from app.db.templates.npc_defaults import (
    DEFAULT_ANIMAL_TEMPLATES,
    DEFAULT_HUMAN_TEMPLATES,
    DEFAULT_PLAYER_TEMPLATE,
    AgentTemplate,
)
from app.domain.world_gen.interiors import (
    INDOOR_H,
    INDOOR_W,
    build_indoor_tiles,
    furniture_for_interior,
)
from app.domain.world_gen.outdoor import (
    OUTDOOR_AREAS,
    build_outdoor_tiles,
    populate_outdoor_objects,
)
from app.domain.world_gen.placement import assign_and_build_agents
from app.domain.world_gen.relationships import build_matrix
from app.domain.world_gen.types import (
    BuildingPlan,
    HomePlan,
    WorldPlan,
)


def _default_outdoor_width() -> int:
    return get_settings().world_gen_outdoor_default_width


def _default_outdoor_height() -> int:
    return get_settings().world_gen_outdoor_default_height


@dataclass
class GenerationConfig:
    """新游戏生成参数。"""

    seed: int = 42
    outdoor_width: int = field(default_factory=_default_outdoor_width)
    outdoor_height: int = field(default_factory=_default_outdoor_height)
    humans: list[AgentTemplate] = field(default_factory=list)
    animals: list[AgentTemplate] = field(default_factory=list)
    player: AgentTemplate | None = None
    object_density: dict[str, int] | None = None  # 透传给 populate_outdoor_objects

    @classmethod
    def default(cls, seed: int = 42) -> "GenerationConfig":
        """默认 22 NPC + 默认玩家 + 配置中的室外地图尺寸。"""
        return cls(
            seed=seed,
            humans=list(DEFAULT_HUMAN_TEMPLATES),
            animals=list(DEFAULT_ANIMAL_TEMPLATES),
            player=DEFAULT_PLAYER_TEMPLATE,
        )


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def generate_world(config: GenerationConfig) -> WorldPlan:
    """根据参数生成完整世界计划。"""
    rng = random.Random(config.seed)

    plan = WorldPlan(
        seed=config.seed,
        outdoor_size=(config.outdoor_width, config.outdoor_height),
    )

    # ───── 1. 室外瓦片 + 公共建筑 / 住宅占位 ─────
    tiles, buildings, homes = build_outdoor_tiles(
        config.outdoor_width, config.outdoor_height, rng=rng
    )
    plan.tiles_by_scene[plan.outdoor_scene_id] = tiles
    plan.buildings = buildings
    plan.homes = homes

    w, h = config.outdoor_width, config.outdoor_height
    plan.scenes.append({
        "id": plan.outdoor_scene_id,
        "name": "小镇室外",
        "scene_type": "outdoor",
        "width": w,
        "height": h,
        "tile_size": 32,
        "description": (
            f"{w}×{h} 室外地图：北区河岸住宅、中区商业服务、南区农场森林。"
        ),
    })

    # ───── 2. 室外装饰物 ─────
    object_rows = populate_outdoor_objects(
        config.outdoor_width, config.outdoor_height, tiles,
        rng=rng, config=config.object_density,
    )
    for o in object_rows:
        o.setdefault("id", _new_id("obj_outdoor"))
        o["scene_id"] = plan.outdoor_scene_id
        plan.world_objects.append(o)

    # ───── 3. 公共建筑室内 ─────
    for b in buildings:
        plan.scenes.append({
            "id": b.interior_scene_id,
            "name": f"{b.name}（室内）",
            "scene_type": "indoor",
            "width": INDOOR_W,
            "height": INDOOR_H,
            "tile_size": 32,
            "description": b.description or "",
        })
        plan.tiles_by_scene[b.interior_scene_id] = build_indoor_tiles(entry_x=7)
        # 家具
        for fname, fx, fy, fw, fh, blk, ints, otype, fstate, ftags in furniture_for_interior(b.interior_kind):
            plan.world_objects.append({
                "id": _new_id(f"obj_{b.key}"),
                "scene_id": b.interior_scene_id,
                "name": fname,
                "object_type": otype,
                "position": {"x": fx, "y": fy},
                "size": {"width": fw, "height": fh},
                "blocks_movement": blk,
                "available_interactions": list(ints),
                "state": dict(fstate),
                "tags": list(ftags),
            })

    # ───── 4. 住宅室内 ─────
    for h in homes:
        plan.scenes.append({
            "id": h.interior_scene_id,
            "name": f"{h.key} 家·室内",
            "scene_type": "indoor",
            "width": INDOOR_W,
            "height": INDOOR_H,
            "tile_size": 32,
            "description": "",
        })
        plan.tiles_by_scene[h.interior_scene_id] = build_indoor_tiles(entry_x=7)
        # 默认家具（住户的 key 若有 override，则用之）
        for fname, fx, fy, fw, fh, blk, ints, otype, fstate, ftags in furniture_for_interior("home", h.key):
            plan.world_objects.append({
                "id": _new_id(f"obj_home_{h.key}"),
                "scene_id": h.interior_scene_id,
                "name": fname,
                "object_type": otype,
                "position": {"x": fx, "y": fy},
                "size": {"width": fw, "height": fh},
                "blocks_movement": blk,
                "available_interactions": list(ints),
                "state": dict(fstate),
                "tags": list(ftags),
            })

    # ───── 5. Locations（室外建筑外壳 + 室内 + 公共户外区） ─────
    for b in buildings:
        plan.locations.append({
            "id": b.location_id,
            "scene_id": plan.outdoor_scene_id,
            "name": b.name,
            "location_type": "building",
            "bounds": b.bounds,
            "entry_tiles": [{"x": b.door[0], "y": b.door[1]}],
            "open_hours": b.open_hours,
            "tags": list(b.tags),
            "description": b.description,
        })
        plan.locations.append({
            "id": b.interior_location_id,
            "scene_id": b.interior_scene_id,
            "name": f"{b.name}·室内",
            "location_type": "room",
            "bounds": {"x": 1, "y": 1, "width": INDOOR_W - 2, "height": INDOOR_H - 2},
            "entry_tiles": [{"x": 7, "y": INDOOR_H - 2}],
            "open_hours": b.open_hours,
            "tags": [f"{b.key}_interior"],
            "description": "",
        })

    for h in homes:
        plan.locations.append({
            "id": h.location_id,
            "scene_id": plan.outdoor_scene_id,
            "name": h.name,
            "location_type": "building",
            "bounds": h.bounds,
            "entry_tiles": [{"x": h.door[0], "y": h.door[1]}],
            "open_hours": None,
            "tags": ["home"],
            "description": "",
        })
        plan.locations.append({
            "id": h.interior_location_id,
            "scene_id": h.interior_scene_id,
            "name": f"{h.name}·室内",
            "location_type": "room",
            "bounds": {"x": 1, "y": 1, "width": INDOOR_W - 2, "height": INDOOR_H - 2},
            "entry_tiles": [{"x": 7, "y": INDOOR_H - 2}],
            "open_hours": None,
            "tags": ["home_interior", "rest"],
            "description": "",
        })

    # 公共户外区
    for area in OUTDOOR_AREAS:
        plan.locations.append({
            "id": area["id"],
            "scene_id": plan.outdoor_scene_id,
            "name": area["name"],
            "location_type": "outdoor_area",
            "bounds": area["bounds"],
            "entry_tiles": [{"x": area["entry"][0], "y": area["entry"][1]}],
            "open_hours": None,
            "tags": list(area["tags"]),
            "description": area.get("description", ""),
        })

    # ───── 6. Portals（每个建筑/住宅建对内、对外两条） ─────
    def make_portal_pair(
        outdoor_scene: str, outdoor_door: tuple[int, int],
        indoor_scene: str, indoor_entry: tuple[int, int],
        outside_step: tuple[int, int], key: str,
    ) -> list[dict[str, Any]]:
        return [
            {
                "id": f"portal_{key}_in",
                "from_scene_id": outdoor_scene,
                "from_tile": {"x": outdoor_door[0], "y": outdoor_door[1]},
                "to_scene_id": indoor_scene,
                "to_tile": {"x": indoor_entry[0], "y": indoor_entry[1]},
                "interaction_type": "auto_enter",
                "requires_permission": False,
                "name": f"{key} · 入",
            },
            {
                "id": f"portal_{key}_out",
                "from_scene_id": indoor_scene,
                "from_tile": {"x": indoor_entry[0], "y": indoor_entry[1] + 1},
                "to_scene_id": outdoor_scene,
                "to_tile": {"x": outside_step[0], "y": outside_step[1]},
                "interaction_type": "auto_enter",
                "requires_permission": False,
                "name": f"{key} · 出",
            },
        ]

    for b in buildings:
        outside = (b.door[0], b.door[1] + 1)
        plan.portals.extend(make_portal_pair(
            plan.outdoor_scene_id, b.door,
            b.interior_scene_id, (7, INDOOR_H - 2),
            outside, b.key,
        ))
    for h in homes:
        outside = (h.door[0], h.door[1] + 1)
        plan.portals.extend(make_portal_pair(
            plan.outdoor_scene_id, h.door,
            h.interior_scene_id, (7, INDOOR_H - 2),
            outside, f"home_{h.key}",
        ))

    # ───── 7. NPC / 玩家 ─────
    agent_rows, state_rows, home_assignment = assign_and_build_agents(
        config.humans, config.animals, config.player,
        homes=homes, buildings=buildings,
        outdoor_scene_id=plan.outdoor_scene_id,
    )
    plan.agents = agent_rows
    plan.agent_states = state_rows

    # ───── 8. 关系矩阵 ─────
    plan.relationships = build_matrix(
        config.humans, config.animals,
        player_id=config.player.id if config.player else None,
    )

    return plan
