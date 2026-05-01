"""层次化规划的规则兜底单元测试（阶段 15）。"""

from __future__ import annotations

from datetime import datetime, time

from app.db.models import Agent
from app.domain.planning.hierarchical import (
    DailyPlan,
    DailyPlanSegment,
    PlanningService,
    _day_key,
    _parse_hhmm,
)


def _mock_agent(
    *,
    schedule: list[dict] | None = None,
    home: str | None = None,
    lifestyle: str | None = None,
) -> Agent:
    # 直接构造 ORM 对象，不经过 DB
    a = Agent(
        id="npc_test",
        entity_type="human",
        name="测试",
        schedule_template=schedule or [],
        home_location_id=home,
        lifestyle=lifestyle,
    )
    a.long_term_goals = ["测试目标"]
    a.personality = ["测试"]
    a.background = ""
    return a


def test_rule_daily_plan_uses_schedule_template() -> None:
    svc = PlanningService()
    agent = _mock_agent(
        schedule=[
            {
                "start": "07:00",
                "end": "08:00",
                "activity": "起床",
                "location_id": "loc_home",
                "description": "起床洗漱",
            },
            {
                "start": "08:00",
                "end": "17:00",
                "activity": "工作",
                "location_id": "loc_cafe",
                "description": "在咖啡店",
            },
        ]
    )
    out = svc._rule_daily_plan(agent)
    assert len(out.segments) == 2
    assert out.segments[0].start == "07:00"
    assert out.segments[0].location_id == "loc_home"
    assert out.segments[1].activity == "工作"


def test_rule_daily_plan_empty_schedule_uses_default() -> None:
    svc = PlanningService()
    agent = _mock_agent()
    out = svc._rule_daily_plan(agent)
    assert len(out.segments) >= 2
    assert any(s.activity == "回家休息" for s in out.segments)


def test_pick_segment_simple_range() -> None:
    svc = PlanningService()
    plan = DailyPlan(
        id="plan_x",
        day_key="2026-05-01",
        summary="",
        segments=[
            DailyPlanSegment(start="07:00", end="08:00", activity="A"),
            DailyPlanSegment(start="08:00", end="17:00", activity="B"),
            DailyPlanSegment(start="22:00", end="07:00", activity="sleep"),
        ],
    )
    assert svc.pick_segment(plan, datetime(2026, 5, 1, 12, 0)).activity == "B"
    assert svc.pick_segment(plan, datetime(2026, 5, 1, 7, 30)).activity == "A"


def test_pick_segment_crossing_midnight() -> None:
    svc = PlanningService()
    plan = DailyPlan(
        id="plan_x",
        day_key="2026-05-01",
        summary="",
        segments=[
            DailyPlanSegment(start="07:00", end="22:00", activity="day"),
            DailyPlanSegment(start="22:00", end="07:00", activity="sleep"),
        ],
    )
    assert svc.pick_segment(plan, datetime(2026, 5, 1, 23, 0)).activity == "sleep"
    assert svc.pick_segment(plan, datetime(2026, 5, 1, 3, 0)).activity == "sleep"


def test_day_key_format() -> None:
    assert _day_key(datetime(2026, 5, 1, 12, 0)) == "2026-05-01"


def test_parse_hhmm_fallback() -> None:
    # 非法字符串要回到默认值
    assert _parse_hhmm("bad", time(9, 30)) == time(9, 30)
    assert _parse_hhmm("08:15", time(0, 0)) == time(8, 15)
