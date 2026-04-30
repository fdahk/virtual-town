"""基础单元测试：grid/astar、memory 评分、schedule 选择。"""

from __future__ import annotations

from datetime import datetime, time, timezone

import pytest

from app.domain.simulation.schedule import pick_current_slot
from app.domain.world.grid import SceneGrid, astar
from app.services.memory_service import _keyword_relevance, _recency_score


def test_astar_respects_walls():
    g = SceneGrid(scene_id="s", width=8, height=8, tile_size=32)
    for y in range(6):
        g.block(4, y)
    path = astar(g, (0, 0), (7, 0))
    assert path and path[0] == (0, 0) and path[-1] == (7, 0)
    # 墙上 tile 不在路径内
    for p in path:
        assert not (p[0] == 4 and p[1] < 6)


def test_astar_goal_blocked_returns_empty():
    g = SceneGrid(scene_id="s", width=5, height=5, tile_size=32)
    g.block(3, 3)
    path = astar(g, (0, 0), (3, 3), avoid_hazards=True)
    assert path == []


def test_schedule_slot_picker_handles_overnight():
    template = [
        {"start": "22:00", "end": "06:00", "activity": "sleep", "location_id": "home"},
        {"start": "08:00", "end": "12:00", "activity": "work", "location_id": "cafe"},
    ]
    assert pick_current_slot(template, time(23, 0)).activity == "sleep"
    assert pick_current_slot(template, time(5, 0)).activity == "sleep"
    assert pick_current_slot(template, time(9, 0)).activity == "work"
    assert pick_current_slot(template, time(13, 0)) is None


def test_keyword_relevance_scales_with_overlap():
    query = "小王喜欢美式咖啡"
    low = _keyword_relevance(query, "今天天气很好", [])
    high = _keyword_relevance(query, "小王总是点美式咖啡，不加糖", ["小王", "美式"])
    assert high > low


def test_recency_decays():
    now = datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc)
    recent = datetime(2026, 4, 30, 11, 0, tzinfo=timezone.utc)
    old = datetime(2026, 4, 10, 0, 0, tzinfo=timezone.utc)
    assert _recency_score(recent, now) > _recency_score(old, now)
