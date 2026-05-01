"""
阶段 19+++：``start_pursuit`` / ``_refresh_pursuits`` 测试。

覆盖：
- 同场景：``start_pursuit`` 设置 path + 标记 pursuing_entity_id。
- 跨场景：先寻向中转 portal（依赖 ``_find_inter_scene_route``）。
- ``_refresh_pursuits``：到达 ≤2 格距离时自动清除 + 触发立即重决策。
- ``_refresh_pursuits``：path 走空但仍有距离时重新规划（处理目标移动）。
- 自我目标 / 玩家目标 / 不存在目标：返回明确错误。
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.domain.simulation.engine import EngineAgent, SimulationEngine
from app.domain.world.grid import SceneGrid


def _make_engine() -> SimulationEngine:
    factory = MagicMock()
    eng = SimulationEngine(factory)
    sim = MagicMock()
    sim.id = "sim-test"
    sim.status = "running"
    sim.world_tick_hz = 5.0
    sim.ai_tick_minutes = 5
    sim.speed_multiplier = 1.0
    sim.current_step = 0
    eng._simulation = sim
    eng._world_time = datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc)
    return eng


def _agent(
    aid: str,
    *,
    scene: str = "scene1",
    x: int = 0,
    y: int = 0,
    state: str = "IDLE",
    is_player: bool = False,
) -> EngineAgent:
    return EngineAgent(
        id=aid,
        entity_type="player" if is_player else "human",
        name=aid,
        scene_id=scene,
        x=x,
        y=y,
        state=state,
        is_player=is_player,
    )


def _portal(from_scene: str, to_scene: str, fx: int, fy: int, tx: int = 0, ty: int = 0):
    return SimpleNamespace(
        id=f"portal_{from_scene}_{to_scene}",
        from_scene_id=from_scene,
        from_tile={"x": fx, "y": fy},
        to_scene_id=to_scene,
        to_tile={"x": tx, "y": ty},
        interaction_type="auto_enter",
    )


def _patch_grid(monkeypatch, scene_grids: dict[str, SceneGrid]) -> None:
    cache = MagicMock()
    cache.get_grid.side_effect = lambda sid: scene_grids.get(sid)
    cache.all_scene_ids.return_value = list(scene_grids.keys())
    monkeypatch.setattr(
        "app.domain.simulation.engine.get_scene_cache", lambda: cache
    )


# ---------------------------------------------------------------------------
# start_pursuit
# ---------------------------------------------------------------------------


def test_start_pursuit_same_scene_sets_path(monkeypatch):
    eng = _make_engine()
    eng._agents = {
        "a": _agent("a", x=2, y=2),
        "b": _agent("b", x=8, y=2),
    }
    grid = SceneGrid(scene_id="scene1", width=16, height=16, tile_size=32)
    _patch_grid(monkeypatch, {"scene1": grid})
    eng._portals_by_scene = {}

    out = eng.start_pursuit("a", "b", reason="去找 b 聊天")
    assert out["ok"] is True
    a = eng._agents["a"]
    assert a.pursuing_entity_id == "b"
    assert a.pursuing_reason == "去找 b 聊天"
    assert a.path, "should have a non-empty path toward b's neighbor"
    # 终点应是 b 周围的相邻格之一
    last = a.path[-1]
    assert abs(last[0] - 8) + abs(last[1] - 2) == 1


def test_start_pursuit_cross_scene_uses_portal(monkeypatch):
    eng = _make_engine()
    eng._agents = {
        "a": _agent("a", scene="school_inside", x=3, y=3),
        "b": _agent("b", scene="cafe_inside", x=5, y=5),
    }
    grids = {
        "school_inside": SceneGrid(scene_id="school_inside", width=16, height=16, tile_size=32),
        "cafe_inside": SceneGrid(scene_id="cafe_inside", width=16, height=16, tile_size=32),
        "outdoor": SceneGrid(scene_id="outdoor", width=16, height=16, tile_size=32),
    }
    _patch_grid(monkeypatch, grids)
    eng._portals_by_scene = {
        "school_inside": [_portal("school_inside", "outdoor", 7, 7)],
        "outdoor": [
            _portal("outdoor", "school_inside", 1, 1),
            _portal("outdoor", "cafe_inside", 12, 12),
        ],
        "cafe_inside": [_portal("cafe_inside", "outdoor", 0, 0)],
    }

    out = eng.start_pursuit("a", "b", reason="找 b")
    assert out["ok"] is True
    a = eng._agents["a"]
    # 应当走向 school_inside 的中转 portal 出口 (7,7)
    assert a.path, "expected path toward school's portal"
    assert a.path[-1] == (7, 7)
    assert a.pursuing_entity_id == "b"


def test_start_pursuit_already_close_skips(monkeypatch):
    eng = _make_engine()
    eng._agents = {
        "a": _agent("a", x=5, y=5),
        "b": _agent("b", x=5, y=6),
    }
    grid = SceneGrid(scene_id="scene1", width=16, height=16, tile_size=32)
    _patch_grid(monkeypatch, {"scene1": grid})

    out = eng.start_pursuit("a", "b")
    assert out["ok"] is True
    assert out["reason"] == "ALREADY_THERE"
    assert eng._agents["a"].pursuing_entity_id is None


def test_start_pursuit_self_or_player_or_missing(monkeypatch):
    eng = _make_engine()
    eng._agents = {
        "a": _agent("a"),
        "p": _agent("p", is_player=True),
    }
    grid = SceneGrid(scene_id="scene1", width=16, height=16, tile_size=32)
    _patch_grid(monkeypatch, {"scene1": grid})

    assert eng.start_pursuit("a", "a")["reason"] == "INVALID_ARGUMENTS"
    assert eng.start_pursuit("a", "missing")["reason"] == "TARGET_NOT_FOUND"
    assert eng.start_pursuit("a", "p")["reason"] == "TARGET_NOT_FOUND"


# ---------------------------------------------------------------------------
# _refresh_pursuits
# ---------------------------------------------------------------------------


def test_refresh_pursuit_clears_when_close(monkeypatch):
    eng = _make_engine()
    a = _agent("a", x=5, y=5)
    a.pursuing_entity_id = "b"
    a.pursuing_reason = "找 b"
    a.last_decision_at = datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc)
    eng._agents = {
        "a": a,
        "b": _agent("b", x=5, y=6),
    }
    grid = SceneGrid(scene_id="scene1", width=16, height=16, tile_size=32)
    _patch_grid(monkeypatch, {"scene1": grid})

    eng._refresh_pursuits()
    assert a.pursuing_entity_id is None
    assert a.last_decision_at is None  # 立即重决策
    assert a.path == []


def test_refresh_pursuit_recomputes_when_path_empty(monkeypatch):
    eng = _make_engine()
    a = _agent("a", x=2, y=2)
    a.pursuing_entity_id = "b"
    a.pursuing_reason = "找 b"
    a.path = []
    eng._agents = {
        "a": a,
        "b": _agent("b", x=10, y=2),  # b 移动到了远处
    }
    grid = SceneGrid(scene_id="scene1", width=16, height=16, tile_size=32)
    _patch_grid(monkeypatch, {"scene1": grid})
    eng._portals_by_scene = {}

    eng._refresh_pursuits()
    # 应当重新规划路径
    assert a.path, "should recompute path when it ran out"
    assert a.pursuing_entity_id == "b"


def test_refresh_pursuit_drops_when_target_gone(monkeypatch):
    eng = _make_engine()
    a = _agent("a", x=2, y=2)
    a.pursuing_entity_id = "ghost"
    a.pursuing_reason = "找鬼"
    eng._agents = {"a": a}
    grid = SceneGrid(scene_id="scene1", width=16, height=16, tile_size=32)
    _patch_grid(monkeypatch, {"scene1": grid})

    eng._refresh_pursuits()
    assert a.pursuing_entity_id is None
    assert a.pursuing_reason is None
