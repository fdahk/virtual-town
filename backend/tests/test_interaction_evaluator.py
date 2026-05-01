"""
阶段 19：交互请求评估器单元测试。

直接测试 ``evaluate_interaction_request`` 的分级拒绝规则，使用 mock session。
不依赖 Redis / LLM / 仿真引擎 / 数据库。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db.models import Agent, AgentState, Relationship
from app.domain.dialogue.interaction_evaluator import evaluate_interaction_request


def _make_agent(agent_id: str, name: str, personality=None) -> Agent:
    a = Agent(
        id=agent_id,
        entity_type="human",
        name=name,
        personality=personality or [],
        background="",
    )
    return a


def _make_state(
    agent_id: str,
    *,
    state: str = "IDLE",
    interruptible: bool = True,
    current_priority: int = 0,
    busy_until: datetime | None = None,
) -> AgentState:
    s = AgentState(
        agent_id=agent_id,
        scene_id="scene1",
        x=0,
        y=0,
        state=state,
    )
    s.interruptible = interruptible
    s.current_priority = current_priority
    s.busy_until = busy_until
    return s


def _make_rel(familiarity: float, fear: float = 0.0) -> Relationship:
    r = Relationship(
        from_agent_id="tgt1",
        to_entity_id="req1",
        familiarity=familiarity,
        fear=fear,
    )
    return r


def _build_session(
    *,
    requester: Agent,
    target: Agent,
    requester_state: AgentState,
    target_state: AgentState,
    relationship: Relationship | None,
):
    """构造一个最小可用的 mock session。

    `evaluate_interaction_request` 只用了：
    - ``await session.get(Agent, id)``
    - ``await session.get(AgentState, id)``
    - ``(await session.execute(stmt)).scalar_one_or_none()`` 用于查 Relationship
    """
    session = MagicMock()

    async def _get(model, pk):
        if model is Agent:
            return {requester.id: requester, target.id: target}.get(pk)
        if model is AgentState:
            return {
                requester.id: requester_state,
                target.id: target_state,
            }.get(pk)
        return None

    session.get = AsyncMock(side_effect=_get)

    async def _execute(_stmt):
        result = MagicMock()
        result.scalar_one_or_none.return_value = relationship
        return result

    session.execute = AsyncMock(side_effect=_execute)
    return session


@pytest.mark.asyncio
async def test_accept_when_target_idle_and_known():
    requester = _make_agent("req1", "小芳")
    target = _make_agent("tgt1", "小王", personality=["内向"])
    rs = _make_state("req1")
    ts = _make_state("tgt1", state="IDLE")
    rel = _make_rel(familiarity=0.5)
    session = _build_session(
        requester=requester, target=target,
        requester_state=rs, target_state=ts, relationship=rel,
    )
    res = await evaluate_interaction_request(
        session, requester_id="req1", target_id="tgt1", kind="chat"
    )
    assert res.decision == "accept"


@pytest.mark.asyncio
async def test_hard_decline_when_target_sleeping():
    requester = _make_agent("req1", "小芳")
    target = _make_agent("tgt1", "小王")
    session = _build_session(
        requester=requester, target=target,
        requester_state=_make_state("req1"),
        target_state=_make_state("tgt1", state="SLEEPING"),
        relationship=_make_rel(0.5),
    )
    res = await evaluate_interaction_request(
        session, requester_id="req1", target_id="tgt1", kind="chat"
    )
    assert res.decision == "hard_decline"
    assert res.reason == "sleeping"


@pytest.mark.asyncio
async def test_hard_decline_when_high_priority_task():
    requester = _make_agent("req1", "小芳")
    target = _make_agent("tgt1", "小王")
    session = _build_session(
        requester=requester, target=target,
        requester_state=_make_state("req1"),
        target_state=_make_state(
            "tgt1", state="WORKING", interruptible=False, current_priority=9
        ),
        relationship=_make_rel(0.5),
    )
    res = await evaluate_interaction_request(
        session, requester_id="req1", target_id="tgt1", kind="chat"
    )
    assert res.decision == "hard_decline"
    assert res.reason == "high_priority_task"


@pytest.mark.asyncio
async def test_hard_decline_when_busy_stranger():
    requester = _make_agent("req1", "小芳")
    target = _make_agent("tgt1", "小王")
    # familiarity 低于阈值 0.2 = 陌生人
    session = _build_session(
        requester=requester, target=target,
        requester_state=_make_state("req1"),
        target_state=_make_state("tgt1", state="WORKING", current_priority=4),
        relationship=_make_rel(0.05),
    )
    res = await evaluate_interaction_request(
        session, requester_id="req1", target_id="tgt1", kind="chat"
    )
    assert res.decision == "hard_decline"
    assert res.reason == "busy_stranger"


@pytest.mark.asyncio
async def test_soft_decline_when_busy_friend():
    requester = _make_agent("req1", "小芳")
    target = _make_agent("tgt1", "小王", personality=["内向"])
    session = _build_session(
        requester=requester, target=target,
        requester_state=_make_state("req1"),
        target_state=_make_state("tgt1", state="WORKING", current_priority=4),
        relationship=_make_rel(0.7),
    )
    res = await evaluate_interaction_request(
        session, requester_id="req1", target_id="tgt1", kind="chat"
    )
    assert res.decision == "soft_decline"
    assert res.npc_line  # 必须有台词
    assert res.reason == "busy_friend"


@pytest.mark.asyncio
async def test_self_interaction_rejected():
    requester = _make_agent("req1", "小芳")
    session = _build_session(
        requester=requester, target=requester,
        requester_state=_make_state("req1"),
        target_state=_make_state("req1"),
        relationship=None,
    )
    res = await evaluate_interaction_request(
        session, requester_id="req1", target_id="req1", kind="chat"
    )
    assert res.decision == "hard_decline"
    assert res.reason == "cannot_self_interact"


@pytest.mark.asyncio
async def test_busy_until_in_future_treated_as_busy():
    """阶段 19：busy_until > now 即使 state==IDLE 也算忙碌。"""
    requester = _make_agent("req1", "小芳")
    target = _make_agent("tgt1", "小王")
    future = datetime.now(timezone.utc) + timedelta(minutes=10)
    session = _build_session(
        requester=requester, target=target,
        requester_state=_make_state("req1"),
        # state=IDLE 但 busy_until 未到 → 仍视为忙碌
        target_state=_make_state("tgt1", state="IDLE", busy_until=future),
        # familiarity 低 → 陌生人 → 应硬拒
        relationship=_make_rel(0.05),
    )
    res = await evaluate_interaction_request(
        session, requester_id="req1", target_id="tgt1", kind="chat"
    )
    assert res.decision == "hard_decline"
    assert res.reason == "busy_stranger"
