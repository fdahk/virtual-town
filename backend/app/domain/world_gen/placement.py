"""把 NPC 模板分配到具体住宅 / 工作场所，并解析 schedule 占位符。

输入：
- ``humans`` / ``animals`` / ``player`` 模板列表
- 由 ``outdoor`` 已构造好的 ``buildings`` / ``homes``

输出：
- ``agent_rows``：可直接写入 ``agents`` 表的字典
- ``state_rows``：对应 ``agent_states``
- ``home_assignment``：``{npc_id: HomePlan}`` 用于关系/事件
"""

from __future__ import annotations

from typing import Any

from app.db.templates.art_catalog import OCCUPATION_PRESETS, resolve_schedule
from app.db.templates.npc_defaults import AgentTemplate
from app.domain.world_gen.types import BuildingPlan, HomePlan


# ``upstairs_of`` → 公共建筑 key 的映射（与 npc_defaults.py 中字段对齐）
UPSTAIRS_BUILDING: dict[str, str] = {
    "bakery": "bakery",
    "tavern": "tavern",
    "cafe": "cafe",
}


def assign_and_build_agents(
    humans: list[AgentTemplate],
    animals: list[AgentTemplate],
    player: AgentTemplate | None,
    *,
    homes: list[HomePlan],
    buildings: list[BuildingPlan],
    outdoor_scene_id: str,
    outdoor_width: int = 200,
    outdoor_height: int = 200,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, HomePlan]]:
    """把 NPC 分配到住宅 / 工作场所。

    分配规则（按 NPC 字段）：
    1. ``cohabits_with``：与某个 NPC 同住（占用对方的 HomePlan）
    2. ``upstairs_of``：住在某公共建筑楼上（不分配独立住宅，
       室外场景用建筑 location，室内仍指向建筑室内）
    3. 否则：从 ``homes`` 中按照 ``preferred_district`` 顺序消费一个
    """
    homes_by_key = {h.key: h for h in homes}
    buildings_by_key = {b.key: b for b in buildings}

    # 把 NPC 分配到住宅；先处理普通分配，再合并 cohabit / upstairs
    home_assignment: dict[str, HomePlan] = {}

    # NPC 想要独立住宅的子集
    standalone = [
        h for h in humans
        if h.cohabits_with is None and h.upstairs_of is None and h.has_home
    ]

    used_home_keys: set[str] = set()
    # 优先把 home key 与 npc id 对齐（默认 22 NPC 的住宅 key 直接就是 NPC short id）
    for npc in standalone:
        short_id = npc.id.removeprefix("npc_")
        if short_id in homes_by_key and short_id not in used_home_keys:
            home_assignment[npc.id] = homes_by_key[short_id]
            used_home_keys.add(short_id)

    # 还有未分配住宅的（自定义 NPC）→ 找尚未占用的住宅
    available_homes = [h for h in homes if h.key not in used_home_keys]
    for npc in standalone:
        if npc.id in home_assignment:
            continue
        if not available_homes:
            break
        h = available_homes.pop(0)
        h.occupants = [npc.id]
        home_assignment[npc.id] = h
        used_home_keys.add(h.key)

    # cohabit：复用宿主的住宅
    for npc in humans:
        if npc.cohabits_with and npc.cohabits_with in home_assignment:
            host_home = home_assignment[npc.cohabits_with]
            host_home.occupants.append(npc.id)
            home_assignment[npc.id] = host_home

    # 室内 / 室外 location 解析辅助
    def home_loc_for(npc_id: str) -> tuple[str, str]:
        h = home_assignment.get(npc_id)
        if h:
            return h.location_id, h.interior_location_id
        # 无 home 的（upstairs/animal）走对应建筑
        return ("loc_park", "loc_park")

    def building_for(npc: AgentTemplate) -> BuildingPlan | None:
        if npc.upstairs_of and npc.upstairs_of in buildings_by_key:
            return buildings_by_key[npc.upstairs_of]
        if npc.occupation:
            preset = OCCUPATION_PRESETS.get(npc.occupation, {})
            wp = preset.get("workplace")
            if wp:
                key = wp.replace("loc_", "").replace("_interior", "")
                # 兼容 "loc_hobbs_cafe_interior" → "hobbs_cafe" → cafe
                if key.startswith("hobbs_"):
                    key = key.replace("hobbs_", "")
                return buildings_by_key.get(key)
        return None

    agent_rows: list[dict[str, Any]] = []
    state_rows: list[dict[str, Any]] = []

    # 默认初始位置：从 home 门口；upstairs 从建筑门口；player 从中央广场
    def make_agent_row(npc: AgentTemplate, *, is_player: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
        # 解析 home_location_id 给 agents.home_location_id
        if npc.entity_type == "animal":
            owner_id = npc.owner_id
            if owner_id and owner_id in home_assignment:
                h = home_assignment[owner_id]
                home_loc = h.location_id
                interior_loc = h.interior_location_id
                start_x, start_y = h.door
            else:
                home_loc = None
                interior_loc = None
                start_x, start_y = 60, 42  # 中央广场附近
        elif npc.upstairs_of:
            b = buildings_by_key.get(npc.upstairs_of)
            if b:
                home_loc = b.location_id
                interior_loc = b.interior_location_id
                start_x, start_y = b.door
            else:
                home_loc = None
                interior_loc = None
                start_x, start_y = 60, 42
        elif is_player:
            home_loc = None
            interior_loc = None
            start_x, start_y = 54, 42  # 中央广场
        else:
            home_loc, interior_loc = home_loc_for(npc.id)
            h = home_assignment.get(npc.id)
            if h:
                start_x, start_y = h.door
            else:
                start_x, start_y = 60, 42

        # 安全兜底：将出生坐标钳制在地图范围内，防止地图尺寸偏小时坐标越界
        # 导致角色不可见（e.g. 玩家固定 (54,42) 在 40×30 地图中越界）。
        start_x = max(0, min(start_x, outdoor_width - 1))
        start_y = max(0, min(start_y, outdoor_height - 1))

        # schedule 解析
        schedule: list[dict[str, Any]] = []
        if npc.schedule_id:
            building = building_for(npc)
            workplace_loc_id = building.interior_location_id if building else None
            home_interior = interior_loc or "loc_park"
            schedule = resolve_schedule(
                npc.schedule_id,
                home_location_id=home_interior,
                workplace_location_id=workplace_loc_id,
            )

        appearance: dict[str, Any] = {
            "color": npc.accent_color,
            "kind": npc.art.get("kind", "lpc_human"),
            "sheet_id": npc.art.get("sheet_id"),
            "layers": npc.art.get("layers", {}),
            "color_key": npc.art.get("color"),  # 动物
            "species": npc.species,
            "portrait_url": npc.portrait_url,
        }
        agent = {
            "id": npc.id,
            "entity_type": npc.entity_type if not is_player else "player",
            "name": npc.name,
            "age": npc.age,
            "gender": npc.gender,
            "species": npc.species or ("human" if npc.entity_type == "human" else None),
            "occupation": npc.occupation,
            "personality": list(npc.personality),
            "background": npc.background,
            "lifestyle": npc.lifestyle,
            "appearance": appearance,
            "long_term_goals": list(npc.long_term_goals),
            "home_location_id": home_loc,
            "schedule_template": schedule,
            "is_active": True,
        }
        state = {
            "agent_id": npc.id,
            "scene_id": outdoor_scene_id,
            "x": start_x,
            "y": start_y,
            "state": "IDLE",
            "emotion": None,
            "energy": 1.0,
            "hunger": 0.2,
            "social_need": 0.3,
            "fear": 0.0,
            "facing": "down",
        }
        return agent, state

    for npc in humans:
        a, s = make_agent_row(npc)
        agent_rows.append(a)
        state_rows.append(s)

    for npc in animals:
        a, s = make_agent_row(npc)
        agent_rows.append(a)
        state_rows.append(s)

    if player:
        a, s = make_agent_row(player, is_player=True)
        agent_rows.append(a)
        state_rows.append(s)

    return agent_rows, state_rows, home_assignment
