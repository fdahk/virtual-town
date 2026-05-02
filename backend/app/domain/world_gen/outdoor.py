"""120×90 室外大地图布局生成。

布局策略（PM 选定的"参数化模板"）：
- 北区 (y: 0-29)：河流(y=4..5) + 住宅带(y=22..27)
- 中区 (y: 30-59)：商业 + 公共服务 + 中央广场
- 南区 (y: 60-89)：农场 + 木工坊 + 森林 + 山间小屋

道路：
- 主街 v：x=59,60 (贯穿全图)
- 横向：y=14..15 (北)、y=44..45 (中)、y=74..75 (南)
- 桥：x=58..62 在 y=4..5 横跨河流

可参数化的部分：
- 建筑/住宅位置在 ``BUILDING_LAYOUT`` / ``HOME_LAYOUT`` 中按区域定义
- 装饰物（树/花/蘑菇/果实）由 RNG seed 决定具体位置，但密度参数化
"""

from __future__ import annotations

import random
from typing import Any

from app.domain.world_gen.types import BuildingPlan, HomePlan

# ---------------------------------------------------------------------------
# 静态布局数据
# ---------------------------------------------------------------------------

# 公共建筑：(key, name, bounds, door, terrain_ext, terrain_door, interior_kind, tags, open_hours)
BUILDING_LAYOUT: list[dict[str, Any]] = [
    # ─── 中区商业街（y=33-39 上排）───
    {"key": "cafe", "name": "Hobbs 咖啡店",
     "bounds": {"x": 8, "y": 35, "width": 6, "height": 5}, "door": (10, 39),
     "terrain_ext": "cafe_ext", "terrain_door": "cafe_door",
     "interior_kind": "cafe", "tags": ["cafe", "social", "workplace"],
     "open_hours": {"start": "08:00", "end": "22:00"},
     "description": "小芳工作的咖啡店。"},
    {"key": "school", "name": "小镇学校",
     "bounds": {"x": 50, "y": 32, "width": 10, "height": 7}, "door": (54, 38),
     "terrain_ext": "school_ext", "terrain_door": "school_door",
     "interior_kind": "school", "tags": ["school", "education"],
     "open_hours": {"start": "08:00", "end": "17:00"},
     "description": "小镇唯一一所学校。"},
    {"key": "grocery", "name": "陈伯杂货店",
     "bounds": {"x": 78, "y": 35, "width": 6, "height": 5}, "door": (80, 39),
     "terrain_ext": "grocery_ext", "terrain_door": "grocery_door",
     "interior_kind": "grocery", "tags": ["grocery"],
     "open_hours": {"start": "07:00", "end": "21:00"},
     "description": "货架琳琅满目的小杂货铺，陈伯在这里守了很多年。"},

    # ─── 中区商业街（y=46-50 下排）───
    {"key": "flower", "name": "阿言花店",
     "bounds": {"x": 8, "y": 46, "width": 6, "height": 5}, "door": (10, 50),
     "terrain_ext": "flower_ext", "terrain_door": "flower_door",
     "interior_kind": "flower", "tags": ["flower_shop", "nature"],
     "open_hours": {"start": "09:00", "end": "19:00"},
     "description": "散发着花香的小花房。"},
    {"key": "post", "name": "小镇邮局",
     "bounds": {"x": 25, "y": 46, "width": 6, "height": 5}, "door": (27, 50),
     "terrain_ext": "post_ext", "terrain_door": "post_door",
     "interior_kind": "post", "tags": ["post", "service"],
     "open_hours": {"start": "08:00", "end": "18:00"},
     "description": "月玲打理的邮局，墙上挂着小镇地图。"},
    {"key": "library", "name": "小镇图书馆",
     "bounds": {"x": 38, "y": 46, "width": 8, "height": 5}, "door": (41, 50),
     "terrain_ext": "library_ext", "terrain_door": "library_door",
     "interior_kind": "library", "tags": ["library", "education"],
     "open_hours": {"start": "08:30", "end": "21:30"},
     "description": "藏书丰富的小镇图书馆，傍晚常有学生来自习。"},
    {"key": "bakery", "name": "晨光面包店",
     "bounds": {"x": 65, "y": 46, "width": 6, "height": 5}, "door": (67, 50),
     "terrain_ext": "bakery_ext", "terrain_door": "bakery_door",
     "interior_kind": "bakery", "tags": ["bakery", "food"],
     "open_hours": {"start": "06:00", "end": "20:00"},
     "description": "凌晨四点就开始飘出面包香味的小店。"},
    {"key": "tavern", "name": "阿欣酒馆",
     "bounds": {"x": 78, "y": 46, "width": 8, "height": 5}, "door": (81, 50),
     "terrain_ext": "tavern_ext", "terrain_door": "tavern_door",
     "interior_kind": "tavern", "tags": ["tavern", "social"],
     "open_hours": {"start": "17:00", "end": "00:30"},
     "description": "夜晚最热闹的角落，温暖的橙黄灯光。"},

    # ─── 南区 ───
    {"key": "farmhouse", "name": "李芳农场",
     "bounds": {"x": 40, "y": 65, "width": 10, "height": 8}, "door": (44, 72),
     "terrain_ext": "farmhouse_ext", "terrain_door": "farmhouse_door",
     "interior_kind": "farmhouse", "tags": ["farm", "workplace"],
     "open_hours": {"start": "05:00", "end": "20:00"},
     "description": "南郊的农场屋，外面是大片麦田。"},
    {"key": "woodshop", "name": "老穆木工坊",
     "bounds": {"x": 90, "y": 65, "width": 6, "height": 5}, "door": (92, 69),
     "terrain_ext": "woodshop_ext", "terrain_door": "woodshop_door",
     "interior_kind": "woodshop", "tags": ["craft", "workplace"],
     "open_hours": {"start": "07:00", "end": "19:00"},
     "description": "弥漫木屑香气的小作坊。"},
]


# 12 处独立住宅 + 1 处农场屋住宅；
# 通过 (key, district, x, y) 定义，width/height 默认 4×3
HOME_LAYOUT: list[dict[str, Any]] = [
    # ─── 北区 6 户 ───
    {"key": "xiaofang", "district": "north", "x": 4,   "y": 22},
    {"key": "xiaoming", "district": "north", "x": 16,  "y": 22},
    {"key": "xiaowang", "district": "north", "x": 28,  "y": 22},
    {"key": "linna",    "district": "north", "x": 78,  "y": 22},
    {"key": "chenbo",   "district": "north", "x": 90,  "y": 22},
    {"key": "ayan",     "district": "north", "x": 102, "y": 22},
    # ─── 中区 4 户 ───
    {"key": "lihua",    "district": "center", "x": 4,   "y": 53},
    {"key": "xiaoyu",   "district": "center", "x": 16,  "y": 53},
    {"key": "yueling",  "district": "center", "x": 100, "y": 53},
    {"key": "xiaoke",   "district": "center", "x": 112, "y": 53},
    # ─── 南区 2 户 ───
    {"key": "laomu",    "district": "south",  "x": 78, "y": 80},
    {"key": "xiaodi",   "district": "south",  "x": 88, "y": 80},
]


# 公共户外区（park / river_bench / forest_grove）
OUTDOOR_AREAS: list[dict[str, Any]] = [
    {"id": "loc_park", "name": "中央广场",
     "bounds": {"x": 50, "y": 41, "width": 9, "height": 4},
     "entry": (54, 42), "tags": ["plaza", "social"],
     "description": "镇子中心的广场，周末常有市集。"},
    {"id": "loc_river_bench", "name": "河边长椅",
     "bounds": {"x": 26, "y": 7, "width": 3, "height": 2},
     "entry": (27, 7), "tags": ["nature", "rest"],
     "description": "河边的木长椅，风景最好的位置。"},
    {"id": "loc_forest_grove", "name": "南林树丛",
     "bounds": {"x": 8, "y": 70, "width": 24, "height": 18},
     "entry": (16, 72), "tags": ["nature", "forest"],
     "description": "镇南的小林子，蘑菇与野浆果的产地。"},
    {"id": "loc_farmland", "name": "南郊麦田",
     "bounds": {"x": 36, "y": 73, "width": 18, "height": 14},
     "entry": (44, 73), "tags": ["nature", "farm"],
     "description": "李芳家农场的农田，季节性种麦子和蔬菜。"},
]


# ---------------------------------------------------------------------------
# 室外瓦片构造
# ---------------------------------------------------------------------------


def _empty_tile(x: int, y: int, terrain: str = "grass") -> dict[str, Any]:
    return {
        "x": x, "y": y, "terrain": terrain,
        "walkable": True, "blocks_movement": False, "blocks_vision": False,
        "hazard_type": None, "hazard_level": None, "tags": [],
    }


def _assert_layout_within_bounds(width: int, height: int) -> None:
    """硬编码布局必须全部落在 ``width × height`` 内，否则 fail-fast。

    检查项：
        - BUILDING_LAYOUT 的 bounds 完整框 + 门 + 门外一格（portal_out 落点）
        - HOME_LAYOUT 的 4×3 框 + 门 + 门外一格
        - OUTDOOR_AREAS 的 bounds 完整框 + entry tile

    门外一格 (door_x, door_y+1) 是 portal_out.to_tile，必须在地图内才能让
    NPC 从室内 portal 出来时落在合法格上，否则 grid.is_walkable() 会因
    in_bounds 失败永远返回 False。
    """
    errors: list[str] = []

    def _check_box(label: str, x: int, y: int, w: int, h: int) -> None:
        if x < 0 or y < 0 or x + w > width or y + h > height:
            errors.append(
                f"{label} 框 ({x},{y})+{w}x{h} 越出 {width}x{height}"
            )

    def _check_pt(label: str, x: int, y: int) -> None:
        if not (0 <= x < width and 0 <= y < height):
            errors.append(f"{label} 点 ({x},{y}) 越出 {width}x{height}")

    for spec in BUILDING_LAYOUT:
        b = spec["bounds"]
        _check_box(
            f"BUILDING[{spec['key']}]", b["x"], b["y"], b["width"], b["height"]
        )
        dx, dy = spec["door"]
        _check_pt(f"BUILDING[{spec['key']}].door", dx, dy)
        _check_pt(f"BUILDING[{spec['key']}].outside_step", dx, dy + 1)

    home_w, home_h = 4, 3
    for spec in HOME_LAYOUT:
        x0, y0 = spec["x"], spec["y"]
        _check_box(f"HOME[{spec['key']}]", x0, y0, home_w, home_h)
        dx = x0 + home_w // 2
        dy = y0 + home_h - 1
        _check_pt(f"HOME[{spec['key']}].door", dx, dy)
        _check_pt(f"HOME[{spec['key']}].outside_step", dx, dy + 1)

    for area in OUTDOOR_AREAS:
        b = area["bounds"]
        _check_box(f"OUTDOOR_AREA[{area['id']}]", b["x"], b["y"], b["width"], b["height"])
        ex, ey = area["entry"]
        _check_pt(f"OUTDOOR_AREA[{area['id']}].entry", ex, ey)

    if errors:
        raise ValueError(
            "outdoor 地图尺寸 "
            f"{width}x{height} 装不下硬编码 BUILDING/HOME/AREA 布局：\n  - "
            + "\n  - ".join(errors)
            + "\n\n请把 WORLD_GEN_OUTDOOR_DEFAULT_WIDTH/HEIGHT 调到 ≥120/90，"
            "或同步修改 BUILDING_LAYOUT/HOME_LAYOUT/OUTDOOR_AREAS 的坐标。"
        )


def build_outdoor_tiles(
    width: int,
    height: int,
    *,
    rng: random.Random,
) -> tuple[list[dict[str, Any]], list[BuildingPlan], list[HomePlan]]:
    """生成 ``width × height`` 室外瓦片 + 公共建筑 / 住宅占位。

    返回值：
        - tiles: 完整 tile 列表
        - buildings: BuildingPlan 列表（已包含 interior_scene_id 等字段）
        - homes: HomePlan 列表

    **越界检查**（fail-fast）：
        BUILDING_LAYOUT / HOME_LAYOUT / OUTDOOR_AREAS 是硬编码的坐标，
        ``set_tile`` 在越界时静默返回——这会让 location.entry_tiles /
        portal.from_tile 写入地图外的坐标，NPC 永远走不到那个目标，触发
        "暂时不可达"刷屏。这里在生成前先做强校验，越界立刻 ValueError，
        避免静默错乱（详见 ``docs/开发手册/debug/20260502-world-bounds-mismatch.md``）。
    """
    _assert_layout_within_bounds(width, height)
    tiles: list[dict[str, Any]] = [
        _empty_tile(x, y) for y in range(height) for x in range(width)
    ]

    def set_tile(x: int, y: int, **kw: Any) -> None:
        if not (0 <= x < width and 0 <= y < height):
            return
        tiles[y * width + x].update(kw)

    def block_rect(x0: int, y0: int, x1: int, y1: int, terrain: str) -> None:
        for yy in range(y0, y1 + 1):
            for xx in range(x0, x1 + 1):
                set_tile(xx, yy, terrain=terrain, walkable=False,
                         blocks_movement=True, tags=["building"])

    # ─── 道路 ───
    for y in range(height):
        set_tile(59, y, terrain="road", tags=["road"])
        set_tile(60, y, terrain="road", tags=["road"])
    for x in range(width):
        for road_y in (14, 15, 44, 45, 74, 75):
            if road_y < height:
                set_tile(x, road_y, terrain="road", tags=["road"])

    # ─── 河流（北区 y=4..5），桥 x=58..62 ───
    for x in range(width):
        for y in (4, 5):
            if 58 <= x <= 62:
                set_tile(x, y, terrain="bridge", tags=["bridge"])
            else:
                set_tile(x, y, terrain="river", walkable=True, blocks_movement=False,
                         hazard_type="deep_water", hazard_level=8, tags=["water", "danger"])

    # ─── 农田背景（南区 y=73..86 的部分区域）───
    for y in range(73, min(87, height)):
        for x in range(36, min(54, width)):
            # 留出田埂（道路），其它格设为可走的 farmland
            if tiles[y * width + x]["terrain"] == "grass":
                set_tile(x, y, terrain="farmland", tags=["farm"])

    # ─── 森林背景（南区 y=70..87, x=8..32）───
    for y in range(70, min(88, height)):
        for x in range(8, min(32, width)):
            if tiles[y * width + x]["terrain"] == "grass":
                set_tile(x, y, terrain="forest_ground", tags=["forest"])

    # ─── 公共建筑 ───
    buildings: list[BuildingPlan] = []
    for spec in BUILDING_LAYOUT:
        b = spec["bounds"]
        x0, y0 = b["x"], b["y"]
        x1, y1 = x0 + b["width"] - 1, y0 + b["height"] - 1
        block_rect(x0, y0, x1, y1, spec["terrain_ext"])
        dx, dy = spec["door"]
        set_tile(dx, dy, terrain=spec["terrain_door"], walkable=True,
                 blocks_movement=False, tags=["entry"])
        bp = BuildingPlan(
            key=spec["key"],
            name=spec["name"],
            location_id=f"loc_{spec['key']}" if spec["key"] != "cafe" else "loc_hobbs_cafe",
            interior_location_id=(f"loc_{spec['key']}_interior"
                                   if spec["key"] != "cafe" else "loc_hobbs_cafe_interior"),
            interior_scene_id=f"scene_{spec['key']}_interior",
            bounds=b,
            door=(dx, dy),
            interior_kind=spec["interior_kind"],
            open_hours=spec.get("open_hours"),
            tags=spec["tags"],
            description=spec.get("description", ""),
        )
        buildings.append(bp)

    # ─── 住宅 ───
    homes: list[HomePlan] = []
    home_w, home_h = 4, 3
    for spec in HOME_LAYOUT:
        x0, y0 = spec["x"], spec["y"]
        x1, y1 = x0 + home_w - 1, y0 + home_h - 1
        block_rect(x0, y0, x1, y1, "home_ext")
        dx = x0 + home_w // 2
        dy = y1
        set_tile(dx, dy, terrain="home_door", walkable=True,
                 blocks_movement=False, tags=["entry"])
        npc_id = f"npc_{spec['key']}"
        hp = HomePlan(
            key=spec["key"],
            npc_id=npc_id,
            name=f"{spec['key']} 的家",
            location_id=f"loc_home_{spec['key']}",
            interior_location_id=f"loc_home_{spec['key']}_interior",
            interior_scene_id=f"scene_home_{spec['key']}_interior",
            bounds={"x": x0, "y": y0, "width": home_w, "height": home_h},
            door=(dx, dy),
            occupants=[npc_id],
        )
        homes.append(hp)

    return tiles, buildings, homes


def populate_outdoor_objects(
    width: int,
    height: int,
    tiles: list[dict[str, Any]],
    *,
    rng: random.Random,
    config: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """根据 RNG 在合适位置放置树/花/蘑菇/水果/萤火虫等 WorldObject。

    config 可选键（默认值）：
        ``num_trees`` (60), ``num_flowers`` (40), ``num_mushrooms`` (12),
        ``num_fruits`` (8), ``num_fishing_spots`` (3)
    """

    cfg = {"num_trees": 60, "num_flowers": 40, "num_mushrooms": 16,
           "num_fruits": 10, "num_fishing_spots": 3}
    cfg.update(config or {})

    def is_grass_or_forest(x: int, y: int) -> bool:
        if not (0 <= x < width and 0 <= y < height):
            return False
        t = tiles[y * width + x]
        return t["terrain"] in {"grass", "grass_flower", "forest_ground"} and t["walkable"]

    def is_river(x: int, y: int) -> bool:
        if not (0 <= x < width and 0 <= y < height):
            return False
        return tiles[y * width + x]["terrain"] == "river"

    objects: list[dict[str, Any]] = []
    used: set[tuple[int, int]] = set()

    def random_grass_pos(predicate=is_grass_or_forest, max_tries: int = 50) -> tuple[int, int] | None:
        for _ in range(max_tries):
            x = rng.randrange(0, width)
            y = rng.randrange(0, height)
            if (x, y) in used:
                continue
            if predicate(x, y):
                used.add((x, y))
                return x, y
        return None

    # 树（中等密度，森林区域更密）
    tree_variants = ["tree_pine", "tree_pine_2", "tree_pine_3", "tree_yellow"]
    for _ in range(cfg["num_trees"]):
        pos = random_grass_pos()
        if not pos:
            continue
        kind = rng.choice(tree_variants)
        objects.append({
            "object_type": "decoration",
            "name": kind,
            "position": {"x": pos[0], "y": pos[1]},
            "size": {"width": 1, "height": 1},
            "blocks_movement": False,
            "available_interactions": ["inspect"],
            "state": {"tile_frame_overlay": kind},
            "tags": ["tree", "decoration"],
        })

    # 花（轻量地衣覆盖）
    for _ in range(cfg["num_flowers"]):
        pos = random_grass_pos()
        if not pos:
            continue
        x, y = pos
        # 花会替换草皮为 grass_flower
        if tiles[y * width + x]["terrain"] == "grass":
            tiles[y * width + x]["terrain"] = "grass_flower"

    # 蘑菇（可采集；tags 含 "mushroom_spawn" 让 natural_events.MushroomSpawnHandler
    # 命中并维护 mushroom_present 周期；初始即开启供玩家立刻能采）
    for _ in range(cfg["num_mushrooms"]):
        pos = random_grass_pos(lambda x, y: 0 <= x < width and 0 <= y < height
                                and tiles[y * width + x]["terrain"] == "forest_ground")
        if not pos:
            continue
        objects.append({
            "object_type": "nature_spot",
            "name": "mushroom",
            "position": {"x": pos[0], "y": pos[1]},
            "size": {"width": 1, "height": 1},
            "blocks_movement": False,
            "available_interactions": ["inspect", "take", "pick"],
            "state": {
                "tile_frame_overlay": "mushroom",  # 前端 manifest.overlay_tiles → kenney 帧 29
                "effect": "mushroom",
                "mushroom_present": True,
            },
            "tags": ["mushroom", "mushroom_spawn", "edible"],
        })

    # 水果（果树；tags 含 "fruit_tree" 让 FruitRipenHandler 命中；
    # "plant" 让前端 TownScene._drawPlant 程序化绘制成绿色灌木造型）
    for _ in range(cfg["num_fruits"]):
        pos = random_grass_pos()
        if not pos:
            continue
        objects.append({
            "object_type": "plant",
            "name": "fruit_tree",
            "position": {"x": pos[0], "y": pos[1]},
            "size": {"width": 1, "height": 1},
            "blocks_movement": False,
            "available_interactions": ["inspect", "take", "pick"],
            "state": {"effect": "fruit", "fruit_ripe": True},
            "tags": ["fruit", "fruit_tree", "plant", "edible"],
        })

    # 钓点（河流上；tags 含 "fishing_spot" 让 FishingSpotHandler 命中并周期切换 fishing_active）
    fish_placed = 0
    for _ in range(cfg["num_fishing_spots"] * 6):
        if fish_placed >= cfg["num_fishing_spots"]:
            break
        x = rng.randrange(0, width)
        y = rng.choice([4, 5])
        if (x, y) in used or not is_river(x, y):
            continue
        used.add((x, y))
        objects.append({
            "object_type": "nature_spot",
            "name": "fishing_spot",
            "position": {"x": x, "y": y},
            "size": {"width": 1, "height": 1},
            "blocks_movement": False,
            "available_interactions": ["fish", "inspect"],
            "state": {"effect": "fishing_spot", "fishing_active": True},
            "tags": ["fishing", "fishing_spot", "water"],
        })
        fish_placed += 1

    # 河边长椅（在 loc_river_bench 区域内，给 BenchRestEventHandler / NPC 休憩使用；
    # tile_frame=61 + kenney_tiny_dungeon = 椅子精灵，与室内椅子保持视觉一致）
    for bench_x in (26, 28):
        if 0 <= bench_x < width and 0 <= 7 < height:
            objects.append({
                "object_type": "furniture",
                "name": "park_bench",
                "position": {"x": bench_x, "y": 7},
                "size": {"width": 1, "height": 1},
                "blocks_movement": False,
                "available_interactions": ["sit", "inspect"],
                "state": {"tile_frame": 61, "tileset": "kenney_tiny_dungeon"},
                "tags": ["bench", "rest", "outdoor"],
            })

    # 河边护栏（y=3 上一行）
    for x in range(width):
        if x in range(58, 63):  # 桥两侧不挡
            continue
        if 0 <= 3 < height:
            objects.append({
                "object_type": "barrier",
                "name": "river_fence",
                "position": {"x": x, "y": 3},
                "size": {"width": 1, "height": 1},
                "blocks_movement": True,
                "available_interactions": ["inspect"],
                "state": {"tile_frame_overlay": "fence_h"},
                "tags": ["fence", "decoration"],
            })

    return objects
