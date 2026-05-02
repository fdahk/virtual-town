"""新游戏世界生成器。

根据用户传入的 NPC 模板列表 + 世界参数，生成符合 ``map_scenes`` /
``map_tiles`` / ``locations`` / ``portals`` / ``world_objects`` /
``agents`` / ``relationships`` 规范的完整数据计划。

模块拆分：
- ``types.py``：``WorldPlan`` 等数据类
- ``outdoor.py``：参数化室外地图布局（默认尺寸见 ``WORLD_GEN_OUTDOOR_*``）
- ``interiors.py``：16×12 室内场景与家具
- ``placement.py``：NPC → 住宅 / 工作场所映射
- ``relationships.py``：N×N 双向关系矩阵
- ``generator.py``：编排
- ``apply.py``：把计划写入数据库
"""

from app.domain.world_gen.apply import apply_plan
from app.domain.world_gen.generator import (
    GenerationConfig,
    generate_world,
)
from app.domain.world_gen.types import (
    BuildingPlan,
    HomePlan,
    WorldPlan,
)

__all__ = [
    "GenerationConfig",
    "WorldPlan",
    "BuildingPlan",
    "HomePlan",
    "generate_world",
    "apply_plan",
]
