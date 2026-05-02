"""world_gen 内部使用的数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class BuildingPlan:
    """单个公共建筑（cafe / school / library / ...）的布局。

    bounds 中所有坐标均针对 outdoor scene。``door`` 必须在 bounds 底部一行内，
    保证 portal 入/出格连接的对称性。
    """

    key: str  # cafe / school / library / ...
    name: str
    location_id: str  # outdoor location（loc_hobbs_cafe）
    interior_location_id: str  # 室内 location（loc_hobbs_cafe_interior）
    interior_scene_id: str
    bounds: dict[str, int]  # {x, y, width, height}
    door: tuple[int, int]  # outdoor door tile
    interior_size: tuple[int, int] = (16, 12)
    interior_entry: tuple[int, int] = (7, 10)  # 室内入口（站位）
    interior_kind: str = "generic"  # 影响家具：cafe/school/grocery/flower/library/bakery/tavern/post/woodshop/farmhouse
    open_hours: dict[str, str] | None = None
    tags: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class HomePlan:
    """单个 NPC 住宅的布局。

    ``key`` 通常等于 ``npc.id`` 去掉 ``npc_`` 前缀，也是 location_id 后缀。
    住宅尺寸固定 4×3（与现有 seed.py 对齐），便于 portal 计算。
    """

    key: str
    npc_id: str
    name: str
    location_id: str  # loc_home_<key>
    interior_location_id: str
    interior_scene_id: str
    bounds: dict[str, int]
    door: tuple[int, int]
    interior_size: tuple[int, int] = (16, 12)
    interior_entry: tuple[int, int] = (7, 8)
    occupants: list[str] = field(default_factory=list)  # 住在这里的 NPC ID（含主人 + 同住者 + 宠物）


@dataclass
class WorldPlan:
    """生成器输出的完整世界计划。

    ``apply_plan`` 据此填表；前端 ``GET /games/state`` 也可以序列化此结构
    作为 "保存" 的快照。
    """

    seed: int
    outdoor_size: tuple[int, int]
    outdoor_scene_id: str = "scene_town_outdoor"

    scenes: list[dict[str, Any]] = field(default_factory=list)
    tiles_by_scene: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    locations: list[dict[str, Any]] = field(default_factory=list)
    portals: list[dict[str, Any]] = field(default_factory=list)
    world_objects: list[dict[str, Any]] = field(default_factory=list)
    agents: list[dict[str, Any]] = field(default_factory=list)
    agent_states: list[dict[str, Any]] = field(default_factory=list)
    relationships: list[dict[str, Any]] = field(default_factory=list)

    buildings: list[BuildingPlan] = field(default_factory=list)
    homes: list[HomePlan] = field(default_factory=list)


# 公共建筑的 key 列表（用于生成器决定要不要造该建筑）
PUBLIC_BUILDING_KEYS: tuple[str, ...] = (
    "cafe",       # 咖啡店
    "school",     # 学校
    "grocery",    # 杂货店
    "flower",     # 花店
    "library",    # 图书馆
    "bakery",     # 面包店
    "tavern",     # 酒馆
    "post",       # 邮局
    "woodshop",   # 木工坊
    "farmhouse",  # 农场屋
)

District = Literal["north", "center", "south"]
