"""LPC 美术资源目录 + 职业默认 schedule 模板。

前端 NPCEditor 通过 ``GET /api/games/templates/art`` 拿到 ``LPC_LAYER_CATALOG``
和 ``ANIMAL_COLOR_CATALOG`` 用于实时渲染人物预览；后端
``world_gen.placement`` 用 ``OCCUPATION_PRESETS`` + ``SCHEDULE_TEMPLATES``
把 NPC 占位符 schedule 替换成真实 location_id。

新增 LPC 层时三处必须同步：
1. ``scripts/fetch_assets.sh`` 的 LPC_LAYERS 数组追加下载 URL
2. ``scripts/compose_lpc.py`` 的 NPC_LAYERS 引用新文件名
3. 本文件 LPC_LAYER_CATALOG 注册可选项
"""

from __future__ import annotations

import hashlib
from typing import Any, TypedDict


class LayerOption(TypedDict):
    """LPC 单层的可选择条目。"""

    id: str
    label: str
    file: str  # 与 fetch_assets.sh 中下载到 .cache/assets/lpc/<file> 对齐


# ---------------------------------------------------------------------------
# LPC 人物分层目录（前端 NPCEditor 用作下拉选项）
# ---------------------------------------------------------------------------

LPC_LAYER_CATALOG: dict[str, list[LayerOption]] = {
    "body": [
        {"id": "male_light",   "label": "男·浅肤色", "file": "body_male_light.png"},
        {"id": "male_taupe",   "label": "男·深肤色", "file": "body_male_taupe.png"},
        {"id": "female_light", "label": "女·浅肤色", "file": "body_female_light.png"},
    ],
    "head": [
        {"id": "male_light",   "label": "男·浅肤色", "file": "head_male_light.png"},
        {"id": "male_taupe",   "label": "男·深肤色", "file": "head_male_taupe.png"},
        {"id": "female_light", "label": "女·浅肤色", "file": "head_female_light.png"},
    ],
    "hair": [
        {"id": "long_black",       "label": "长发·黑",     "file": "hair_long_female_black.png"},
        {"id": "long_brown",       "label": "长发·棕",     "file": "hair_long_female_brown.png"},
        {"id": "long_lavender",    "label": "长发·薰衣草", "file": "hair_long_female_lavender.png"},
        {"id": "ponytail_raven",   "label": "马尾·墨黑",   "file": "hair_ponytail_female_raven.png"},
        {"id": "short_male_black", "label": "男短发·黑",   "file": "hair_short_male_black.png"},
        {"id": "short_male_brown", "label": "男短发·棕",   "file": "hair_short_male_brown.png"},
        {"id": "short_male_gray",  "label": "男短发·灰",   "file": "hair_short_male_gray.png"},
        {"id": "short_fem_black",  "label": "女短发·黑",   "file": "hair_short_female_black.png"},
        {"id": "short_fem_brown",  "label": "女短发·棕",   "file": "hair_short_female_brown.png"},
    ],
    "torso": [
        {"id": "ls_male_blue",      "label": "长袖·蓝",         "file": "shirt_longsleeve_male_blue.png"},
        {"id": "ls_male_gray",      "label": "长袖·灰",         "file": "shirt_longsleeve_male_gray.png"},
        {"id": "ls_male_charcoal",  "label": "长袖·炭黑",       "file": "shirt_longsleeve_male_charcoal.png"},
        {"id": "ls_male_red",       "label": "长袖·红",         "file": "shirt_longsleeve_male_red.png"},
        {"id": "ls_male_brown",     "label": "长袖·棕",         "file": "shirt_longsleeve_male_brown.png"},
        {"id": "ls_fem_bluegray",   "label": "女长袖·灰蓝",     "file": "shirt_longsleeve_female_bluegray.png"},
        {"id": "ls_fem_lavender",   "label": "女长袖·薰衣草",   "file": "shirt_longsleeve_female_lavender.png"},
        {"id": "ls_fem_red",        "label": "女长袖·红",       "file": "shirt_longsleeve_female_red.png"},
        {"id": "apron_fem_white",   "label": "围裙·白(女)",     "file": "apron_female_white.png"},
        {"id": "apron_male_white",  "label": "围裙·白(男)",     "file": "apron_male_white.png"},
    ],
    "legs": [
        {"id": "pants_male_black",    "label": "裤子·黑(男)",   "file": "pants_male_black.png"},
        {"id": "pants_male_brown",    "label": "裤子·棕(男)",   "file": "pants_male_brown.png"},
        {"id": "pants_male_charcoal", "label": "裤子·炭黑(男)", "file": "pants_male_charcoal.png"},
        {"id": "pants_male_blue",     "label": "裤子·蓝(男)",   "file": "pants_male_blue.png"},
        {"id": "pants_fem_black",     "label": "裤子·黑(女)",   "file": "pants_female_black.png"},
        {"id": "pants_fem_brown",     "label": "裤子·棕(女)",   "file": "pants_female_brown.png"},
        {"id": "pants_fem_blue",      "label": "裤子·蓝(女)",   "file": "pants_female_blue.png"},
    ],
    "feet": [
        {"id": "shoes_male_brown", "label": "鞋·棕(男)", "file": "shoes_male_brown.png"},
        {"id": "shoes_fem_brown",  "label": "鞋·棕(女)", "file": "shoes_female_brown.png"},
    ],
}


# 动物颜色目录（cat / dog 各 4 色，对应 LPC sheet 列偏移；见 sprites.manifest.json）
ANIMAL_COLOR_CATALOG: dict[str, list[LayerOption]] = {
    "cat": [
        {"id": "white",  "label": "白猫",  "file": "lpc_cats:white"},
        {"id": "brown",  "label": "棕猫",  "file": "lpc_cats:brown"},
        {"id": "golden", "label": "金毛猫","file": "lpc_cats:golden"},
        {"id": "black",  "label": "黑猫",  "file": "lpc_cats:black"},
    ],
    "dog": [
        {"id": "white",  "label": "白狗",  "file": "lpc_dogs:white"},
        {"id": "brown",  "label": "棕狗",  "file": "lpc_dogs:brown"},
        {"id": "golden", "label": "金毛犬","file": "lpc_dogs:golden"},
        {"id": "black",  "label": "黑狗",  "file": "lpc_dogs:black"},
    ],
}


# ---------------------------------------------------------------------------
# 职业 → 工作场所 + 默认 schedule 模板 ID
# ---------------------------------------------------------------------------

OCCUPATION_PRESETS: dict[str, dict[str, str | None]] = {
    "咖啡店店员":   {"workplace": "loc_hobbs_cafe_interior", "schedule": "cafe_staff"},
    "高中生":       {"workplace": "loc_school_interior",     "schedule": "high_school_student"},
    "中学生":       {"workplace": "loc_school_interior",     "schedule": "middle_school_student"},
    "小学生":       {"workplace": "loc_school_interior",     "schedule": "primary_school_student"},
    "程序员":       {"workplace": None,                       "schedule": "remote_worker"},
    "医生":         {"workplace": "loc_school_interior",     "schedule": "clinic"},
    "杂货店老板":   {"workplace": "loc_grocery_interior",    "schedule": "shopkeeper"},
    "花店店主":     {"workplace": "loc_flower_interior",     "schedule": "florist"},
    "校长":         {"workplace": "loc_school_interior",     "schedule": "principal"},
    "面包师":       {"workplace": "loc_bakery_interior",     "schedule": "baker"},
    "图书管理员":   {"workplace": "loc_library_interior",    "schedule": "librarian"},
    "退休木匠":     {"workplace": "loc_woodshop_interior",   "schedule": "elder_craftsman"},
    "学徒木匠":     {"workplace": "loc_woodshop_interior",   "schedule": "apprentice"},
    "酒馆老板娘":   {"workplace": "loc_tavern_interior",     "schedule": "tavern_owner"},
    "厨师":         {"workplace": "loc_tavern_interior",     "schedule": "chef"},
    "农场主":       {"workplace": "loc_farmhouse_interior",  "schedule": "farmer_owner"},
    "农场工人":     {"workplace": "loc_farmhouse_interior",  "schedule": "farmer_worker"},
    "邮差":         {"workplace": "loc_post_interior",       "schedule": "postal_worker"},
}


# ---------------------------------------------------------------------------
# 默认 schedule 模板（schedule_template 占位符 + 时段）
# ---------------------------------------------------------------------------
# location_id 占位符约定：
#   <HOME>      → NPC 实际住宅室内 location_id
#   <WORKPLACE> → OCCUPATION_PRESETS[occupation].workplace
#   <PARK>      → loc_park（中央广场，所有世界都有）
#   <RIVER>     → loc_river_bench
#   <CAFE>      → loc_hobbs_cafe_interior（社交锚点）

SCHEDULE_TEMPLATES: dict[str, list[dict[str, Any]]] = {
    "cafe_staff": [
        {"start": "00:00", "end": "07:00", "activity": "sleep",   "location_id": "<HOME>",      "description": "在家睡觉"},
        {"start": "07:00", "end": "08:00", "activity": "morning", "location_id": "<HOME>",      "description": "准备上班"},
        {"start": "08:00", "end": "12:00", "activity": "work",    "location_id": "<WORKPLACE>", "description": "在咖啡店冲咖啡"},
        {"start": "12:00", "end": "13:00", "activity": "lunch",   "location_id": "<WORKPLACE>", "description": "在店里吃午饭"},
        {"start": "13:00", "end": "19:00", "activity": "work",    "location_id": "<WORKPLACE>", "description": "下午继续服务客人"},
        {"start": "19:00", "end": "21:00", "activity": "relax",   "location_id": "<RIVER>",     "description": "去河边散步"},
        {"start": "21:00", "end": "24:00", "activity": "home",    "location_id": "<HOME>",      "description": "回家休息"},
    ],
    "high_school_student": [
        {"start": "00:00", "end": "07:00", "activity": "sleep",     "location_id": "<HOME>"},
        {"start": "07:00", "end": "08:00", "activity": "breakfast", "location_id": "<HOME>"},
        {"start": "08:00", "end": "16:00", "activity": "school",    "location_id": "<WORKPLACE>", "description": "在教室上课"},
        {"start": "16:00", "end": "18:00", "activity": "study",     "location_id": "<CAFE>",      "description": "在咖啡店做作业"},
        {"start": "18:00", "end": "19:00", "activity": "dinner",    "location_id": "<HOME>"},
        {"start": "19:00", "end": "21:00", "activity": "play",      "location_id": "<PARK>"},
        {"start": "21:00", "end": "24:00", "activity": "home",      "location_id": "<HOME>"},
    ],
    "middle_school_student": [
        {"start": "00:00", "end": "07:00", "activity": "sleep",     "location_id": "<HOME>"},
        {"start": "07:00", "end": "08:00", "activity": "breakfast", "location_id": "<HOME>"},
        {"start": "08:00", "end": "15:30", "activity": "school",    "location_id": "<WORKPLACE>", "description": "上课"},
        {"start": "15:30", "end": "17:30", "activity": "play",      "location_id": "<PARK>",       "description": "和同学在广场玩"},
        {"start": "17:30", "end": "19:00", "activity": "dinner",    "location_id": "<HOME>"},
        {"start": "19:00", "end": "21:00", "activity": "study",     "location_id": "<HOME>"},
        {"start": "21:00", "end": "24:00", "activity": "home",      "location_id": "<HOME>"},
    ],
    "primary_school_student": [
        {"start": "00:00", "end": "07:30", "activity": "sleep",     "location_id": "<HOME>"},
        {"start": "07:30", "end": "08:30", "activity": "breakfast", "location_id": "<HOME>"},
        {"start": "08:30", "end": "15:00", "activity": "school",    "location_id": "<WORKPLACE>", "description": "上课"},
        {"start": "15:00", "end": "17:00", "activity": "play",      "location_id": "<PARK>",       "description": "在公园玩"},
        {"start": "17:00", "end": "20:00", "activity": "home",      "location_id": "<HOME>",       "description": "和家人吃饭、玩耍"},
        {"start": "20:00", "end": "24:00", "activity": "sleep",     "location_id": "<HOME>"},
    ],
    "remote_worker": [
        {"start": "00:00", "end": "08:00", "activity": "sleep",          "location_id": "<HOME>"},
        {"start": "08:00", "end": "12:00", "activity": "work_from_home", "location_id": "<HOME>",  "description": "在家写代码"},
        {"start": "12:00", "end": "13:00", "activity": "coffee",         "location_id": "<CAFE>",  "description": "去咖啡店买美式"},
        {"start": "13:00", "end": "18:00", "activity": "work_from_home", "location_id": "<HOME>"},
        {"start": "18:00", "end": "19:30", "activity": "dinner",         "location_id": "loc_grocery_interior", "description": "去杂货店买食材"},
        {"start": "19:30", "end": "22:00", "activity": "gym",            "location_id": "<PARK>",  "description": "在广场锻炼"},
        {"start": "22:00", "end": "24:00", "activity": "home",           "location_id": "<HOME>"},
    ],
    "clinic": [
        {"start": "00:00", "end": "07:00", "activity": "sleep",  "location_id": "<HOME>"},
        {"start": "07:00", "end": "08:00", "activity": "coffee", "location_id": "<CAFE>",       "description": "上班前的咖啡"},
        {"start": "08:00", "end": "12:00", "activity": "clinic", "location_id": "<WORKPLACE>",  "description": "在诊所看诊"},
        {"start": "12:00", "end": "13:00", "activity": "lunch",  "location_id": "<CAFE>"},
        {"start": "13:00", "end": "18:00", "activity": "clinic", "location_id": "<WORKPLACE>"},
        {"start": "18:00", "end": "19:00", "activity": "walk",   "location_id": "<RIVER>"},
        {"start": "19:00", "end": "24:00", "activity": "home",   "location_id": "<HOME>"},
    ],
    "shopkeeper": [
        {"start": "00:00", "end": "06:00", "activity": "sleep",     "location_id": "<HOME>"},
        {"start": "06:00", "end": "07:00", "activity": "open_shop", "location_id": "<WORKPLACE>", "description": "开店"},
        {"start": "07:00", "end": "19:00", "activity": "shopkeep",  "location_id": "<WORKPLACE>", "description": "守店"},
        {"start": "19:00", "end": "21:00", "activity": "chat",      "location_id": "<RIVER>",     "description": "去河边乘凉"},
        {"start": "21:00", "end": "24:00", "activity": "home",      "location_id": "<HOME>"},
    ],
    "florist": [
        {"start": "00:00", "end": "07:00", "activity": "sleep",   "location_id": "<HOME>"},
        {"start": "07:00", "end": "09:00", "activity": "morning", "location_id": "<RIVER>"},
        {"start": "09:00", "end": "19:00", "activity": "flower",  "location_id": "<WORKPLACE>", "description": "在花店照料植物"},
        {"start": "19:00", "end": "21:00", "activity": "cafe",    "location_id": "<CAFE>"},
        {"start": "21:00", "end": "24:00", "activity": "home",    "location_id": "<HOME>"},
    ],
    "principal": [
        {"start": "00:00", "end": "06:30", "activity": "sleep",     "location_id": "<HOME>"},
        {"start": "06:30", "end": "07:30", "activity": "breakfast", "location_id": "<HOME>"},
        {"start": "07:30", "end": "12:00", "activity": "school",    "location_id": "<WORKPLACE>", "description": "处理校务"},
        {"start": "12:00", "end": "13:00", "activity": "lunch",     "location_id": "<CAFE>"},
        {"start": "13:00", "end": "18:00", "activity": "school",    "location_id": "<WORKPLACE>", "description": "上课、备课"},
        {"start": "18:00", "end": "20:00", "activity": "library",   "location_id": "loc_library_interior", "description": "去图书馆备课"},
        {"start": "20:00", "end": "24:00", "activity": "home",      "location_id": "<HOME>"},
    ],
    "baker": [
        {"start": "00:00", "end": "04:00", "activity": "sleep",   "location_id": "<HOME>"},
        {"start": "04:00", "end": "07:00", "activity": "bake",    "location_id": "<WORKPLACE>", "description": "凌晨揉面、烘焙"},
        {"start": "07:00", "end": "13:00", "activity": "shop",    "location_id": "<WORKPLACE>", "description": "看店、卖面包"},
        {"start": "13:00", "end": "14:30", "activity": "rest",    "location_id": "<WORKPLACE>", "description": "店内午休"},
        {"start": "14:30", "end": "19:00", "activity": "shop",    "location_id": "<WORKPLACE>"},
        {"start": "19:00", "end": "21:00", "activity": "tavern",  "location_id": "loc_tavern_interior", "description": "去酒馆放松"},
        {"start": "21:00", "end": "24:00", "activity": "home",    "location_id": "<HOME>"},
    ],
    "librarian": [
        {"start": "00:00", "end": "07:30", "activity": "sleep",   "location_id": "<HOME>"},
        {"start": "07:30", "end": "08:30", "activity": "morning", "location_id": "<HOME>"},
        {"start": "08:30", "end": "12:00", "activity": "library", "location_id": "<WORKPLACE>", "description": "整理书架、接待读者"},
        {"start": "12:00", "end": "13:00", "activity": "lunch",   "location_id": "<CAFE>"},
        {"start": "13:00", "end": "20:00", "activity": "library", "location_id": "<WORKPLACE>"},
        {"start": "20:00", "end": "21:30", "activity": "walk",    "location_id": "<PARK>"},
        {"start": "21:30", "end": "24:00", "activity": "home",    "location_id": "<HOME>"},
    ],
    "elder_craftsman": [
        {"start": "00:00", "end": "06:00", "activity": "sleep",     "location_id": "<HOME>"},
        {"start": "06:00", "end": "08:00", "activity": "morning",   "location_id": "<RIVER>"},
        {"start": "08:00", "end": "11:30", "activity": "wood_work", "location_id": "<WORKPLACE>", "description": "在木工坊指点学徒"},
        {"start": "11:30", "end": "13:00", "activity": "lunch",     "location_id": "<HOME>"},
        {"start": "13:00", "end": "16:00", "activity": "wood_work", "location_id": "<WORKPLACE>"},
        {"start": "16:00", "end": "19:00", "activity": "chat",      "location_id": "<PARK>",      "description": "在广场和老朋友闲聊"},
        {"start": "19:00", "end": "24:00", "activity": "home",      "location_id": "<HOME>"},
    ],
    "apprentice": [
        {"start": "00:00", "end": "06:30", "activity": "sleep",      "location_id": "<HOME>"},
        {"start": "06:30", "end": "08:00", "activity": "errand",     "location_id": "loc_grocery_interior", "description": "出门买早餐"},
        {"start": "08:00", "end": "12:00", "activity": "wood_work",  "location_id": "<WORKPLACE>", "description": "学习木工"},
        {"start": "12:00", "end": "13:00", "activity": "lunch",      "location_id": "<WORKPLACE>"},
        {"start": "13:00", "end": "18:30", "activity": "wood_work",  "location_id": "<WORKPLACE>"},
        {"start": "18:30", "end": "21:00", "activity": "tavern",     "location_id": "loc_tavern_interior", "description": "去酒馆喝一杯"},
        {"start": "21:00", "end": "24:00", "activity": "home",       "location_id": "<HOME>"},
    ],
    "tavern_owner": [
        {"start": "00:30", "end": "08:30", "activity": "sleep",      "location_id": "<HOME>"},
        {"start": "08:30", "end": "10:00", "activity": "errand",     "location_id": "loc_grocery_interior", "description": "采购酒馆食材"},
        {"start": "10:00", "end": "12:00", "activity": "prep",       "location_id": "<WORKPLACE>",  "description": "酒馆开门前准备"},
        {"start": "12:00", "end": "14:00", "activity": "lunch_run",  "location_id": "<WORKPLACE>"},
        {"start": "14:00", "end": "17:00", "activity": "rest",       "location_id": "<HOME>"},
        {"start": "17:00", "end": "00:30", "activity": "tavern",     "location_id": "<WORKPLACE>",  "description": "招呼客人到深夜"},
    ],
    "chef": [
        {"start": "00:30", "end": "09:00", "activity": "sleep",      "location_id": "<HOME>"},
        {"start": "09:00", "end": "11:00", "activity": "errand",     "location_id": "loc_grocery_interior", "description": "采买食材"},
        {"start": "11:00", "end": "14:00", "activity": "kitchen",    "location_id": "<WORKPLACE>",  "description": "酒馆厨房备餐"},
        {"start": "14:00", "end": "16:00", "activity": "rest",       "location_id": "<HOME>"},
        {"start": "16:00", "end": "00:30", "activity": "kitchen",    "location_id": "<WORKPLACE>",  "description": "晚餐与夜宵高峰"},
    ],
    "farmer_owner": [
        {"start": "00:00", "end": "05:30", "activity": "sleep",  "location_id": "<HOME>"},
        {"start": "05:30", "end": "11:00", "activity": "farm",   "location_id": "<WORKPLACE>", "description": "下田、巡视作物"},
        {"start": "11:00", "end": "12:30", "activity": "lunch",  "location_id": "<HOME>"},
        {"start": "12:30", "end": "17:00", "activity": "farm",   "location_id": "<WORKPLACE>"},
        {"start": "17:00", "end": "19:00", "activity": "market", "location_id": "loc_grocery_interior", "description": "把作物送到杂货店"},
        {"start": "19:00", "end": "24:00", "activity": "home",   "location_id": "<HOME>"},
    ],
    "farmer_worker": [
        {"start": "00:00", "end": "05:00", "activity": "sleep", "location_id": "<HOME>"},
        {"start": "05:00", "end": "12:00", "activity": "farm",  "location_id": "<WORKPLACE>", "description": "干活、浇水、除草"},
        {"start": "12:00", "end": "13:00", "activity": "lunch", "location_id": "<WORKPLACE>"},
        {"start": "13:00", "end": "18:00", "activity": "farm",  "location_id": "<WORKPLACE>"},
        {"start": "18:00", "end": "20:00", "activity": "tavern","location_id": "loc_tavern_interior"},
        {"start": "20:00", "end": "24:00", "activity": "home",  "location_id": "<HOME>"},
    ],
    "postal_worker": [
        {"start": "00:00", "end": "06:30", "activity": "sleep",    "location_id": "<HOME>"},
        {"start": "06:30", "end": "08:00", "activity": "morning",  "location_id": "<HOME>"},
        {"start": "08:00", "end": "12:00", "activity": "delivery", "location_id": "<PARK>",     "description": "巡街送信"},
        {"start": "12:00", "end": "13:00", "activity": "lunch",    "location_id": "<CAFE>"},
        {"start": "13:00", "end": "17:00", "activity": "delivery", "location_id": "<RIVER>",    "description": "下午投递"},
        {"start": "17:00", "end": "18:00", "activity": "office",   "location_id": "<WORKPLACE>","description": "在邮局整理"},
        {"start": "18:00", "end": "20:00", "activity": "tavern",   "location_id": "loc_tavern_interior"},
        {"start": "20:00", "end": "24:00", "activity": "home",     "location_id": "<HOME>"},
    ],
}


def resolve_schedule(
    template_id: str,
    home_location_id: str,
    workplace_location_id: str | None,
    *,
    park: str = "loc_park",
    river: str = "loc_river_bench",
    cafe: str = "loc_hobbs_cafe_interior",
) -> list[dict[str, Any]]:
    """把 SCHEDULE_TEMPLATES 中的占位符替换为真实 location_id。"""
    template = SCHEDULE_TEMPLATES.get(template_id)
    if not template:
        return []
    placeholders = {
        "<HOME>": home_location_id,
        "<WORKPLACE>": workplace_location_id or home_location_id,
        "<PARK>": park,
        "<RIVER>": river,
        "<CAFE>": cafe,
    }
    resolved: list[dict[str, Any]] = []
    for entry in template:
        loc = entry.get("location_id")
        if isinstance(loc, str) and loc in placeholders:
            entry = {**entry, "location_id": placeholders[loc]}
        resolved.append(entry)
    return resolved


def build_sprite_sheet_id(layers: dict[str, str]) -> str:
    """根据 LPC 层组合生成稳定的 sheet ID（用于 manifest 注册与文件名）。

    自定义组合返回 ``custom_<8位摘要>``，与默认 NPC 的 ``npc_xiaofang`` 等
    形式区分。
    """
    canonical = "|".join(f"{k}={v}" for k, v in sorted(layers.items()))
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:8]
    return f"custom_{digest}"
