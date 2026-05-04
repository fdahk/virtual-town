"""暴风雨避难规则：仅在室外触发，室内交给日程，避免 portal 振荡。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.domain.simulation.rule_agent import NaturalWorldContext, decide_human_action
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


def _agent_row(schedule: list) -> SimpleNamespace:
    return SimpleNamespace(
        id="npc_test",
        entity_type="human",
        schedule_template=schedule,
        home_location_id=None,
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


def test_storm_skipped_when_already_indoor():
    """已在 cafe indoor 时不应再触发「去找别的室内」而走向 portal。"""
    portals = {
        "outdoor": [_portal("outdoor", "cafe_inside", 4, 4)],
        "cafe_inside": [_portal("cafe_inside", "outdoor", 1, 1)],
    }
    grids = {"outdoor": _grid("outdoor"), "cafe_inside": _grid("cafe_inside")}
    locations = {
        "loc_work": {
            "id": "loc_work",
            "scene_id": "cafe_inside",
            "location_type": "cafe",
            "bounds": {"x": 8, "y": 8, "width": 2, "height": 2},
            "entry_tiles": [{"x": 8, "y": 8}],
        }
    }
    schedule = [
        {
            "start": "00:00",
            "end": "23:59",
            "activity": "work",
            "location_id": "loc_work",
            "description": "下午继续服务客人",
        }
    ]
    decision = decide_human_action(
        agent_row=_agent_row(schedule),
        state_row=_state_row("cafe_inside", x=5, y=5),
        world_time=datetime(2026, 5, 1, 14, 0),
        locations=locations,
        grids=grids,
        portals_by_scene=portals,
        natural_ctx=NaturalWorldContext(storm_active=True),
        scene_type_by_id={"cafe_inside": "indoor", "outdoor": "outdoor"},
    )
    assert "暴风雨" not in decision.description
    assert "下午继续服务客人" in decision.description or decision.action_type in (
        "move_to_location",
        "interact",
    )


def test_storm_seeks_shelter_when_outdoor():
    """室外 + 暴风雨 → 规则仍应导向室内 portal。"""
    portals = {
        "outdoor": [_portal("outdoor", "cafe_inside", 4, 4)],
        "cafe_inside": [_portal("cafe_inside", "outdoor", 1, 1)],
    }
    grids = {"outdoor": _grid("outdoor"), "cafe_inside": _grid("cafe_inside")}
    locations = {
        "loc_cafe": {
            "id": "loc_cafe",
            "scene_id": "cafe_inside",
            "location_type": "cafe",
            "bounds": {"x": 5, "y": 5, "width": 2, "height": 2},
            "entry_tiles": [{"x": 5, "y": 5}],
        }
    }
    schedule = [
        {"start": "00:00", "end": "23:59", "activity": "idle", "location_id": None}
    ]
    decision = decide_human_action(
        agent_row=_agent_row(schedule),
        state_row=_state_row("outdoor", x=5, y=5),
        world_time=datetime(2026, 5, 1, 14, 0),
        locations=locations,
        grids=grids,
        portals_by_scene=portals,
        natural_ctx=NaturalWorldContext(storm_active=True),
        scene_type_by_id={"cafe_inside": "indoor", "outdoor": "outdoor"},
    )
    assert "暴风雨" in decision.description
