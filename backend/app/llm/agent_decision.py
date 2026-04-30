"""
LLM 驱动的 Agent 决策管线。

实现 `智能NPC与记忆模块实施方案.md` 的 perceive → retrieve → plan → execute 流程：

1. perceive：从引擎内存读取 Agent 自身 + 附近实体 + 附近地点 + 当前 hazard。
2. retrieve：MemoryService.search 取最相关的 k 条记忆。
3. plan：把角色档案、状态、感知、记忆、可用工具 catalog 拼进 prompt，调 LLM chat_json。
4. execute：ToolExecutor 依次执行 LLM 返回的 tool_calls，任何失败都不会打断仿真。

未配置 LLM 或调用失败时返回 None，由引擎 fallback 到规则版 rule_agent。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Agent, AgentState, Location
from app.llm.client import get_llm_client
from app.llm.tools.base import ToolCall, ToolContext, ToolResult
from app.llm.tools.executor import get_tool_executor
from app.llm.tools.registry import get_tool_registry
from app.prompts import load as load_prompt
from app.schemas.memory import MemorySearchRequest
from app.services.memory_service import get_memory_service

logger = get_logger(__name__)


class _LLMToolCall(BaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.5
    thought: str | None = None


class _MemoryWrite(BaseModel):
    memory_type: str = "thought"
    description: str
    importance: int = 3


class AgentDecisionOutput(BaseModel):
    thought: str = ""
    emotion: str | None = None
    tool_calls: list[_LLMToolCall] = Field(default_factory=list)
    memory_writes: list[_MemoryWrite] = Field(default_factory=list)


async def decide_with_llm(
    session: AsyncSession,
    *,
    agent_id: str,
    simulation_id: str,
    world_time: datetime,
) -> list[ToolResult] | None:
    """
    用 LLM 驱动决策。返回 ToolResult 列表。
    若未接入 LLM 或失败返回 None，调用方应走规则兜底。
    """
    client = get_llm_client()
    if not client.settings.llm_is_configured:
        return None

    agent = await session.get(Agent, agent_id)
    state = await session.get(AgentState, agent_id)
    if agent is None or state is None:
        return None

    perception = await _perceive(session, agent, state)
    memories = await _retrieve(session, agent_id, perception, world_time)

    registry = get_tool_registry()
    tool_catalog = registry.prompt_catalog(entity_type=agent.entity_type)

    prompt = _fill_prompt(
        load_prompt("agent_decision_v1"),
        {
            "profile_block": _profile_block(agent),
            "state_block": _state_block(state, world_time),
            "perception_block": perception["text"],
            "memory_block": _memory_block(memories),
            "tool_catalog": tool_catalog,
        },
    )

    data = await client.chat_json(
        system=(
            "你必须严格按要求只输出 JSON，不加任何额外文字。"
            "调用的 tool 必须来自给定目录，arguments 字段必须匹配描述。"
        ),
        user=prompt,
        temperature=0.4,
        max_tokens=700,
    )
    if data is None:
        return None
    try:
        plan = AgentDecisionOutput.model_validate(data)
    except ValidationError as exc:
        logger.warning("agent_decision validation failed: %s", exc)
        return None

    executor = get_tool_executor()
    ctx = ToolContext(
        session=session,
        agent_id=agent_id,
        entity_type=agent.entity_type,
        scene_id=state.scene_id,
        position=(state.x, state.y),
        simulation_id=simulation_id,
        world_time=world_time,
        source="llm",
    )
    calls = [
        ToolCall(
            tool=tc.tool,
            arguments=tc.arguments,
            confidence=tc.confidence,
            thought=tc.thought,
        )
        for tc in plan.tool_calls[:3]
    ]
    results = await executor.execute_batch(ctx, calls)

    # 写 LLM 自己提议的 memory_writes
    ms = get_memory_service()
    for mw in plan.memory_writes[:4]:
        try:
            await ms.write(
                session,
                agent_id=agent_id,
                memory_type=mw.memory_type if mw.memory_type in {"event", "thought", "chat", "summary"} else "thought",
                scope="short_term",
                description=mw.description[:400],
                importance=max(1, min(mw.importance, 10)),
                commit=False,
            )
        except Exception:
            logger.exception("memory write from LLM failed")

    # 把情绪也同步到引擎
    if plan.emotion:
        from app.services.simulation_runtime import get_simulation_runtime

        engine_agent = get_simulation_runtime().engine.get_agent(agent_id)
        if engine_agent is not None:
            engine_agent.emotion = plan.emotion
            engine_agent.dirty = True

    return results


# ---------------------------------------------------------------------------
# 感知 & 检索
# ---------------------------------------------------------------------------


async def _perceive(
    session: AsyncSession, agent: Agent, state: AgentState
) -> dict[str, Any]:
    """只读取数据库层的场景 & 附近实体，非侵入。"""
    nearby_entities: list[tuple[str, int, int, str]] = []
    rows = (
        await session.execute(
            select(AgentState).where(
                AgentState.scene_id == state.scene_id,
                AgentState.agent_id != agent.id,
            )
        )
    ).scalars().all()
    for r in rows:
        dist = abs(r.x - state.x) + abs(r.y - state.y)
        if dist > 10:
            continue
        other = await session.get(Agent, r.agent_id)
        if other is None:
            continue
        nearby_entities.append((other.name, r.x, r.y, other.entity_type))
    nearby_entities.sort(key=lambda t: abs(t[1] - state.x) + abs(t[2] - state.y))

    # 当前场景的地点集
    locs = (
        await session.execute(
            select(Location).where(Location.scene_id == state.scene_id)
        )
    ).scalars().all()
    loc_items = [f"{l.id}: {l.name}" for l in locs[:16]]

    text_lines: list[str] = []
    text_lines.append(f"- 你当前位置 scene={state.scene_id} (x={state.x}, y={state.y}), 状态={state.state}")
    if state.status_effects:
        text_lines.append(f"- 身上有效果：{', '.join(state.status_effects)}")
    if nearby_entities:
        text_lines.append("- 附近实体：")
        for name, x, y, et in nearby_entities[:6]:
            text_lines.append(f"    {name}({et}) @ ({x},{y})")
    else:
        text_lines.append("- 周围没有人。")
    if loc_items:
        text_lines.append("- 当前场景的地点 id 供你选择（示例 id 开头）：")
        text_lines.append("    " + "; ".join(loc_items))

    return {"text": "\n".join(text_lines), "nearby": nearby_entities}


async def _retrieve(
    session: AsyncSession, agent_id: str, perception: dict[str, Any], world_time: datetime
) -> list[dict[str, Any]]:
    query = " ".join([p[0] for p in perception.get("nearby", [])]) or "今天要做什么"
    results = await get_memory_service().search(
        session,
        agent_id,
        MemorySearchRequest(query=query, limit=6),
        now=world_time,
    )
    return [
        {
            "id": r.memory.id,
            "description": r.memory.description,
            "importance": r.memory.importance,
            "score": r.score,
        }
        for r in results
    ]


# ---------------------------------------------------------------------------
# 格式化
# ---------------------------------------------------------------------------


def _profile_block(agent: Agent) -> str:
    lines = [f"- 名字：{agent.name}", f"- 类型：{agent.entity_type}"]
    if agent.species:
        lines.append(f"- 物种：{agent.species}")
    if agent.occupation:
        lines.append(f"- 职业：{agent.occupation}")
    if agent.personality:
        lines.append(f"- 性格：{', '.join(agent.personality)}")
    if agent.background:
        lines.append(f"- 背景：{agent.background}")
    if agent.lifestyle:
        lines.append(f"- 生活习惯：{agent.lifestyle}")
    if agent.long_term_goals:
        lines.append(f"- 长期目标：{' / '.join(agent.long_term_goals)}")
    return "\n".join(lines)


def _state_block(state: AgentState, world_time: datetime) -> str:
    return (
        f"- 游戏时间：{world_time.strftime('%Y-%m-%d %H:%M')}\n"
        f"- 精力：{state.energy:.2f} 饥饿：{state.hunger:.2f} 社交：{state.social_need:.2f}\n"
        f"- 情绪：{state.emotion or '平静'}"
    )


def _memory_block(memories: list[dict[str, Any]]) -> str:
    if not memories:
        return "- 暂无相关记忆"
    return "\n".join(
        f"- [{m['id'][:8]} imp={m['importance']}] {m['description']}"
        for m in memories
    )


def _fill_prompt(template: str, values: dict[str, str]) -> str:
    out = template
    for k, v in values.items():
        out = out.replace("{{" + k + "}}", v)
    return out
