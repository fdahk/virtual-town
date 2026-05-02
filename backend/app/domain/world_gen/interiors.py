"""室内场景与家具生成。

每种 ``interior_kind`` 对应一份家具模板；位置基于 16×12 室内瓦片网格。
住宅家具支持基于 ``key`` 的个性化覆盖（默认家具 + NPC 风格家具混搭）。
"""

from __future__ import annotations

from typing import Any

INDOOR_W, INDOOR_H = 16, 12


def build_indoor_tiles(entry_x: int = 7) -> list[dict[str, Any]]:
    """16×12 室内瓦片：外圈墙 + 内部地板，底部 ``entry_x`` 留一格门。"""
    tiles: list[dict[str, Any]] = []
    for y in range(INDOOR_H):
        for x in range(INDOOR_W):
            is_edge = (x == 0 or x == INDOOR_W - 1 or y == 0 or y == INDOOR_H - 1)
            is_door = (y == INDOOR_H - 1 and x == entry_x)
            if is_edge and not is_door:
                tiles.append({
                    "x": x, "y": y, "terrain": "wall",
                    "walkable": False, "blocks_movement": True, "blocks_vision": True,
                    "hazard_type": None, "hazard_level": None, "tags": ["wall"],
                })
            else:
                tiles.append({
                    "x": x, "y": y, "terrain": "floor",
                    "walkable": True, "blocks_movement": False, "blocks_vision": False,
                    "hazard_type": None, "hazard_level": None, "tags": [],
                })
    return tiles


# 家具元组格式：(name, x, y, w, h, blocks, interactions, object_type, state, tags)
_F = tuple[str, int, int, int, int, bool, list[str], str, dict[str, Any], list[str]]


PUBLIC_INTERIOR_FURNITURE: dict[str, list[_F]] = {
    "cafe": [
        ("吧台",   3, 2, 6, 1, True,  ["inspect"],                "furniture",
            {"color": 0xBB8855}, ["cafe", "desk", "table"]),
        ("咖啡机", 9, 2, 1, 1, True,  ["make_coffee", "inspect"], "facility",
            {"tile_frame": 53, "tileset": "kenney_tiny_dungeon"}, ["cafe", "appliance"]),
        ("小桌",   10, 4, 1, 1, True, ["sit", "inspect"], "furniture",
            {"tile_frame": 60, "tileset": "kenney_tiny_dungeon"}, ["cafe", "table"]),
        ("小桌",   10, 7, 1, 1, True, ["sit", "inspect"], "furniture",
            {"tile_frame": 60, "tileset": "kenney_tiny_dungeon"}, ["cafe", "table"]),
        ("书架",   2,  8, 1, 2, True, ["inspect"], "furniture",
            {"color": 0x663311}, ["cafe", "bookshelf"]),
        ("椅子",   9,  4, 1, 1, False, ["sit", "inspect"], "furniture",
            {"tile_frame": 61, "tileset": "kenney_tiny_dungeon"}, ["cafe", "chair"]),
        ("椅子",   9,  7, 1, 1, False, ["sit", "inspect"], "furniture",
            {"tile_frame": 61, "tileset": "kenney_tiny_dungeon"}, ["cafe", "chair"]),
    ],
    "school": [
        ("黑板", 4, 2, 6, 1, True,  ["inspect", "write"], "furniture",
            {"color": 0x2D5A27}, ["school", "blackboard"]),
        ("讲台", 7, 3, 2, 1, True,  ["inspect"], "furniture",
            {"color": 0xBB8855}, ["school", "desk", "table"]),
        ("课桌", 3, 6, 1, 1, True,  ["sit", "inspect"], "furniture",
            {"tile_frame": 60, "tileset": "kenney_tiny_dungeon"}, ["school", "table"]),
        ("课桌", 6, 6, 1, 1, True,  ["sit", "inspect"], "furniture",
            {"tile_frame": 60, "tileset": "kenney_tiny_dungeon"}, ["school", "table"]),
        ("课桌", 9, 6, 1, 1, True,  ["sit", "inspect"], "furniture",
            {"tile_frame": 60, "tileset": "kenney_tiny_dungeon"}, ["school", "table"]),
        ("课桌", 12, 6, 1, 1, True, ["sit", "inspect"], "furniture",
            {"tile_frame": 60, "tileset": "kenney_tiny_dungeon"}, ["school", "table"]),
        ("书架", 2, 4, 1, 4, True,  ["inspect", "browse"], "furniture",
            {"color": 0x663311}, ["school", "bookshelf"]),
    ],
    "grocery": [
        ("柜台", 3, 2, 6, 1, True, ["inspect"], "furniture",
            {"color": 0xBB8855}, ["grocery", "desk", "table"]),
        ("货架", 2, 4, 1, 5, True, ["inspect", "browse"], "furniture",
            {"color": 0x8B4513}, ["grocery", "bookshelf"]),
        ("货架", 13, 4, 1, 5, True, ["inspect", "browse"], "furniture",
            {"color": 0x8B4513}, ["grocery", "bookshelf"]),
        ("展台", 7, 5, 2, 3, False, ["inspect", "browse"], "furniture",
            {"color": 0xC49A6C}, ["grocery", "desk", "table"]),
    ],
    "flower": [
        ("工作台",   3,  2, 6, 1, True,  ["inspect", "tend"], "furniture",
            {"color": 0xBB8855}, ["flower_shop", "desk", "table"]),
        ("收银台",   10, 2, 3, 1, True,  ["inspect"], "furniture",
            {"tile_frame": 77, "tileset": "kenney_tiny_dungeon"}, ["flower_shop", "desk"]),
        ("花架·左",  2,  3, 1, 5, True,  ["inspect", "tend", "water"], "facility",
            {"color": 0x4A7C59}, ["flower_shop", "plant"]),
        ("花架·右",  13, 3, 1, 5, True,  ["inspect", "tend", "water"], "facility",
            {"color": 0x4A7C59}, ["flower_shop", "plant"]),
        ("长椅",     5,  8, 1, 1, False, ["sit", "inspect"], "furniture",
            {"tile_frame": 61, "tileset": "kenney_tiny_dungeon"}, ["flower_shop", "chair"]),
        ("花盆展示", 7,  5, 2, 2, False, ["inspect"], "plant",
            {"color": 0x4A7C59}, ["flower_shop", "plant"]),
    ],
    "library": [
        ("书架·北墙左", 2, 1, 1, 6, True, ["inspect", "browse"], "furniture",
            {"color": 0x5C3A21}, ["library", "bookshelf"]),
        ("书架·北墙右", 13, 1, 1, 6, True, ["inspect", "browse"], "furniture",
            {"color": 0x5C3A21}, ["library", "bookshelf"]),
        ("阅览长桌", 5, 4, 4, 1, True, ["sit", "study", "inspect"], "furniture",
            {"color": 0xBB8855}, ["library", "desk", "table"]),
        ("椅子", 5, 5, 1, 1, False, ["sit"], "furniture",
            {"tile_frame": 61, "tileset": "kenney_tiny_dungeon"}, ["library", "chair"]),
        ("椅子", 7, 5, 1, 1, False, ["sit"], "furniture",
            {"tile_frame": 61, "tileset": "kenney_tiny_dungeon"}, ["library", "chair"]),
        ("借还柜台", 9, 2, 4, 1, True, ["inspect"], "furniture",
            {"color": 0x8B4513}, ["library", "desk", "table"]),
        ("故事角", 4, 8, 3, 2, False, ["inspect", "read"], "furniture",
            {"color": 0xc8c0a0}, ["library", "rest"]),
    ],
    "bakery": [
        ("烤炉", 2, 2, 2, 1, True, ["bake", "inspect"], "facility",
            {"tile_frame": 92, "tileset": "kenney_tiny_dungeon", "flammable": True}, ["bakery", "appliance"]),
        ("揉面台", 5, 2, 4, 1, True, ["bake", "inspect"], "furniture",
            {"color": 0xC49A6C}, ["bakery", "desk", "table"]),
        ("展示柜", 10, 2, 3, 1, True, ["inspect", "browse"], "furniture",
            {"tile_frame": 51, "tileset": "kenney_tiny_dungeon"}, ["bakery", "shelf"]),
        ("面粉桶", 2, 5, 1, 1, True, ["inspect"], "furniture",
            {"tile_frame": 55, "tileset": "kenney_tiny_dungeon"}, ["bakery", "barrel"]),
        ("面粉桶", 3, 5, 1, 1, True, ["inspect"], "furniture",
            {"tile_frame": 55, "tileset": "kenney_tiny_dungeon"}, ["bakery", "barrel"]),
        ("吧台椅", 12, 5, 1, 1, False, ["sit"], "furniture",
            {"tile_frame": 61, "tileset": "kenney_tiny_dungeon"}, ["bakery", "chair"]),
    ],
    "tavern": [
        ("吧台", 2, 2, 8, 1, True, ["sit", "order"], "furniture",
            {"color": 0x8a604c}, ["tavern", "desk", "table"]),
        ("酒架", 2, 1, 8, 1, True, ["inspect", "browse"], "furniture",
            {"color": 0x5C3A21}, ["tavern", "bookshelf"]),
        ("圆桌", 4, 5, 2, 2, True, ["sit", "inspect"], "furniture",
            {"color": 0xa07050}, ["tavern", "desk", "table"]),
        ("圆桌", 9, 5, 2, 2, True, ["sit", "inspect"], "furniture",
            {"color": 0xa07050}, ["tavern", "desk", "table"]),
        ("壁炉", 12, 2, 2, 2, True, ["warm", "inspect"], "facility",
            {"color": 0xd56b6b, "flammable": False}, ["tavern", "fire"]),
        ("酒桶", 12, 8, 2, 1, True, ["inspect"], "furniture",
            {"tile_frame": 56, "tileset": "kenney_tiny_dungeon"}, ["tavern", "barrel"]),
    ],
    "post": [
        ("分拣桌", 3, 2, 6, 1, True, ["sort", "inspect"], "furniture",
            {"color": 0xBB8855}, ["post", "desk", "table"]),
        ("信筒架", 2, 4, 1, 5, True, ["inspect", "browse"], "furniture",
            {"color": 0x8B4513}, ["post", "bookshelf"]),
        ("地图墙", 10, 2, 4, 1, True, ["inspect"], "furniture",
            {"color": 0xa4c8ff}, ["post", "decoration"]),
        ("座椅", 6, 7, 1, 1, False, ["sit"], "furniture",
            {"tile_frame": 61, "tileset": "kenney_tiny_dungeon"}, ["post", "chair"]),
    ],
    "woodshop": [
        ("木工台", 3, 2, 6, 1, True, ["craft", "inspect"], "furniture",
            {"color": 0x8a604c}, ["woodshop", "desk", "table"]),
        ("木材堆", 11, 2, 2, 2, True, ["inspect", "take"], "furniture",
            {"tile_frame": 68, "tileset": "kenney_tiny_dungeon"}, ["woodshop", "decoration"]),
        ("工具墙", 2, 4, 1, 4, True, ["inspect"], "furniture",
            {"color": 0x5C3A21}, ["woodshop", "bookshelf"]),
        ("成品架", 13, 4, 1, 5, True, ["inspect", "browse"], "furniture",
            {"color": 0x5C3A21}, ["woodshop", "bookshelf"]),
        ("矮凳", 6, 5, 1, 1, False, ["sit"], "furniture",
            {"tile_frame": 61, "tileset": "kenney_tiny_dungeon"}, ["woodshop", "chair"]),
    ],
    "farmhouse": [
        ("床",     3, 2, 3, 2, True, ["sleep", "inspect"], "furniture",
            {"color": 0xc2a37e}, ["home", "bed"]),
        ("餐桌",   8, 4, 4, 1, True, ["sit", "inspect"], "furniture",
            {"color": 0xBB8855}, ["home", "desk", "table"]),
        ("椅子",   8, 5, 1, 1, False, ["sit"], "furniture",
            {"tile_frame": 61, "tileset": "kenney_tiny_dungeon"}, ["home", "chair"]),
        ("椅子",   11, 5, 1, 1, False, ["sit"], "furniture",
            {"tile_frame": 61, "tileset": "kenney_tiny_dungeon"}, ["home", "chair"]),
        ("壁炉",   2, 7, 2, 2, True,  ["warm", "inspect"], "facility",
            {"color": 0xd56b6b}, ["home", "fire"]),
        ("农具角", 12, 7, 2, 2, True, ["inspect", "take"], "furniture",
            {"color": 0x5C3A21}, ["home", "decoration"]),
    ],
}


# 6 处旧版住宅家具（保留兼容）；其它住宅使用 ``DEFAULT_HOME_FURNITURE``
HOME_FURNITURE_OVERRIDES: dict[str, list[_F]] = {
    "xiaofang": [
        ("床",   5, 2, 3, 2, True, ["sleep", "inspect"], "furniture",
            {"color": 0x8899BB}, ["home", "bed"]),
        ("书桌", 10, 2, 2, 1, True, ["inspect"], "furniture",
            {"color": 0xBB8855}, ["home", "desk", "table"]),
        ("书架", 2, 4, 1, 3, True, ["inspect"], "furniture",
            {"color": 0x663311}, ["home", "bookshelf"]),
        ("椅子", 10, 3, 1, 1, False, ["sit", "inspect"], "furniture",
            {"tile_frame": 61, "tileset": "kenney_tiny_dungeon"}, ["home", "chair"]),
    ],
    "xiaoming": [
        ("床",   3, 2, 3, 2, True, ["sleep", "inspect"], "furniture",
            {"color": 0x7799CC}, ["home", "bed"]),
        ("书桌", 8, 2, 3, 1, True, ["study", "inspect"], "furniture",
            {"color": 0xBB8855}, ["home", "desk", "table"]),
        ("书架", 12, 3, 1, 4, True, ["inspect"], "furniture",
            {"color": 0x663311}, ["home", "bookshelf"]),
    ],
    "xiaowang": [
        ("床", 3, 2, 3, 2, True, ["sleep", "inspect"], "furniture",
            {"color": 0x334466}, ["home", "bed"]),
        ("工作台", 8, 2, 4, 1, True, ["work", "use_computer", "inspect"], "facility",
            {"color": 0x333355}, ["home", "desk", "table"]),
        ("椅子", 9, 3, 1, 1, False, ["sit", "inspect"], "furniture",
            {"tile_frame": 61, "tileset": "kenney_tiny_dungeon"}, ["home", "chair"]),
    ],
    "linna": [
        ("床", 5, 2, 3, 2, True, ["sleep", "inspect"], "furniture",
            {"color": 0xCC9988}, ["home", "bed"]),
        ("书架", 2, 2, 1, 5, True, ["inspect"], "furniture",
            {"color": 0x663311}, ["home", "bookshelf"]),
        ("桌子", 10, 5, 2, 1, True, ["inspect"], "furniture",
            {"color": 0xBB8855}, ["home", "desk", "table"]),
        ("椅子", 10, 6, 1, 1, False, ["sit", "inspect"], "furniture",
            {"tile_frame": 61, "tileset": "kenney_tiny_dungeon"}, ["home", "chair"]),
    ],
    "chenbo": [
        ("床", 3, 2, 3, 2, True, ["sleep", "inspect"], "furniture",
            {"color": 0x887755}, ["home", "bed"]),
        ("老椅子", 9, 4, 1, 1, False, ["sit", "inspect"], "furniture",
            {"tile_frame": 61, "tileset": "kenney_tiny_dungeon"}, ["home", "chair"]),
        ("茶桌", 8, 3, 2, 1, True, ["inspect"], "furniture",
            {"color": 0xBB8855}, ["home", "desk", "table"]),
    ],
    "ayan": [
        ("床", 3, 2, 3, 2, True, ["sleep", "inspect"], "furniture",
            {"color": 0xCC99BB}, ["home", "bed"]),
        ("花架", 9, 2, 2, 3, True, ["inspect", "tend"], "facility",
            {"color": 0x4A7C59}, ["home", "plant"]),
        ("猫窝", 12, 6, 2, 1, False, ["inspect"], "furniture",
            {"color": 0xE8C4A0}, ["home", "pet"]),
    ],
}


DEFAULT_HOME_FURNITURE: list[_F] = [
    ("床",   3, 2, 3, 2, True, ["sleep", "inspect"], "furniture",
        {"color": 0x8899BB}, ["home", "bed"]),
    ("书桌", 8, 2, 3, 1, True, ["inspect"], "furniture",
        {"color": 0xBB8855}, ["home", "desk", "table"]),
    ("书架", 2, 4, 1, 3, True, ["inspect"], "furniture",
        {"color": 0x663311}, ["home", "bookshelf"]),
    ("椅子", 9, 3, 1, 1, False, ["sit", "inspect"], "furniture",
        {"tile_frame": 61, "tileset": "kenney_tiny_dungeon"}, ["home", "chair"]),
]


def furniture_for_interior(kind: str, npc_key: str | None = None) -> list[_F]:
    """根据 ``interior_kind`` + 可选 NPC key 返回家具元组列表。"""
    if kind == "home":
        if npc_key and npc_key in HOME_FURNITURE_OVERRIDES:
            return HOME_FURNITURE_OVERRIDES[npc_key]
        return DEFAULT_HOME_FURNITURE
    return PUBLIC_INTERIOR_FURNITURE.get(kind, [])
