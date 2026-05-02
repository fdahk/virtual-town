"""默认 NPC / 美术 / 关系模板库。

供 ``world_gen`` 在新游戏初始化阶段使用，也可被前端通过
``GET /api/games/templates/*`` 拉取后用于 NPC 编辑器。

新增 NPC 时只需要在 ``npc_defaults.py`` 追加一项；
新增 LPC 美术层时同时改 ``art_catalog.py`` + ``scripts/fetch_assets.sh``
+ ``scripts/compose_lpc.py``。
"""

from app.db.templates.art_catalog import (
    ANIMAL_COLOR_CATALOG,
    LPC_LAYER_CATALOG,
    OCCUPATION_PRESETS,
    SCHEDULE_TEMPLATES,
    build_sprite_sheet_id,
    resolve_schedule,
)
from app.db.templates.npc_defaults import (
    DEFAULT_ANIMAL_TEMPLATES,
    DEFAULT_HUMAN_TEMPLATES,
    DEFAULT_PLAYER_TEMPLATE,
    AgentTemplate,
)
from app.db.templates.relationship_seeds import (
    DEFAULT_OWNER_PAIRS,
    DEFAULT_RELATIONSHIP_PAIRS,
)

__all__ = [
    "AgentTemplate",
    "DEFAULT_HUMAN_TEMPLATES",
    "DEFAULT_ANIMAL_TEMPLATES",
    "DEFAULT_PLAYER_TEMPLATE",
    "LPC_LAYER_CATALOG",
    "ANIMAL_COLOR_CATALOG",
    "OCCUPATION_PRESETS",
    "SCHEDULE_TEMPLATES",
    "DEFAULT_RELATIONSHIP_PAIRS",
    "DEFAULT_OWNER_PAIRS",
    "build_sprite_sheet_id",
    "resolve_schedule",
]
