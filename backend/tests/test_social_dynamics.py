"""
阶段 19+：社交动态测试。

覆盖：
- ``_evolve_basic_needs``：social_need / hunger / energy 随时间演化。
- ``_scan_social_encounters``：两个空闲 NPC 靠近 + 一方 social_need 高时
  自动配对并通过 InteractionService 派发请求。
- ``decide_with_llm``：消费 ``ToolResult.memory_candidates``，把工具产生的
  候选记忆写入 memories。

这些用例直接构造一个 ``SimulationEngine`` 实例并 stub 掉外部副作用
（session_factory / interaction_service / event_bus），不依赖真实数据库。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.simulation.engine import EngineAgent, SimulationEngine


def _make_engine() -> SimulationEngine:
    factory = MagicMock()
    eng = SimulationEngine(factory)
    # 模拟 simulation 已加载（避免广播路径报错）
    sim = MagicMock()
    sim.id = "sim-test"
    sim.status = "running"
    sim.world_tick_hz = 5.0
    sim.ai_tick_minutes = 5
    sim.speed_multiplier = 1.0
    sim.current_step = 0
    eng._simulation = sim
    return eng


def _agent(
    aid: str,
    *,
    entity_type: str = "human",
    state: str = "IDLE",
    scene: str = "scene1",
    x: int = 0,
    y: int = 0,
    social_need: float = 0.3,
    hunger: float = 0.2,
    energy: float = 1.0,
) -> EngineAgent:
    return EngineAgent(
        id=aid,
        entity_type=entity_type,
        name=aid,
        scene_id=scene,
        x=x,
        y=y,
        state=state,
        social_need=social_need,
        hunger=hunger,
        energy=energy,
        is_player=(entity_type == "player"),
    )


# ---------------------------------------------------------------------------
# _evolve_basic_needs
# ---------------------------------------------------------------------------


def test_basic_needs_grow_over_simulated_minutes():
    eng = _make_engine()
    eng._agents = {
        "a": _agent("a", social_need=0.30, hunger=0.20, energy=1.0),
    }
    t0 = datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc)
    eng._evolve_basic_needs(t0)
    # 第一次调用只校准基线，不应改动状态
    a = eng._agents["a"]
    assert pytest.approx(a.social_need, abs=1e-6) == 0.30

    # 经过 60 仿真分钟
    eng._evolve_basic_needs(t0 + timedelta(minutes=60))
    a = eng._agents["a"]
    assert a.social_need > 0.30
    assert a.hunger > 0.20
    assert a.energy < 1.0


def test_basic_needs_paused_when_sleeping_or_chatting():
    eng = _make_engine()
    eng._agents = {
        "sleep": _agent("sleep", state="SLEEPING", energy=0.4),
        "chat": _agent("chat", state="CHATTING", social_need=0.9),
    }
    t0 = datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc)
    eng._evolve_basic_needs(t0)
    eng._evolve_basic_needs(t0 + timedelta(minutes=120))

    # 睡眠中精力应回升
    assert eng._agents["sleep"].energy > 0.4
    # 对话中社交需求应衰减
    assert eng._agents["chat"].social_need < 0.9


# ---------------------------------------------------------------------------
# _scan_social_encounters
# ---------------------------------------------------------------------------


def _patch_create_task(monkeypatch, captured: list[tuple[str, str]]) -> None:
    """让 asyncio.create_task 立刻执行协程，并把 dispatch 拦截到 captured。"""
    import asyncio

    async def _runner(coro):
        await coro

    def _fake_create_task(coro, **kwargs):
        # 直接调度同步执行：测试已在事件循环内，可用 ensure_future
        return asyncio.ensure_future(_runner(coro))

    monkeypatch.setattr("app.domain.simulation.engine.asyncio", asyncio)
    monkeypatch.setattr(asyncio, "create_task", _fake_create_task)


@pytest.mark.asyncio
async def test_encounter_pairs_idle_npc_with_high_social_need(monkeypatch):
    eng = _make_engine()
    # 两个空闲 NPC 在同一场景内靠得很近，A 的 social_need 已超阈值
    eng._agents = {
        "a": _agent("a", x=10, y=10, social_need=0.80),
        "b": _agent("b", x=12, y=10, social_need=0.30),
    }

    captured: list[tuple[str, str]] = []

    async def _fake_dispatch(pairs: list[tuple[str, str]]) -> None:
        captured.extend(pairs)

    monkeypatch.setattr(eng, "_dispatch_social_encounters", _fake_dispatch)

    t0 = datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc)
    await eng._scan_social_encounters(t0)
    # 等异步派发任务跑完
    import asyncio
    await asyncio.sleep(0)

    assert captured, "should have dispatched at least one encounter pair"
    assert captured[0] in {("a", "b"), ("b", "a")}

    # 二次同时间扫描应被去抖跳过（间隔 < scan_interval）
    captured.clear()
    await eng._scan_social_encounters(t0 + timedelta(seconds=10))
    await asyncio.sleep(0)
    assert captured == []


@pytest.mark.asyncio
async def test_encounter_skipped_when_distance_too_far(monkeypatch):
    eng = _make_engine()
    eng._agents = {
        "a": _agent("a", x=0, y=0, social_need=0.95),
        "b": _agent("b", x=20, y=20, social_need=0.95),
    }
    captured: list[tuple[str, str]] = []

    async def _fake_dispatch(pairs):
        captured.extend(pairs)

    monkeypatch.setattr(eng, "_dispatch_social_encounters", _fake_dispatch)

    t0 = datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc)
    await eng._scan_social_encounters(t0)
    import asyncio
    await asyncio.sleep(0)
    assert captured == []


@pytest.mark.asyncio
async def test_encounter_skipped_when_target_not_idle(monkeypatch):
    eng = _make_engine()
    eng._agents = {
        "a": _agent("a", x=0, y=0, social_need=0.95),
        "b": _agent("b", x=1, y=0, social_need=0.95, state="WORKING"),
    }
    captured: list[tuple[str, str]] = []

    async def _fake_dispatch(pairs):
        captured.extend(pairs)

    monkeypatch.setattr(eng, "_dispatch_social_encounters", _fake_dispatch)

    t0 = datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc)
    await eng._scan_social_encounters(t0)
    import asyncio
    await asyncio.sleep(0)
    assert captured == []


# ---------------------------------------------------------------------------
# agent_decision 消费 memory_candidates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_decision_persists_tool_memory_candidates(monkeypatch):
    """ToolResult.memory_candidates 现在必须被 decide_with_llm 消费写库。"""

    from app.llm.agent_decision import decide_with_llm
    from app.llm.tools.base import ToolResult

    # ── stub LLM 客户端：返回一个调用了 socialize 的决策 ───────────────────
    fake_llm = MagicMock()
    fake_llm.settings = MagicMock()
    fake_llm.settings.llm_is_configured = True
    fake_llm.settings.model_for_role = MagicMock(return_value="qwen-plus")
    fake_llm.chat_json = AsyncMock(
        return_value={
            "thought": "去找邻居聊聊",
            "emotion": "friendly",
            "tool_calls": [
                {"tool": "socialize", "arguments": {}, "confidence": 0.7}
            ],
            "memory_writes": [],
        }
    )
    monkeypatch.setattr(
        "app.llm.agent_decision.get_llm_client", lambda: fake_llm
    )

    # ── stub session.get：返回 Agent + AgentState ──────────────────────────
    from app.db.models import Agent, AgentState

    agent = Agent(
        id="npc1", entity_type="human", name="小明", personality=["开朗"],
    )
    state = AgentState(
        agent_id="npc1", scene_id="scene1", x=0, y=0, state="IDLE",
        energy=0.8, hunger=0.2, social_need=0.7,
    )

    session = MagicMock()
    session.get = AsyncMock(return_value=None)

    async def _get(model, pk):
        if model is Agent and pk == "npc1":
            return agent
        if model is AgentState and pk == "npc1":
            return state
        return None

    session.get = AsyncMock(side_effect=_get)

    # 提供任意 execute（_perceive 用，返回空集合）
    empty_scalar = MagicMock()
    empty_scalar.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=empty_scalar)

    # ── stub perceive / retrieve / planning / prompt / tool_executor ─────
    monkeypatch.setattr(
        "app.llm.agent_decision._perceive",
        AsyncMock(return_value={"text": "", "nearby": []}),
    )
    monkeypatch.setattr(
        "app.llm.agent_decision._retrieve",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.llm.agent_decision._social_block",
        AsyncMock(return_value=""),
    )

    fake_planning = MagicMock()
    fake_planning.get_current_context = AsyncMock(return_value={})
    monkeypatch.setattr(
        "app.llm.agent_decision.get_planning_service", lambda: fake_planning
    )

    fake_registry = MagicMock()
    fake_registry.prompt_catalog = MagicMock(return_value="")
    monkeypatch.setattr(
        "app.llm.agent_decision.get_tool_registry", lambda: fake_registry
    )

    # 让 prompt 渲染走通
    fake_prompt = MagicMock(id="agent_decision", version="v1", model_role="chat")
    monkeypatch.setattr(
        "app.llm.agent_decision.render_prompt",
        lambda name, ctx: ("prompt", fake_prompt),
    )

    # 工具执行返回带 memory_candidates 的 ToolResult
    fake_executor = MagicMock()
    fake_executor.execute_batch = AsyncMock(
        return_value=[
            ToolResult(
                tool="socialize",
                success=True,
                result={"target_entity_id": "npc2"},
                memory_candidates=[
                    {
                        "memory_type": "chat",
                        "scope": "short_term",
                        "description": "我主动去找 npc2 聊了聊",
                        "importance": 4,
                        "keywords": ["npc2", "socialize"],
                    }
                ],
            )
        ]
    )
    monkeypatch.setattr(
        "app.llm.tools.executor.get_tool_executor", lambda: fake_executor
    )
    monkeypatch.setattr(
        "app.llm.agent_decision.get_tool_executor", lambda: fake_executor
    )

    # 抓 memory_service.write 调用
    written: list[dict[str, Any]] = []

    fake_ms = MagicMock()

    async def _write(session, **kwargs):
        written.append(kwargs)
        return MagicMock(id=f"mem-{len(written)}")

    fake_ms.write = AsyncMock(side_effect=_write)
    monkeypatch.setattr(
        "app.services.memory_service.get_memory_service", lambda: fake_ms
    )
    monkeypatch.setattr(
        "app.llm.agent_decision.get_memory_service", lambda: fake_ms
    )

    # ── 跑 ─────────────────────────────────────────────────────────────────
    res = await decide_with_llm(
        session,
        agent_id="npc1",
        simulation_id="sim-test",
        world_time=datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc),
    )
    assert res is not None
    # 关键断言：socialize 的 memory_candidate 被消费写库
    assert any(
        kw.get("description", "").startswith("我主动去找 npc2") for kw in written
    ), f"expected tool memory candidate to be persisted; got writes: {written}"
