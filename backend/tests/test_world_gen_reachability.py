"""默认世界生成器连通性回归测试。

背景（2026-05-02）：22 NPC 测试时观察到"小芳一直绕行 / 暂时不可达"刷屏，
怀疑某 building 的 entry 在某些参数下被堵死。bug 重启后无法复现，
因此把"代码合理性"沉淀成 CI 能抓的连通性断言：

1. 每个 location 的 ``entry_tiles[0]`` 必须落在自己声明的 scene 内、且
   网格上 ``is_walkable=True``（避开 hazards）；
2. outdoor scene 上每个 location 的 entry 必须能从中央广场 (54, 42)
   A* 到达；
3. 每个 portal 的 ``from_tile`` 必须在 ``from_scene`` 网格上是 walkable；
4. 每个室内 ``room`` location 的 entry 必须能从该 indoor scene 的标准
   入口 ``(7, INDOOR_H-2)`` A* 到达——也就是 NPC 从 portal 落地后
   一定能走到自己的 entry。

任何破坏世界连通性的改动（新加的建筑墙体压住主街、装饰物落在门格上、
室内家具堵住 entry 等）都会让此处断言失败，CI 会立即报警。
"""

from __future__ import annotations

import pytest

from app.domain.world.grid import SceneGrid, TileInfo, astar
from app.domain.world_gen import GenerationConfig, generate_world
from app.domain.world_gen.interiors import INDOOR_H
from app.domain.world_gen.outdoor import build_outdoor_tiles
from app.domain.world_gen.types import WorldPlan

# ---------------------------------------------------------------------------
# 辅助：把 WorldPlan 的 tiles + blocks_movement 对象组合成内存 SceneGrid
# 与 ``scene_cache._load_scene`` 的逻辑保持一致，便于跑出和运行时一样的网格。
# ---------------------------------------------------------------------------


def _build_grid(plan: WorldPlan, scene_id: str) -> SceneGrid:
    scene_meta = next(s for s in plan.scenes if s["id"] == scene_id)
    grid = SceneGrid(
        scene_id=scene_id,
        width=int(scene_meta["width"]),
        height=int(scene_meta["height"]),
        tile_size=int(scene_meta["tile_size"]),
    )
    for tile in plan.tiles_by_scene.get(scene_id, []):
        grid.tiles[(tile["x"], tile["y"])] = TileInfo(
            x=tile["x"],
            y=tile["y"],
            walkable=bool(tile["walkable"]),
            blocks_movement=bool(tile["blocks_movement"]),
            hazard_type=tile.get("hazard_type"),
            hazard_level=tile.get("hazard_level"),
            terrain=str(tile.get("terrain", "grass")),
        )
    for obj in plan.world_objects:
        if obj.get("scene_id") != scene_id or not obj.get("blocks_movement"):
            continue
        size = obj.get("size") or {}
        pos = obj.get("position") or {}
        w = int(size.get("width", 1))
        h = int(size.get("height", 1))
        x0 = int(pos.get("x", 0))
        y0 = int(pos.get("y", 0))
        for dx in range(w):
            for dy in range(h):
                grid.block(x0 + dx, y0 + dy)
    return grid


@pytest.fixture(scope="module")
def plan() -> WorldPlan:
    """默认 22 NPC + 默认 outdoor 尺寸的世界计划，整模块共享一份。"""
    return generate_world(GenerationConfig.default(seed=42))


@pytest.fixture(scope="module")
def grids(plan: WorldPlan) -> dict[str, SceneGrid]:
    """每个 scene 一份预构建的内存网格（共享，避免重复 build）。"""
    return {s["id"]: _build_grid(plan, s["id"]) for s in plan.scenes}


# ---------------------------------------------------------------------------
# 1. entry_tile 自身合法性
# ---------------------------------------------------------------------------


def test_every_location_entry_is_walkable(
    plan: WorldPlan, grids: dict[str, SceneGrid]
) -> None:
    """每个 location.entry_tiles[0] 必须在所属 scene 内、网格上可走。"""
    failures: list[str] = []
    for loc in plan.locations:
        scene_id = loc["scene_id"]
        grid = grids.get(scene_id)
        if grid is None:
            failures.append(f"{loc['id']}: 不存在的 scene_id={scene_id}")
            continue
        entries = loc.get("entry_tiles") or []
        if not entries:
            failures.append(f"{loc['id']}: 缺 entry_tiles")
            continue
        ex, ey = int(entries[0]["x"]), int(entries[0]["y"])
        if not grid.in_bounds(ex, ey):
            failures.append(
                f"{loc['id']}: entry ({ex},{ey}) 超出场景 "
                f"{scene_id} 边界 {grid.width}x{grid.height}"
            )
            continue
        if not grid.is_walkable(ex, ey, avoid_hazards=True):
            tile = grid.get(ex, ey)
            failures.append(
                f"{loc['id']}: entry ({ex},{ey}) 不可走 "
                f"(walkable={tile.walkable if tile else 'None'}, "
                f"blocks={tile.blocks_movement if tile else 'None'}, "
                f"terrain={tile.terrain if tile else 'None'})"
            )
    assert not failures, "以下 location entry 不合法：\n  " + "\n  ".join(failures)


# ---------------------------------------------------------------------------
# 2. outdoor 全场连通：从中央广场到每个 outdoor location entry
# ---------------------------------------------------------------------------


def test_outdoor_locations_reachable_from_center(
    plan: WorldPlan, grids: dict[str, SceneGrid]
) -> None:
    """从中央广场 (54, 42) 出发，每个 outdoor location 的 entry 都能 A* 到达。

    中央广场是 OUTDOOR_AREAS 里唯一保证 walkable 的稳定锚点（坐标在
    ``loc_park`` 范围内，建筑/河流/围栏均不与之冲突）。
    """
    outdoor_scene = plan.outdoor_scene_id
    grid = grids[outdoor_scene]
    start = (54, 42)
    assert grid.is_walkable(*start, avoid_hazards=True), (
        f"测试锚点 {start} 自己就不可走，outdoor 网格构造有问题"
    )

    failures: list[str] = []
    for loc in plan.locations:
        if loc["scene_id"] != outdoor_scene:
            continue
        entries = loc.get("entry_tiles") or []
        if not entries:
            continue
        ex, ey = int(entries[0]["x"]), int(entries[0]["y"])
        if (ex, ey) == start:
            continue
        path = astar(grid, start, (ex, ey), avoid_hazards=True)
        if not path:
            failures.append(
                f"{loc['id']} entry=({ex},{ey}) 从中央广场 (54,42) 走不到"
            )
    assert not failures, (
        "outdoor 部分 location entry 不可达：\n  " + "\n  ".join(failures)
    )


# ---------------------------------------------------------------------------
# 3. portal.from_tile 必须 walkable（NPC 必须能站到 portal 上才能传送）
# ---------------------------------------------------------------------------


def test_every_portal_from_tile_is_walkable(
    plan: WorldPlan, grids: dict[str, SceneGrid]
) -> None:
    failures: list[str] = []
    for p in plan.portals:
        from_scene = p["from_scene_id"]
        ft = p["from_tile"]
        fx, fy = int(ft["x"]), int(ft["y"])
        grid = grids.get(from_scene)
        if grid is None:
            failures.append(f"{p['id']}: 不存在的 from_scene={from_scene}")
            continue
        if not grid.in_bounds(fx, fy):
            failures.append(
                f"{p['id']}: from_tile ({fx},{fy}) 超出 {from_scene} 边界"
            )
            continue
        if not grid.is_walkable(fx, fy, avoid_hazards=True):
            failures.append(
                f"{p['id']}: from_tile ({fx},{fy}) 在 {from_scene} 不可走"
            )
    assert not failures, "portal from_tile 不合法：\n  " + "\n  ".join(failures)


# ---------------------------------------------------------------------------
# 4. 室内：portal 落地点 → location entry 可达
# ---------------------------------------------------------------------------


def test_indoor_entry_reachable_from_portal_landing(
    plan: WorldPlan, grids: dict[str, SceneGrid]
) -> None:
    """室内场景：NPC 从外面 portal 进来后必须能走到 ``room`` location 的 entry。

    ``build_indoor_tiles`` 里 portal 的 to_tile 都设置为 ``(7, INDOOR_H-2)``，
    每间室内 location 的 entry 也是这格——所以本质是验证 NPC 在落地点
    没有被家具卡住、且没有家具落在门口必经路上。
    """
    landing = (7, INDOOR_H - 2)
    failures: list[str] = []
    for loc in plan.locations:
        if loc.get("location_type") != "room":
            continue
        scene_id = loc["scene_id"]
        grid = grids.get(scene_id)
        if grid is None:
            continue
        entries = loc.get("entry_tiles") or []
        if not entries:
            continue
        ex, ey = int(entries[0]["x"]), int(entries[0]["y"])
        if not grid.is_walkable(*landing, avoid_hazards=True):
            failures.append(
                f"{loc['id']}: 室内 portal 落地点 {landing} 自己被家具堵住了"
            )
            continue
        path = astar(grid, landing, (ex, ey), avoid_hazards=True)
        if not path:
            failures.append(
                f"{loc['id']}: entry ({ex},{ey}) 从 portal 落地点 "
                f"{landing} 走不到（家具布局把入口区切断了）"
            )
    assert not failures, (
        "室内 location 入口被家具阻断：\n  " + "\n  ".join(failures)
    )


# ---------------------------------------------------------------------------
# 5. 跨场景：从 outdoor 中央广场 → outdoor portal → indoor entry 整链可达
# ---------------------------------------------------------------------------


def test_outdoor_to_indoor_chain_reachable(
    plan: WorldPlan, grids: dict[str, SceneGrid]
) -> None:
    """端到端检查：从中央广场出发，对每个有室内的 building/home，
    走 outdoor → portal_in.from_tile → portal 切场景 → portal.to_tile →
    indoor entry，全程不应该断链。

    这是 NPC 真实的"我要去咖啡店"日常路径——能跑通就证明 schedule
    上 ``<WORKPLACE> = loc_hobbs_cafe_interior`` 不会复现"暂时不可达"。
    """
    outdoor_scene = plan.outdoor_scene_id
    outdoor_grid = grids[outdoor_scene]
    start = (54, 42)
    failures: list[str] = []

    portal_in_by_scene_pair: dict[tuple[str, str], tuple[int, int]] = {}
    portal_to_tile_by_id: dict[str, tuple[int, int]] = {}
    for p in plan.portals:
        portal_in_by_scene_pair[(p["from_scene_id"], p["to_scene_id"])] = (
            int(p["from_tile"]["x"]),
            int(p["from_tile"]["y"]),
        )
        portal_to_tile_by_id[p["id"]] = (
            int(p["to_tile"]["x"]),
            int(p["to_tile"]["y"]),
        )

    for loc in plan.locations:
        if loc.get("location_type") != "room":
            continue
        indoor_scene = loc["scene_id"]
        portal_from = portal_in_by_scene_pair.get((outdoor_scene, indoor_scene))
        if portal_from is None:
            failures.append(
                f"{loc['id']}: 没有 outdoor→{indoor_scene} 的 portal"
            )
            continue
        path_outdoor = astar(outdoor_grid, start, portal_from, avoid_hazards=True)
        if not path_outdoor:
            failures.append(
                f"{loc['id']}: outdoor 段 (54,42)→{portal_from} 走不通"
            )
            continue
        indoor_grid = grids.get(indoor_scene)
        if indoor_grid is None:
            failures.append(f"{loc['id']}: indoor scene {indoor_scene} 不存在")
            continue
        landing = (7, INDOOR_H - 2)
        if not indoor_grid.is_walkable(*landing, avoid_hazards=True):
            failures.append(f"{loc['id']}: 室内落地点 {landing} 不可走")
            continue
        entries = loc.get("entry_tiles") or []
        if not entries:
            continue
        target = (int(entries[0]["x"]), int(entries[0]["y"]))
        path_indoor = astar(indoor_grid, landing, target, avoid_hazards=True)
        if not path_indoor:
            failures.append(
                f"{loc['id']}: 室内段 {landing}→{target} 走不通"
            )

    assert not failures, (
        "跨场景路径链断裂（NPC 进不了某些建筑）：\n  "
        + "\n  ".join(failures)
    )


# ---------------------------------------------------------------------------
# 6. fail-fast：过小尺寸必须立即抛错而不是静默生成残缺世界
# ---------------------------------------------------------------------------


def test_build_outdoor_tiles_rejects_too_small_map() -> None:
    """``build_outdoor_tiles`` 在 outdoor 尺寸装不下硬编码布局时抛 ValueError。

    Regression：曾经的 90×60 默认尺寸会让 8 户家 + 农场屋 + 木工坊的
    door / portal_in.from_tile 落在地图外，``set_tile`` 静默跳过、
    location 仍然把越界坐标写入数据库——NPC 永远走不到那些目标，
    日志里疯狂打印 "暂时不可达，先在附近闲逛"。
    """
    import random

    with pytest.raises(ValueError) as exc:
        build_outdoor_tiles(90, 60, rng=random.Random(0))
    msg = str(exc.value)
    # 错误消息要给出关键越界条目，方便排查
    assert "90x60" in msg
    assert "BUILDING" in msg or "HOME" in msg or "AREA" in msg


def test_build_outdoor_tiles_accepts_default_size() -> None:
    """默认 120×90 必须能正常构建，与 ``GenerationConfig.default()`` 协同。"""
    import random

    tiles, buildings, homes = build_outdoor_tiles(120, 90, rng=random.Random(0))
    assert tiles, "构建结果不应为空"
    assert len(buildings) >= 10, "应至少 10 个公共建筑"
    assert len(homes) >= 12, "应至少 12 处住宅"
