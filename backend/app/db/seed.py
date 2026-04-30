"""
种子数据脚本。

生成：
- 室外大地图 + 3 个室内地图（咖啡店、学校、杂货店）
- 6 个人类 NPC（小明、小芳、小王、林娜、陈伯、阿言）
- 2 只狗（豆豆、黑黑）+ 2 只猫（咪咪、小白）
- 初始关系网络
- 默认玩家角色

使用：
  python -m app.db.seed                # 强制重建（保留表结构）
  python -m app.db.seed --if-empty     # 数据库无 agent 时才执行
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
    Agent,
    AgentAction,
    AgentState,
    DialogueMessage,
    Location,
    MapScene,
    MapTile,
    Memory,
    Portal,
    Relationship,
    Simulation,
    WorldEvent,
    WorldObject,
)


OUTDOOR_ID = "scene_town_outdoor"
CAFE_ID = "scene_cafe_inside"
SCHOOL_ID = "scene_school_inside"
GROCERY_ID = "scene_grocery_inside"

OUTDOOR_W, OUTDOOR_H = 40, 30
INDOOR_W, INDOOR_H = 16, 12

# 6 处 NPC 住宅室内场景 ID
HOME_SCENE_IDS: dict[str, str] = {
    "xiaofang": "scene_home_xiaofang_inside",
    "xiaoming": "scene_home_xiaoming_inside",
    "xiaowang": "scene_home_xiaowang_inside",
    "linna":    "scene_home_linna_inside",
    "chenbo":   "scene_home_chenbo_inside",
    "ayan":     "scene_home_ayan_inside",
}


# ----------------------------------------------------------------------
# 地图构造
# ----------------------------------------------------------------------


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def build_outdoor_tiles() -> list[dict[str, Any]]:
    """
    构造一张 40x30 的室外地图：
    - 默认草地
    - 垂直道路：x=19, 20
    - 水平道路：y=14, 15
    - 河流：y=5（宽 2 格），x 从 0 到 40；x in [12..16] 为桥（walkable，无危险）
    - 河流边 y=4 有护栏对象（由 world_objects 表表达），其余区域可直接落水
    - 四个公共建筑：
        咖啡店 bounds (4-9, 18-22)，入口 (6, 22)
        学校   bounds (24-33, 17-23)，入口 (28, 23)
        杂货店 bounds (30-35, 6-10)，入口 (32, 10)
        花店   bounds (4-8, 8-11)，入口 (6, 11)
    - 六处 NPC 住宅（home_ext）：
        小芳家   blocks (1-4,  24-26)，门 (3, 26)，入口 (3, 27)
        小明家   blocks (10-13,24-26)，门 (12,26)，入口 (12,27)
        小王家   blocks (12-15, 7- 9)，门 (14, 9)，入口 (14,10)
        林娜家   blocks (33-36,24-26)，门 (35,26)，入口 (35,27)
        陈伯家   blocks (36-38,10-11)，门 (37,11)，入口 (37,12)  ← 避开 y=14 道路
        阿言家   blocks (1- 3, 11-12)，门 (2, 12)，入口 (2, 13)
    """
    tiles: list[dict[str, Any]] = []
    # 初始化整张地图为草地
    for y in range(OUTDOOR_H):
        for x in range(OUTDOOR_W):
            tiles.append(
                {
                    "x": x,
                    "y": y,
                    "terrain": "grass",
                    "walkable": True,
                    "blocks_movement": False,
                    "blocks_vision": False,
                    "hazard_type": None,
                    "hazard_level": None,
                    "tags": [],
                }
            )

    def set_tile(x: int, y: int, **kwargs: Any) -> None:
        idx = y * OUTDOOR_W + x
        tiles[idx].update(kwargs)

    # 垂直道路
    for y in range(OUTDOOR_H):
        set_tile(19, y, terrain="road", tags=["road"])
        set_tile(20, y, terrain="road", tags=["road"])
    # 水平道路
    for x in range(OUTDOOR_W):
        set_tile(x, 14, terrain="road", tags=["road"])
        set_tile(x, 15, terrain="road", tags=["road"])

    # 河流：y=4,5（上方）带危险
    for x in range(OUTDOOR_W):
        for y in (4, 5):
            # x 12-16 是桥（木板路）
            if 12 <= x <= 16:
                set_tile(x, y, terrain="bridge", tags=["bridge"])
            else:
                set_tile(
                    x,
                    y,
                    terrain="river",
                    walkable=True,
                    blocks_movement=False,
                    hazard_type="deep_water",
                    hazard_level=8,
                    tags=["water", "danger"],
                )
    # 河岸（y=3）也为 walkable
    # 建筑地块（4 栋）：建筑主体不可走，入口由 portal 补（此处留入口一格 walkable）
    def block_rect(x0: int, y0: int, x1: int, y1: int, terrain: str) -> None:
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                set_tile(
                    x,
                    y,
                    terrain=terrain,
                    walkable=False,
                    blocks_movement=True,
                    tags=["building"],
                )

    # 咖啡店
    block_rect(4, 18, 9, 22, "cafe_ext")
    set_tile(6, 22, terrain="cafe_door", walkable=True, blocks_movement=False, tags=["entry"])
    # 学校
    block_rect(24, 17, 33, 23, "school_ext")
    set_tile(28, 23, terrain="school_door", walkable=True, blocks_movement=False, tags=["entry"])
    # 杂货店
    block_rect(30, 6, 35, 10, "grocery_ext")
    set_tile(32, 10, terrain="grocery_door", walkable=True, blocks_movement=False, tags=["entry"])
    # 花店（装饰）
    block_rect(4, 8, 8, 11, "flower_ext")
    set_tile(6, 11, terrain="flower_door", walkable=True, blocks_movement=False, tags=["entry"])

    # NPC 住宅（6 处）：(x0, y0, x1, y1, door_x, door_y)
    # door 在建筑底行中心，设为 walkable；入口格在 door 正下方一格
    npc_homes = [
        (1,  24, 4,  26, 3,  26),  # 小芳家
        (10, 24, 13, 26, 12, 26),  # 小明家
        (12, 7,  15, 9,  14, 9),   # 小王家
        (33, 24, 36, 26, 35, 26),  # 林娜家
        (36, 10, 38, 11, 37, 11),  # 陈伯家（避开 y=14 道路）
        (1,  11, 3,  12, 2,  12),  # 阿言家
    ]
    for x0, y0, x1, y1, door_x, door_y in npc_homes:
        block_rect(x0, y0, x1, y1, "home_ext")
        set_tile(door_x, door_y, terrain="home_door", walkable=True, blocks_movement=False, tags=["entry"])

    return tiles


def build_indoor_tiles(
    kind: str,
) -> list[dict[str, Any]]:
    """
    16x12 室内房间：
    - 外圈为墙（blocks_movement）
    - 内部为地板（可走）
    - 家具由 world_objects 添加，不改动 tile
    - 入口 tile 在 (entry_x, 11)
    """
    entry_x = {"cafe": 7, "school": 7, "grocery": 7, "home": 7}[kind]
    tiles: list[dict[str, Any]] = []
    for y in range(INDOOR_H):
        for x in range(INDOOR_W):
            is_wall = (
                x == 0 or x == INDOOR_W - 1 or y == 0 or y == INDOOR_H - 1
            )
            if is_wall and not (y == INDOOR_H - 1 and x == entry_x):
                tiles.append(
                    {
                        "x": x,
                        "y": y,
                        "terrain": "wall",
                        "walkable": False,
                        "blocks_movement": True,
                        "blocks_vision": True,
                        "hazard_type": None,
                        "hazard_level": None,
                        "tags": ["wall"],
                    }
                )
            else:
                tiles.append(
                    {
                        "x": x,
                        "y": y,
                        "terrain": "floor",
                        "walkable": True,
                        "blocks_movement": False,
                        "blocks_vision": False,
                        "hazard_type": None,
                        "hazard_level": None,
                        "tags": [],
                    }
                )
    return tiles


# ----------------------------------------------------------------------
# 种子主流程
# ----------------------------------------------------------------------


def seed(session: Session, *, force: bool = True) -> None:
    existing = session.execute(select(Agent).limit(1)).scalar_one_or_none()
    if existing is not None and not force:
        print("[seed] agents already exist, skip")
        return

    print("[seed] clearing old data...")
    # 清理存在的数据；FK CASCADE 会连带清除相关行
    for model in (
        DialogueMessage,
        WorldEvent,
        AgentAction,
        Memory,
        Relationship,
        AgentState,
        Agent,
        WorldObject,
        Portal,
        Location,
        MapTile,
        MapScene,
        Simulation,
    ):
        session.execute(delete(model))
    session.commit()

    # ------------------ Scenes ------------------
    print("[seed] creating scenes & tiles...")
    outdoor = MapScene(
        id=OUTDOOR_ID,
        name="小镇室外",
        scene_type="outdoor",
        width=OUTDOOR_W,
        height=OUTDOOR_H,
        tile_size=32,
        description="AI 小镇的室外大地图：包含街道、河流、桥、咖啡店、学校、杂货店、花店。",
    )
    cafe = MapScene(
        id=CAFE_ID,
        name="Hobbs 咖啡店",
        scene_type="indoor",
        width=INDOOR_W,
        height=INDOOR_H,
        tile_size=32,
        description="温馨的社区咖啡店，吧台、桌椅、咖啡机。",
    )
    school = MapScene(
        id=SCHOOL_ID,
        name="小镇学校",
        scene_type="indoor",
        width=INDOOR_W,
        height=INDOOR_H,
        tile_size=32,
        description="小镇唯一一所学校，教室里有黑板和课桌。",
    )
    grocery = MapScene(
        id=GROCERY_ID,
        name="陈伯杂货店",
        scene_type="indoor",
        width=INDOOR_W,
        height=INDOOR_H,
        tile_size=32,
        description="货架琳琅满目的小杂货铺，陈伯在这里守了很多年。",
    )
    # 6 处 NPC 住宅室内场景
    home_scene_meta: dict[str, tuple[str, str]] = {
        "xiaofang": ("小芳家·卧室",   "小芳的温馨小屋，窗台上摆着盆栽，桌上有诗集。"),
        "xiaoming": ("小明家·书房",   "小明的房间，书桌上堆满教科书和草稿纸。"),
        "xiaowang": ("小王家·工作室", "小王的公寓，显示器常亮，键盘旁有一杯凉咖啡。"),
        "linna":    ("林娜家·卧室",   "林娜的家，简洁整洁，医学书摆满了角落的书架。"),
        "chenbo":   ("陈伯家·厅堂",   "陈伯家，老式家具，墙上挂着旧照片和杂货店的货单。"),
        "ayan":     ("阿言家·花房",   "阿言的小屋，到处都是花盆和猫咪玩具，花香扑鼻。"),
    }
    home_scenes: dict[str, MapScene] = {}
    for key, (scene_name, scene_desc) in home_scene_meta.items():
        hs = MapScene(
            id=HOME_SCENE_IDS[key],
            name=scene_name,
            scene_type="indoor",
            width=INDOOR_W,
            height=INDOOR_H,
            tile_size=32,
            description=scene_desc,
        )
        home_scenes[key] = hs

    session.add_all([outdoor, cafe, school, grocery, *home_scenes.values()])
    session.flush()

    for t in build_outdoor_tiles():
        session.add(MapTile(scene_id=outdoor.id, **t))
    for t in build_indoor_tiles("cafe"):
        session.add(MapTile(scene_id=cafe.id, **t))
    for t in build_indoor_tiles("school"):
        session.add(MapTile(scene_id=school.id, **t))
    for t in build_indoor_tiles("grocery"):
        session.add(MapTile(scene_id=grocery.id, **t))
    for key, hs in home_scenes.items():
        for t in build_indoor_tiles("home"):
            session.add(MapTile(scene_id=hs.id, **t))

    # ------------------ Locations ------------------
    cafe_loc = Location(
        id="loc_hobbs_cafe",
        scene_id=outdoor.id,
        name="Hobbs 咖啡店",
        location_type="building",
        bounds={"x": 4, "y": 18, "width": 6, "height": 5},
        entry_tiles=[{"x": 6, "y": 22}],
        open_hours={"start": "08:00", "end": "22:00"},
        tags=["cafe", "social", "workplace"],
        description="小芳工作的咖啡店。",
    )
    cafe_interior = Location(
        id="loc_hobbs_cafe_interior",
        scene_id=cafe.id,
        name="Hobbs 咖啡店 · 吧台",
        location_type="room",
        bounds={"x": 1, "y": 1, "width": 14, "height": 10},
        entry_tiles=[{"x": 7, "y": 10}],
        tags=["cafe_interior"],
    )
    school_loc = Location(
        id="loc_school",
        scene_id=outdoor.id,
        name="小镇学校",
        location_type="building",
        bounds={"x": 24, "y": 17, "width": 10, "height": 7},
        entry_tiles=[{"x": 28, "y": 23}],
        open_hours={"start": "08:00", "end": "17:00"},
        tags=["school", "education"],
    )
    school_interior = Location(
        id="loc_school_interior",
        scene_id=school.id,
        name="学校 · 教室",
        location_type="room",
        bounds={"x": 1, "y": 1, "width": 14, "height": 10},
        entry_tiles=[{"x": 7, "y": 10}],
        tags=["school_interior"],
    )
    grocery_loc = Location(
        id="loc_grocery",
        scene_id=outdoor.id,
        name="陈伯杂货店",
        location_type="building",
        bounds={"x": 30, "y": 6, "width": 6, "height": 5},
        entry_tiles=[{"x": 32, "y": 10}],
        open_hours={"start": "07:00", "end": "21:00"},
        tags=["grocery"],
    )
    grocery_interior = Location(
        id="loc_grocery_interior",
        scene_id=grocery.id,
        name="杂货店 · 柜台",
        location_type="room",
        bounds={"x": 1, "y": 1, "width": 14, "height": 10},
        entry_tiles=[{"x": 7, "y": 10}],
        tags=["grocery_interior"],
    )
    flower_loc = Location(
        id="loc_flower",
        scene_id=outdoor.id,
        name="阿言花店",
        location_type="building",
        bounds={"x": 4, "y": 8, "width": 5, "height": 4},
        entry_tiles=[{"x": 6, "y": 11}],
        open_hours={"start": "09:00", "end": "19:00"},
        tags=["flower_shop", "nature"],
    )
    river_bench = Location(
        id="loc_river_bench",
        scene_id=outdoor.id,
        name="河边长椅",
        location_type="outdoor_area",
        bounds={"x": 22, "y": 7, "width": 3, "height": 2},
        entry_tiles=[{"x": 23, "y": 7}],
        tags=["nature", "rest"],
    )
    park = Location(
        id="loc_park",
        scene_id=outdoor.id,
        name="中心广场",
        location_type="outdoor_area",
        bounds={"x": 17, "y": 11, "width": 6, "height": 3},
        entry_tiles=[{"x": 19, "y": 12}],
        tags=["plaza", "social"],
    )
    homes = {
        "xiaofang": ("loc_home_xiaofang", {"x": 1, "y": 24, "width": 4, "height": 3}, "小芳的家"),
        "xiaoming": ("loc_home_xiaoming", {"x": 10, "y": 24, "width": 4, "height": 3}, "小明家"),
        "xiaowang": ("loc_home_xiaowang", {"x": 12, "y": 7, "width": 4, "height": 3}, "小王家"),
        "linna":    ("loc_home_linna",    {"x": 33, "y": 24, "width": 4, "height": 3}, "林娜家"),
        "chenbo":   ("loc_home_chenbo",   {"x": 36, "y": 10, "width": 3, "height": 2}, "陈伯家"),
        "ayan":     ("loc_home_ayan",     {"x": 1,  "y": 11, "width": 3, "height": 2}, "阿言家"),
    }
    all_locs = [
        cafe_loc, cafe_interior,
        school_loc, school_interior,
        grocery_loc, grocery_interior,
        flower_loc, river_bench, park,
    ]
    for key, (loc_id, bounds, name) in homes.items():
        door_y = bounds["y"] + bounds["height"] - 1   # 建筑底行（walkable door tile）
        entry_y = bounds["y"] + bounds["height"]       # 门外一格
        door_x = bounds["x"] + bounds["width"] // 2
        # 室外 Location（建筑外壳 + 入口格）
        all_locs.append(
            Location(
                id=loc_id,
                scene_id=outdoor.id,
                name=name,
                location_type="building",
                bounds=bounds,
                entry_tiles=[{"x": door_x, "y": door_y}],  # 门片格作为入口
                tags=["home"],
            )
        )
        # 室内 Location（卧室/起居区）
        all_locs.append(
            Location(
                id=f"{loc_id}_interior",
                scene_id=HOME_SCENE_IDS[key],
                name=f"{name}（室内）",
                location_type="room",
                bounds={"x": 1, "y": 1, "width": 14, "height": 10},
                entry_tiles=[{"x": 7, "y": 8}],   # 室内中央区域
                tags=["home_interior", "rest"],
            )
        )
    session.add_all(all_locs)

    # ------------------ Portals ------------------
    portals = [
        # outdoor → cafe
        Portal(
            id="portal_cafe_in",
            from_scene_id=outdoor.id,
            from_tile={"x": 6, "y": 22},
            to_scene_id=cafe.id,
            to_tile={"x": 7, "y": 9},
            interaction_type="auto_enter",
            requires_permission=False,
            name="咖啡店门（入）",
        ),
        Portal(
            id="portal_cafe_out",
            from_scene_id=cafe.id,
            from_tile={"x": 7, "y": 11},
            to_scene_id=outdoor.id,
            to_tile={"x": 6, "y": 23},
            interaction_type="auto_enter",
            requires_permission=False,
            name="咖啡店门（出）",
        ),
        Portal(
            id="portal_school_in",
            from_scene_id=outdoor.id,
            from_tile={"x": 28, "y": 23},
            to_scene_id=school.id,
            to_tile={"x": 7, "y": 9},
            interaction_type="auto_enter",
            requires_permission=False,
            name="学校门（入）",
        ),
        Portal(
            id="portal_school_out",
            from_scene_id=school.id,
            from_tile={"x": 7, "y": 11},
            to_scene_id=outdoor.id,
            to_tile={"x": 28, "y": 24},
            interaction_type="auto_enter",
            requires_permission=False,
            name="学校门（出）",
        ),
        Portal(
            id="portal_grocery_in",
            from_scene_id=outdoor.id,
            from_tile={"x": 32, "y": 10},
            to_scene_id=grocery.id,
            to_tile={"x": 7, "y": 9},
            interaction_type="auto_enter",
            requires_permission=False,
            name="杂货店门（入）",
        ),
        Portal(
            id="portal_grocery_out",
            from_scene_id=grocery.id,
            from_tile={"x": 7, "y": 11},
            to_scene_id=outdoor.id,
            to_tile={"x": 32, "y": 11},
            interaction_type="auto_enter",
            requires_permission=False,
            name="杂货店门（出）",
        ),
    ]
    # 6 处 NPC 住宅传送门（进 + 出各 1，共 12 个）
    for key, (loc_id, bounds, name) in homes.items():
        door_x = bounds["x"] + bounds["width"] // 2
        door_y = bounds["y"] + bounds["height"] - 1   # 室外门片格
        entry_y = bounds["y"] + bounds["height"]       # 室外门外一格（返回落点）
        home_scene = home_scenes[key]
        portals.append(Portal(
            id=f"portal_home_{key}_in",
            from_scene_id=outdoor.id,
            from_tile={"x": door_x, "y": door_y},
            to_scene_id=home_scene.id,
            to_tile={"x": 7, "y": 9},
            interaction_type="auto_enter",
            requires_permission=False,
            name=f"{name}·进门",
        ))
        portals.append(Portal(
            id=f"portal_home_{key}_out",
            from_scene_id=home_scene.id,
            from_tile={"x": 7, "y": 11},
            to_scene_id=outdoor.id,
            to_tile={"x": door_x, "y": entry_y},
            interaction_type="auto_enter",
            requires_permission=False,
            name=f"{name}·出门",
        ))
    session.add_all(portals)

    # ------------------ World objects ------------------
    objects: list[WorldObject] = []
    # 护栏（无桥处的河岸）
    for x in list(range(0, 12)) + list(range(17, OUTDOOR_W)):
        objects.append(
            WorldObject(
                id=_uid("obj_fence"),
                scene_id=outdoor.id,
                name="河岸护栏",
                object_type="barrier",
                position={"x": x, "y": 3},
                size={"width": 1, "height": 1},
                blocks_movement=True,
                available_interactions=[],
                state={},
                tags=["fence", "safety"],
            )
        )

    # 咖啡店室内家具
    cafe_furniture = [
        ("吧台", 3, 2, 6, 1, True, ["inspect"], "furniture"),
        ("咖啡机", 4, 2, 1, 1, True, ["make_coffee", "inspect", "clean"], "facility"),
        ("小桌", 10, 4, 1, 1, True, ["sit", "inspect"], "furniture"),
        ("小桌", 10, 7, 1, 1, True, ["sit", "inspect"], "furniture"),
        ("书架", 2, 8, 1, 2, True, ["inspect"], "furniture"),
    ]
    for name, x, y, w, h, blk, ints, otype in cafe_furniture:
        objects.append(
            WorldObject(
                id=_uid("obj_cafe"),
                scene_id=cafe.id,
                name=name,
                object_type=otype,
                position={"x": x, "y": y},
                size={"width": w, "height": h},
                blocks_movement=blk,
                available_interactions=ints,
                state={},
                tags=["cafe"],
            )
        )

    # 学校室内
    school_furniture = [
        ("黑板", 4, 2, 6, 1, True, ["inspect"], "furniture"),
        ("讲台", 7, 3, 2, 1, True, ["inspect"], "furniture"),
        ("课桌", 3, 6, 1, 1, True, ["sit", "inspect"], "furniture"),
        ("课桌", 6, 6, 1, 1, True, ["sit", "inspect"], "furniture"),
        ("课桌", 9, 6, 1, 1, True, ["sit", "inspect"], "furniture"),
    ]
    for name, x, y, w, h, blk, ints, otype in school_furniture:
        objects.append(
            WorldObject(
                id=_uid("obj_school"),
                scene_id=school.id,
                name=name,
                object_type=otype,
                position={"x": x, "y": y},
                size={"width": w, "height": h},
                blocks_movement=blk,
                available_interactions=ints,
                state={},
                tags=["school"],
            )
        )

    # 杂货店室内
    grocery_furniture = [
        ("柜台", 3, 2, 6, 1, True, ["inspect"], "furniture"),
        ("货架", 2, 4, 1, 5, True, ["inspect", "browse"], "furniture"),
        ("货架", 13, 4, 1, 5, True, ["inspect", "browse"], "furniture"),
    ]
    for name, x, y, w, h, blk, ints, otype in grocery_furniture:
        objects.append(
            WorldObject(
                id=_uid("obj_grocery"),
                scene_id=grocery.id,
                name=name,
                object_type=otype,
                position={"x": x, "y": y},
                size={"width": w, "height": h},
                blocks_movement=blk,
                available_interactions=ints,
                state={},
                tags=["grocery"],
            )
        )

    # 6 处 NPC 住宅室内家具
    # (name, x, y, w, h, blocks, interactions, object_type, tags)
    home_furniture_templates: dict[str, list[tuple]] = {
        "xiaofang": [
            ("床",   5, 2, 3, 2, True,  ["sleep", "inspect"], "furniture", ["bed"]),
            ("书桌", 10, 2, 2, 1, True,  ["inspect"],          "furniture", ["desk"]),
            ("书架", 2,  4, 1, 3, True,  ["inspect"],          "furniture", ["bookshelf"]),
        ],
        "xiaoming": [
            ("床",   3, 2, 3, 2, True,  ["sleep", "inspect"], "furniture", ["bed"]),
            ("书桌", 8,  2, 3, 1, True,  ["study", "inspect"], "furniture", ["desk"]),
            ("书架", 12, 3, 1, 4, True,  ["inspect"],          "furniture", ["bookshelf"]),
        ],
        "xiaowang": [
            ("床",     3, 2, 3, 2, True,  ["sleep", "inspect"],          "furniture", ["bed"]),
            ("工作台", 8, 2, 4, 1, True,  ["work", "use_computer", "inspect"], "facility", ["desk"]),
            ("椅子",   9, 3, 1, 1, False, ["sit", "inspect"],             "furniture", ["chair"]),
        ],
        "linna": [
            ("床",   5, 2, 3, 2, True,  ["sleep", "inspect"], "furniture", ["bed"]),
            ("书架", 2, 2, 1, 5, True,  ["inspect"],          "furniture", ["bookshelf"]),
            ("桌子", 10, 5, 2, 1, True,  ["inspect"],          "furniture", ["table"]),
        ],
        "chenbo": [
            ("床",     3, 2, 3, 2, True,  ["sleep", "inspect"], "furniture", ["bed"]),
            ("老椅子", 9, 4, 1, 1, False, ["sit", "inspect"],   "furniture", ["chair"]),
            ("茶桌",   8, 3, 2, 1, True,  ["inspect"],          "furniture", ["table"]),
        ],
        "ayan": [
            ("床",   3, 2, 3, 2, True,  ["sleep", "inspect"], "furniture", ["bed"]),
            ("花架", 9, 2, 2, 3, True,  ["inspect", "tend"],  "furniture", ["plant"]),
            ("猫窝", 12, 6, 2, 1, False, ["inspect"],          "furniture", ["pet"]),
        ],
    }
    for key, furniture_list in home_furniture_templates.items():
        hs = home_scenes[key]
        for fname, fx, fy, fw, fh, fblk, fints, fotype, ftags in furniture_list:
            objects.append(
                WorldObject(
                    id=_uid(f"obj_home_{key}"),
                    scene_id=hs.id,
                    name=fname,
                    object_type=fotype,
                    position={"x": fx, "y": fy},
                    size={"width": fw, "height": fh},
                    blocks_movement=fblk,
                    available_interactions=fints,
                    state={},
                    tags=["home"] + ftags,
                )
            )

    session.add_all(objects)

    # ------------------ Agents ------------------
    print("[seed] creating agents...")
    humans: list[tuple[str, str, dict[str, Any]]] = [
        (
            "npc_xiaofang",
            "小芳",
            {
                "age": 24,
                "gender": "female",
                "occupation": "咖啡店店员",
                "personality": ["热情", "细心", "外向"],
                "background": "小芳在 Hobbs 咖啡店当店员两年了，记得每一位常客的口味。",
                "lifestyle": "早起锻炼、白天在咖啡店工作，晚上读诗。",
                "home_location_id": "loc_home_xiaofang",
                "appearance": {"sprite_sheet": "npc_xiaofang", "color": "#ffadc2", "scale": 1.0},
                "schedule_template": [
                    {"start": "00:00", "end": "07:00", "activity": "sleep",   "location_id": "loc_home_xiaofang_interior", "description": "在家睡觉"},
                    {"start": "07:00", "end": "08:00", "activity": "morning", "location_id": "loc_home_xiaofang_interior", "description": "准备上班"},
                    {"start": "08:00", "end": "12:00", "activity": "work",    "location_id": "loc_hobbs_cafe_interior", "description": "在咖啡店冲咖啡"},
                    {"start": "12:00", "end": "13:00", "activity": "lunch",   "location_id": "loc_hobbs_cafe_interior", "description": "在店里吃午饭"},
                    {"start": "13:00", "end": "19:00", "activity": "work",    "location_id": "loc_hobbs_cafe_interior", "description": "下午继续服务客人"},
                    {"start": "19:00", "end": "21:00", "activity": "relax",   "location_id": "loc_river_bench", "description": "去河边散步"},
                    {"start": "21:00", "end": "24:00", "activity": "home",    "location_id": "loc_home_xiaofang_interior", "description": "回家休息"},
                ],
                "home": "loc_home_xiaofang",
            },
        ),
        (
            "npc_xiaoming",
            "小明",
            {
                "age": 16,
                "gender": "male",
                "occupation": "高中生",
                "personality": ["腼腆", "认真", "好奇"],
                "background": "小明是小镇学校的高中生，喜欢放学后去咖啡店做作业。",
                "lifestyle": "规律上学，喜欢读书。",
                "home_location_id": "loc_home_xiaoming",
                "appearance": {"sprite_sheet": "npc_xiaoming", "color": "#8cc0ff", "scale": 1.0},
                "schedule_template": [
                    {"start": "00:00", "end": "07:00", "activity": "sleep",     "location_id": "loc_home_xiaoming_interior"},
                    {"start": "07:00", "end": "08:00", "activity": "breakfast", "location_id": "loc_home_xiaoming_interior"},
                    {"start": "08:00", "end": "16:00", "activity": "school",    "location_id": "loc_school_interior", "description": "在教室上课"},
                    {"start": "16:00", "end": "18:00", "activity": "study",     "location_id": "loc_hobbs_cafe_interior", "description": "在咖啡店做作业"},
                    {"start": "18:00", "end": "19:00", "activity": "dinner",    "location_id": "loc_home_xiaoming_interior"},
                    {"start": "19:00", "end": "21:00", "activity": "play",      "location_id": "loc_park"},
                    {"start": "21:00", "end": "24:00", "activity": "home",      "location_id": "loc_home_xiaoming_interior"},
                ],
                "home": "loc_home_xiaoming",
            },
        ),
        (
            "npc_xiaowang",
            "小王",
            {
                "age": 28,
                "gender": "male",
                "occupation": "程序员",
                "personality": ["理性", "内向", "喜欢咖啡"],
                "background": "小王在家远程办公，每天中午来咖啡店点一杯美式，绝对不加糖。",
                "lifestyle": "远程办公，规律吃饭。",
                "home_location_id": "loc_home_xiaowang",
                "appearance": {"sprite_sheet": "npc_xiaowang", "color": "#8cffd1", "scale": 1.0},
                "schedule_template": [
                    {"start": "00:00", "end": "08:00", "activity": "sleep",          "location_id": "loc_home_xiaowang_interior"},
                    {"start": "08:00", "end": "12:00", "activity": "work_from_home", "location_id": "loc_home_xiaowang_interior", "description": "在家写代码"},
                    {"start": "12:00", "end": "13:00", "activity": "coffee",         "location_id": "loc_hobbs_cafe_interior", "description": "去咖啡店买美式"},
                    {"start": "13:00", "end": "18:00", "activity": "work_from_home", "location_id": "loc_home_xiaowang_interior"},
                    {"start": "18:00", "end": "19:30", "activity": "dinner",         "location_id": "loc_grocery_interior", "description": "去杂货店买食材"},
                    {"start": "19:30", "end": "22:00", "activity": "gym",            "location_id": "loc_park", "description": "去广场锻炼"},
                    {"start": "22:00", "end": "24:00", "activity": "home",           "location_id": "loc_home_xiaowang_interior"},
                ],
                "home": "loc_home_xiaowang",
            },
        ),
        (
            "npc_linna",
            "林娜",
            {
                "age": 34,
                "gender": "female",
                "occupation": "医生",
                "personality": ["冷静", "负责"],
                "background": "林娜是小镇的全科医生，熟悉每一位居民的基本健康。",
                "lifestyle": "规律作息，工作以外喜欢徒步。",
                "home_location_id": "loc_home_linna",
                "appearance": {"sprite_sheet": "npc_linna", "color": "#fff0a1", "scale": 1.0},
                "schedule_template": [
                    {"start": "00:00", "end": "07:00", "activity": "sleep",  "location_id": "loc_home_linna_interior"},
                    {"start": "07:00", "end": "08:00", "activity": "coffee", "location_id": "loc_hobbs_cafe_interior", "description": "上班前的咖啡"},
                    {"start": "08:00", "end": "12:00", "activity": "clinic", "location_id": "loc_school_interior", "description": "在诊所看诊（暂借学校）"},
                    {"start": "12:00", "end": "13:00", "activity": "lunch",  "location_id": "loc_hobbs_cafe_interior"},
                    {"start": "13:00", "end": "18:00", "activity": "clinic", "location_id": "loc_school_interior"},
                    {"start": "18:00", "end": "19:00", "activity": "walk",   "location_id": "loc_river_bench"},
                    {"start": "19:00", "end": "24:00", "activity": "home",   "location_id": "loc_home_linna_interior"},
                ],
                "home": "loc_home_linna",
            },
        ),
        (
            "npc_chenbo",
            "陈伯",
            {
                "age": 58,
                "gender": "male",
                "occupation": "杂货店老板",
                "personality": ["和善", "健谈"],
                "background": "陈伯守着杂货店 30 多年，是小镇的活地图。",
                "lifestyle": "天亮就开门，晚上关门去河边。",
                "home_location_id": "loc_home_chenbo",
                "appearance": {"sprite_sheet": "npc_chenbo", "color": "#d9a066", "scale": 1.0},
                "schedule_template": [
                    {"start": "00:00", "end": "06:00", "activity": "sleep",      "location_id": "loc_home_chenbo_interior"},
                    {"start": "06:00", "end": "07:00", "activity": "open_shop",  "location_id": "loc_grocery_interior", "description": "开店"},
                    {"start": "07:00", "end": "19:00", "activity": "shopkeep",   "location_id": "loc_grocery_interior", "description": "守店"},
                    {"start": "19:00", "end": "21:00", "activity": "chat",       "location_id": "loc_river_bench", "description": "去河边乘凉"},
                    {"start": "21:00", "end": "24:00", "activity": "home",       "location_id": "loc_home_chenbo_interior"},
                ],
                "home": "loc_home_chenbo",
            },
        ),
        (
            "npc_ayan",
            "阿言",
            {
                "age": 26,
                "gender": "female",
                "occupation": "花店店主",
                "personality": ["敏感", "艺术化"],
                "background": "阿言经营一家小小的花店，特别喜欢小猫。",
                "lifestyle": "喜欢在花园里发呆。",
                "home_location_id": "loc_home_ayan",
                "appearance": {"sprite_sheet": "npc_ayan", "color": "#c8a2ff", "scale": 1.0},
                "schedule_template": [
                    {"start": "00:00", "end": "07:00", "activity": "sleep",   "location_id": "loc_home_ayan_interior"},
                    {"start": "07:00", "end": "09:00", "activity": "morning", "location_id": "loc_river_bench"},
                    {"start": "09:00", "end": "19:00", "activity": "flower",  "location_id": "loc_flower", "description": "在花店照料植物"},
                    {"start": "19:00", "end": "21:00", "activity": "cafe",    "location_id": "loc_hobbs_cafe_interior"},
                    {"start": "21:00", "end": "24:00", "activity": "home",    "location_id": "loc_home_ayan_interior"},
                ],
                "home": "loc_home_ayan",
            },
        ),
    ]

    # 动物
    animals: list[tuple[str, str, str, dict[str, Any]]] = [
        (
            "animal_doudou",
            "豆豆",
            "dog",
            {
                "personality": ["亲人", "活泼", "friendly"],
                "background": "小芳从小养大的金毛，喜欢所有人。",
                "home_location_id": "loc_home_xiaofang",
                "appearance": {"sprite_sheet": "dog_doudou", "color": "#f5c06e", "scale": 1.0},
                "scene": OUTDOOR_ID,
                "position": (6, 23),
            },
        ),
        (
            "animal_heihei",
            "黑黑",
            "dog",
            {
                "personality": ["警觉", "护家", "guard"],
                "background": "陈伯收养的中华田园犬，对陌生人保持距离。",
                "home_location_id": "loc_home_chenbo",
                "appearance": {"sprite_sheet": "dog_heihei", "color": "#3a3a3a", "scale": 1.0},
                "scene": OUTDOOR_ID,
                "position": (37, 12),   # 陈伯家入口格（原 (36,11) 在建筑主体内）
            },
        ),
        (
            "animal_mimi",
            "咪咪",
            "cat",
            {
                "personality": ["独立", "爱晒太阳"],
                "background": "独立的三花猫，只在阳光好的时候出现。",
                "home_location_id": "loc_home_ayan",
                "appearance": {"sprite_sheet": "cat_mimi", "color": "#ffb4a8", "scale": 0.9},
                "scene": OUTDOOR_ID,
                "position": (3, 13),
            },
        ),
        (
            "animal_xiaobai",
            "小白",
            "cat",
            {
                "personality": ["胆小", "好奇", "shy", "fearful"],
                "background": "阿言新收留的小白猫，很怕陌生人。",
                "home_location_id": "loc_home_ayan",
                "appearance": {"sprite_sheet": "cat_xiaobai", "color": "#f0f0f0", "scale": 0.9},
                "scene": OUTDOOR_ID,
                "position": (23, 8),
            },
        ),
    ]

    # 默认玩家
    player = Agent(
        id="player_default",
        entity_type="player",
        name="旅人",
        background="初到小镇的访客",
        personality=["好奇", "友善"],
        appearance={"sprite_sheet": "player_default", "color": "#ffffff", "scale": 1.0},
        long_term_goals=[],
        schedule_template=[],
    )
    session.add(player)

    # NPC 落地
    # 初始位置 = 各自家的入口格（门片正下方 1 格，walkable）
    npc_positions: dict[str, tuple[str, tuple[int, int]]] = {
        "npc_xiaofang": (OUTDOOR_ID, (3,  27)),   # 小芳家门口 (3,26) 入口 (3,27)
        "npc_xiaoming": (OUTDOOR_ID, (12, 27)),   # 小明家
        "npc_xiaowang": (OUTDOOR_ID, (14, 10)),   # 小王家
        "npc_linna":    (OUTDOOR_ID, (35, 27)),   # 林娜家
        "npc_chenbo":   (OUTDOOR_ID, (37, 12)),   # 陈伯家
        "npc_ayan":     (OUTDOOR_ID, (2,  13)),   # 阿言家
    }

    for agent_id, name, data in humans:
        session.add(
            Agent(
                id=agent_id,
                entity_type="human",
                name=name,
                age=data.get("age"),
                gender=data.get("gender"),
                occupation=data.get("occupation"),
                personality=data.get("personality", []),
                background=data.get("background", ""),
                lifestyle=data.get("lifestyle"),
                appearance=data.get("appearance", {}),
                long_term_goals=[],
                home_location_id=data.get("home_location_id"),
                schedule_template=data.get("schedule_template", []),
            )
        )
        scene_id, pos = npc_positions[agent_id]
        session.add(
            AgentState(
                agent_id=agent_id,
                scene_id=scene_id,
                x=pos[0],
                y=pos[1],
                state="IDLE",
                path=[],
            )
        )

    for agent_id, name, species, data in animals:
        session.add(
            Agent(
                id=agent_id,
                entity_type="animal",
                name=name,
                species=species,
                personality=data.get("personality", []),
                background=data.get("background", ""),
                appearance=data.get("appearance", {}),
                home_location_id=data.get("home_location_id"),
                schedule_template=[],
            )
        )
        session.add(
            AgentState(
                agent_id=agent_id,
                scene_id=data["scene"],
                x=data["position"][0],
                y=data["position"][1],
                state="IDLE",
                path=[],
                energy=0.8,
                hunger=0.3,
            )
        )

    # 玩家运行态
    session.add(
        AgentState(
            agent_id=player.id,
            scene_id=OUTDOOR_ID,
            x=19,
            y=17,
            state="IDLE",
            path=[],
            facing="down",
        )
    )

    # ------------------ Relationships ------------------
    rels = [
        ("npc_xiaofang", "npc_xiaowang", 0.7, 0.6, 0.4, 0.0, "熟客，喜欢美式咖啡"),
        ("npc_xiaofang", "npc_xiaoming", 0.6, 0.8, 0.5, 0.0, "常来做作业的腼腆学生"),
        ("npc_xiaofang", "npc_linna", 0.5, 0.5, 0.3, 0.0, "每天早上来买咖啡"),
        ("npc_xiaowang", "npc_xiaofang", 0.7, 0.6, 0.5, 0.0, "熟悉的咖啡店店员"),
        ("npc_chenbo", "npc_ayan", 0.4, 0.6, 0.3, 0.0, "花店姑娘，常来买杂货"),
        ("animal_doudou", "npc_xiaofang", 0.95, 0.9, 0.9, 0.0, "主人"),
        ("animal_heihei", "npc_chenbo", 0.9, 0.9, 0.8, 0.0, "主人"),
        ("animal_mimi", "npc_ayan", 0.8, 0.7, 0.6, 0.0, "主人"),
        ("animal_xiaobai", "npc_ayan", 0.7, 0.6, 0.7, 0.1, "最近收养"),
    ]
    for from_id, to_id, fam, trust, affection, fear, summary in rels:
        session.add(
            Relationship(
                from_agent_id=from_id,
                to_entity_id=to_id,
                familiarity=fam,
                trust=trust,
                affection=affection,
                fear=fear,
                summary=summary,
            )
        )

    # ------------------ 预置记忆 ------------------
    # 小芳记得小王喜欢美式咖啡，是产品演示关键记忆
    base_time = datetime(2026, 4, 25, 10, 0, tzinfo=timezone.utc)
    seed_memories = [
        {
            "agent_id": "npc_xiaofang",
            "memory_type": "event",
            "scope": "long_term",
            "description": "小王每天中午都来咖啡店点一杯美式咖啡，他说不加糖。",
            "importance": 8,
            "subject": "小王",
            "predicate": "喜欢",
            "object": "美式咖啡",
            "keywords": ["小王", "美式咖啡", "不加糖"],
        },
        {
            "agent_id": "npc_xiaofang",
            "memory_type": "event",
            "scope": "long_term",
            "description": "小明放学后经常来咖啡店做作业，他特别害羞。",
            "importance": 6,
            "subject": "小明",
            "predicate": "常来",
            "object": "咖啡店",
            "keywords": ["小明", "做作业", "害羞"],
        },
        {
            "agent_id": "npc_xiaofang",
            "memory_type": "thought",
            "scope": "long_term",
            "description": "我很熟悉这些常客的喜好，他们的小习惯让我感到温暖。",
            "importance": 5,
            "keywords": ["常客", "习惯"],
        },
        {
            "agent_id": "npc_xiaowang",
            "memory_type": "event",
            "scope": "long_term",
            "description": "在咖啡店里我总是点美式，小芳从不问第二遍。",
            "importance": 7,
            "subject": "我",
            "predicate": "点",
            "object": "美式咖啡",
            "keywords": ["美式咖啡", "小芳"],
        },
        {
            "agent_id": "npc_chenbo",
            "memory_type": "event",
            "scope": "long_term",
            "description": "黑黑看到陌生人会叫，我告诉它没事才会安静下来。",
            "importance": 6,
            "subject": "黑黑",
            "predicate": "对陌生人",
            "object": "警觉",
            "keywords": ["黑黑", "陌生人"],
        },
        {
            "agent_id": "npc_ayan",
            "memory_type": "event",
            "scope": "long_term",
            "description": "小白最近才到我家，它非常怕陌生人。",
            "importance": 6,
            "subject": "小白",
            "predicate": "害怕",
            "object": "陌生人",
            "keywords": ["小白", "害怕"],
        },
    ]
    for mem in seed_memories:
        session.add(
            Memory(
                **mem,
                created_at=base_time,
            )
        )

    # ------------------ Simulation ------------------
    sim = Simulation(
        id=str(uuid.uuid4()),
        status="running" if get_settings().simulation_autostart else "idle",
        world_time=datetime(2026, 4, 30, 8, 0, tzinfo=timezone.utc),
        world_tick_hz=get_settings().simulation_world_tick_hz,
        ai_tick_minutes=get_settings().simulation_ai_tick_minutes,
        speed_multiplier=get_settings().simulation_speed_default,
        current_step=0,
    )
    session.add(sim)

    session.commit()
    print(f"[seed] done. simulation_id={sim.id}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--if-empty", action="store_true", help="仅在数据库没有 agent 时执行")
    args = parser.parse_args()

    settings = get_settings()
    url = os.getenv("DATABASE_URL_SYNC", settings.database_url_sync)
    engine = create_engine(url, pool_pre_ping=True)
    with Session(engine) as session:
        seed(session, force=not args.if_empty)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
