"""
阶段 19++：rule_agent 多跳寻路 + 不可达兜底测试。

覆盖：
- ``_find_inter_scene_route``：BFS 找出 ``school_inside → outdoor → cafe_inside`` 链路。
- ``decide_human_action``：当目标场景没有直连 portal 时，使用中转 portal；
  当中转也找不到时，转为在当前场景内 ``wander``，并在 metadata 中标注
  ``stuck=True`` + ``unreachable_location_id``，避免 NPC 长期僵在 WAITING。
- ``decide_human_action``：传入 ``unreachable_location_ids`` 黑名单时，
  跳过该日程目标，直接 wander。
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

from app.domain.simulation.rule_agent import (
    _find_inter_scene_route,
    _find_portal_from_to,
    decide_human_action,
)
from app.domain.world.grid import SceneGrid


def _portal(from_scene: str, to_scene: str, x: int, y: int) -> SimpleNamespace:
    return SimpleNamespace(
        from_scene_id=from_scene,
        to_scene_id=to_scene,
        from_tile={"x": x, "y": y},
        to_tile={"x": 0, "y": 0},
    )


def _grid(scene_id: str = "scene", w: int = 16, h: int = 16) -> SceneGrid:
    return SceneGrid(scene_id=scene_id, width=w, height=h, tile_size=32)


def _agent_row(home: str | None = None, schedule: list | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id="npc_test",
        entity_type="human",
        schedule_template=schedule or [],
        home_location_id=home,
        personality=[],
        appearance={},
    )


def _state_row(scene: str, x: int = 5, y: int = 5) -> SimpleNamespace:
    return SimpleNamespace(
        agent_id="npc_test",
        scene_id=scene,
        x=x,
        y=y,
        state="IDLE",
        status_effects=[],
    )


# ---------------------------------------------------------------------------
# _find_inter_scene_route
# ---------------------------------------------------------------------------


def test_inter_scene_route_two_hop_via_outdoor():
    # school_inside → outdoor → cafe_inside（school 与 cafe 没有直连 portal）
    portals = {
        "school_inside": [_portal("school_inside", "outdoor", 2, 2)],
        "outdoor": [
            _portal("outdoor", "school_inside", 3, 3),
            _portal("outdoor", "cafe_inside", 4, 4),
        ],
        "cafe_inside": [_portal("cafe_inside", "outdoor", 1, 1)],
    }
    route = _find_inter_scene_route(portals, "school_inside", "cafe_inside")
    assert route == ["outdoor", "cafe_inside"]
    # 起点 == 终点 → 空列表
    assert _find_inter_scene_route(portals, "outdoor", "outdoor") == []
    # 完全不连通的孤立场景 → None
    portals["isolated"] = []
    assert _find_inter_scene_route(portals, "isolated", "cafe_inside") is None


def test_find_portal_direct_match():
    portals = {
        "outdoor": [_portal("outdoor", "cafe_inside", 4, 4)],
    }
    assert _find_portal_from_to(portals, "outdoor", "cafe_inside") == (4, 4)
    assert _find_portal_from_to(portals, "outdoor", "missing") is None


# ---------------------------------------------------------------------------
# decide_human_action：多跳场景路由
# ---------------------------------------------------------------------------


def test_decide_uses_intermediate_portal_when_no_direct():
    """
    NPC 在 school_inside，日程目标在 cafe_inside；school 没有直达 cafe 的 portal，
    但有 outdoor 中转 → 决策应当走向 school→outdoor 的 portal（位于 (2,2)）
    并指向中间场景，而不是返回 wait。
    """
    portals = {
        "school_inside": [_portal("school_inside", "outdoor", 2, 2)],
        "outdoor": [
            _portal("outdoor", "school_inside", 3, 3),
            _portal("outdoor", "cafe_inside", 4, 4),
        ],
        "cafe_inside": [_portal("cafe_inside", "outdoor", 1, 1)],
    }
    grids = {
        "school_inside": _grid("school_inside"),
        "outdoor": _grid("outdoor"),
        "cafe_inside": _grid("cafe_inside"),
    }
    locations = {
        "loc_cafe_seat": {
            "id": "loc_cafe_seat",
            "scene_id": "cafe_inside",
            "bounds": {"x": 5, "y": 5, "width": 2, "height": 2},
            "entry_tiles": [{"x": 5, "y": 5}],
        }
    }
    schedule = [
        {"start": "00:00", "end": "23:59", "activity": "study", "location_id": "loc_cafe_seat"}
    ]
    decision = decide_human_action(
        agent_row=_agent_row(schedule=schedule),
        state_row=_state_row("school_inside", x=5, y=5),
        world_time=datetime(2026, 5, 1, 12, 0),
        locations=locations,
        grids=grids,
        portals_by_scene=portals,
    )
    # 应当往中转 portal 移动，target_scene 指向 outdoor 而不是直接 cafe_inside
    assert decision.action_type == "move_to_location"
    assert decision.target_scene_id == "outdoor"
    assert decision.path, "expected a non-empty path to the intermediate portal"


def test_decide_falls_back_to_wander_when_no_route():
    """
    完全没有任何 portal 通往目标场景 → 不再返回 wait，转为 wander，
    并在 metadata 中携带 unreachable_location_id 供引擎拉黑。
    """
    portals: dict[str, list[Any]] = {"isolated": []}
    grids = {"isolated": _grid("isolated")}
    locations = {
        "loc_far": {
            "id": "loc_far",
            "scene_id": "no_such_scene",
            "bounds": {"x": 5, "y": 5, "width": 2, "height": 2},
            "entry_tiles": [{"x": 5, "y": 5}],
        }
    }
    schedule = [
        {"start": "00:00", "end": "23:59", "activity": "go", "location_id": "loc_far"}
    ]
    decision = decide_human_action(
        agent_row=_agent_row(schedule=schedule),
        state_row=_state_row("isolated", x=5, y=5),
        world_time=datetime(2026, 5, 1, 12, 0),
        locations=locations,
        grids=grids,
        portals_by_scene=portals,
    )
    # 不应当是 plain wait；至少应当 wander 或带 stuck 标志
    if decision.action_type == "wait":
        assert decision.metadata.get("stuck") is True
    else:
        assert decision.action_type == "wander"
    assert decision.metadata.get("unreachable_location_id") == "loc_far"


def test_recovery_wander_marks_target_unreachable():
    """
    Regression：portal 存在但 NPC 走不到（被障碍墙隔开）→ rule_agent 走
    ``_make_recovery_or_wait`` 的 wander 分支兜底。

    死循环 bug：原实现 wander 分支不带 ``unreachable_location_id``，引擎
    不写黑名单，下个 ai_tick 又选同一目标 → 死循环 "绕行中→到达目的地"。

    Fix 后：wander 分支也带 ``unreachable_location_id``，让引擎下个 tick 命中
    黑名单走 ``_wander_within_scene_or_wait`` 的"目标暂时不可达"分支。
    """
    portals = {
        "indoor": [_portal("indoor", "outdoor", 15, 15)],
        "outdoor": [_portal("outdoor", "indoor", 0, 0)],
    }
    indoor = _grid("indoor", w=20, h=20)
    # 横着一道墙 y=10，把 (5,5) 起点和 portal (15,15) 隔开
    for x in range(0, 20):
        indoor.block(x, 10)
    grids = {"indoor": indoor, "outdoor": _grid("outdoor")}
    locations = {
        "loc_target": {
            "id": "loc_target",
            "scene_id": "outdoor",
            "bounds": {"x": 5, "y": 5, "width": 2, "height": 2},
            "entry_tiles": [{"x": 5, "y": 5}],
        }
    }
    schedule = [
        {"start": "00:00", "end": "23:59", "activity": "go", "location_id": "loc_target"}
    ]
    decision = decide_human_action(
        agent_row=_agent_row(schedule=schedule),
        state_row=_state_row("indoor", x=5, y=5),
        world_time=datetime(2026, 5, 1, 12, 0),
        locations=locations,
        grids=grids,
        portals_by_scene=portals,
    )
    # NPC 在 5 格内有大量空地，BFS 一定找得到 recovery → 走 wander 分支
    assert decision.action_type == "wander"
    # 关键断言：必须把目标加入黑名单，否则引擎下个 tick 又会选同一目标死循环
    assert decision.metadata.get("unreachable_location_id") == "loc_target"
    # wander 分支不应带 stuck=True（NPC 在动，不必每个 tick 都重决策）
    assert not decision.metadata.get("stuck")


def test_decide_skips_blacklisted_target():
    """
    传入 unreachable_location_ids → 直接跳过该 slot 的 location，进入 wander。
    """
    portals = {
        "outdoor": [_portal("outdoor", "cafe_inside", 4, 4)],
        "cafe_inside": [_portal("cafe_inside", "outdoor", 1, 1)],
    }
    grids = {"outdoor": _grid("outdoor"), "cafe_inside": _grid("cafe_inside")}
    locations = {
        "loc_cafe_seat": {
            "id": "loc_cafe_seat",
            "scene_id": "cafe_inside",
            "bounds": {"x": 5, "y": 5, "width": 2, "height": 2},
            "entry_tiles": [{"x": 5, "y": 5}],
        }
    }
    schedule = [
        {"start": "00:00", "end": "23:59", "activity": "study", "location_id": "loc_cafe_seat"}
    ]
    decision = decide_human_action(
        agent_row=_agent_row(schedule=schedule),
        state_row=_state_row("outdoor", x=5, y=5),
        world_time=datetime(2026, 5, 1, 12, 0),
        locations=locations,
        grids=grids,
        portals_by_scene=portals,
        unreachable_location_ids={"loc_cafe_seat"},
    )
    # 应当不再尝试 cafe_seat（即不会 move_to_location 到那里）
    assert decision.action_type != "move_to_location" or decision.target_location_id != "loc_cafe_seat"
