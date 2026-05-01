"""
反思与每日总结管线。

对齐 `智能NPC与记忆模块实施方案.md` 第 5 节：
- Reflection：重要度累计超过阈值时触发，产出 thought 记忆，并引用证据。
- Daily Summary：游戏内一天结束时触发，产出 summary 记忆。

规则兜底：
- LLM 失败时退化为"摘抄最重要的 2 条事件 + 模板句"的简单 summary，
  保证仿真不因模型不可用而停摆。
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import Agent, Memory
from app.llm.client import get_llm_client
from app.prompts import render_prompt
from app.services.memory_service import get_memory_service

logger = get_logger(__name__)

# 触发阈值：累计重要度
REFLECTION_THRESHOLD = 15

# 每次反思消耗的最近记忆条数
REFLECTION_LOOKBACK = 15


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class ReflectionItem(BaseModel):
    description: str
    importance: int = Field(default=5, ge=1, le=10)
    evidence_memory_ids: list[str] = Field(default_factory=list)


class ReflectionOutput(BaseModel):
    reflections: list[ReflectionItem] = Field(default_factory=list)


class DailySummaryOutput(BaseModel):
    summary: str
    highlights: list[str] = Field(default_factory=list)
    mood: str | None = None


# ---------------------------------------------------------------------------
# Reflection
# ---------------------------------------------------------------------------


async def maybe_reflect(
    session: AsyncSession,
    agent_id: str,
    *,
    world_time: datetime,
    force: bool = False,
) -> list[Memory]:
    """
    如果累计重要度达到阈值，生成反思 thought。返回新写入的 Memory 列表。
    `force=True` 时忽略阈值直接跑一次（供手动触发用）。
    """
    agent = await session.get(Agent, agent_id)
    if agent is None:
        return []

    recent = await _recent_non_reflected(session, agent_id, REFLECTION_LOOKBACK)
    total = sum(m.importance for m in recent)
    if not force and total < REFLECTION_THRESHOLD:
        return []
    if not recent:
        return []

    logger.info(
        "reflect trigger agent=%s total_importance=%d count=%d",
        agent.name,
        total,
        len(recent),
    )

    items = await _generate_reflection(agent, recent)
    written: list[Memory] = []
    ms = get_memory_service()
    for item in items:
        if not item.description.strip():
            continue
        mem = await ms.write(
            session,
            agent_id=agent_id,
            memory_type="thought",
            scope="long_term",
            description=item.description[:400],
            importance=max(3, min(item.importance, 10)),
            evidence_memory_ids=[
                mid for mid in item.evidence_memory_ids if mid in {m.id for m in recent}
            ],
            keywords=[],
            commit=False,
        )
        written.append(mem)
    await session.commit()
    return written


async def _recent_non_reflected(
    session: AsyncSession, agent_id: str, limit: int
) -> list[Memory]:
    rows = (
        await session.execute(
            select(Memory)
            .where(
                Memory.agent_id == agent_id,
                Memory.memory_type.in_(["event", "chat"]),
            )
            .order_by(desc(Memory.created_at))
            .limit(limit)
        )
    ).scalars().all()
    return list(rows)


async def _generate_reflection(
    agent: Agent, memories: list[Memory]
) -> list[ReflectionItem]:
    """LLM 优先，失败走规则兜底。"""
    client = get_llm_client()
    if client.settings.llm_is_configured:
        prompt, meta = render_prompt(
            "reflection",
            {
                "profile_block": _profile_block(agent),
                "memory_block": _memory_block(memories),
            },
        )
        data = await client.chat_json(
            system="你必须只输出 JSON，reflections 数组。",
            user=prompt,
            model=client.settings.model_for_role(meta.model_role or "reasoning"),
            temperature=0.3,
            max_tokens=500,
            prompt_template_id=meta.id,
            prompt_version=meta.version,
            caller_module="memory.reflection",
        )
        if data is not None:
            try:
                return ReflectionOutput.model_validate(data).reflections[:3]
            except ValidationError:
                logger.warning("reflection JSON invalid; fallback to rule")

    return _rule_reflect(agent, memories)


def _rule_reflect(agent: Agent, memories: list[Memory]) -> list[ReflectionItem]:
    """规则兜底：把最重要的 2 条事件合并成一条总结。"""
    if not memories:
        return []
    top = sorted(memories, key=lambda m: m.importance, reverse=True)[:3]
    desc = (
        f"我最近留意到几件值得记住的事：" + "；".join(m.description for m in top[:2])
    )
    return [
        ReflectionItem(
            description=desc[:240],
            importance=min(7, max(m.importance for m in top)),
            evidence_memory_ids=[m.id for m in top],
        )
    ]


# ---------------------------------------------------------------------------
# Daily summary
# ---------------------------------------------------------------------------


async def maybe_daily_summary(
    session: AsyncSession,
    agent_id: str,
    *,
    world_time: datetime,
) -> Memory | None:
    """
    若今天还没有 summary，且已过 23:00（游戏内），生成一条。
    """
    if world_time.time() < time(22, 30):
        return None
    day_start = world_time.replace(hour=0, minute=0, second=0, microsecond=0)
    existing = (
        await session.execute(
            select(Memory)
            .where(
                Memory.agent_id == agent_id,
                Memory.memory_type == "summary",
                Memory.created_at >= day_start,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return None

    agent = await session.get(Agent, agent_id)
    if agent is None:
        return None

    today = (
        await session.execute(
            select(Memory)
            .where(
                Memory.agent_id == agent_id,
                Memory.created_at >= day_start,
                Memory.memory_type.in_(["event", "chat", "thought"]),
            )
            .order_by(Memory.created_at)
        )
    ).scalars().all()
    if not today:
        return None

    summary = await _generate_summary(agent, list(today))
    if not summary.summary.strip():
        return None
    mem = await get_memory_service().write(
        session,
        agent_id=agent_id,
        memory_type="summary",
        scope="long_term",
        description=summary.summary[:400],
        importance=6,
        keywords=summary.highlights[:5],
        commit=True,
    )
    return mem


async def _generate_summary(
    agent: Agent, today_memories: list[Memory]
) -> DailySummaryOutput:
    client = get_llm_client()
    if client.settings.llm_is_configured:
        prompt, meta = render_prompt(
            "daily_summary",
            {
                "profile_block": _profile_block(agent),
                "memory_block": _memory_block(today_memories),
            },
        )
        data = await client.chat_json(
            system="你必须只输出 JSON。",
            user=prompt,
            model=client.settings.model_for_role(meta.model_role or "reasoning"),
            temperature=0.3,
            max_tokens=400,
            prompt_template_id=meta.id,
            prompt_version=meta.version,
            caller_module="memory.daily_summary",
        )
        if data is not None:
            try:
                return DailySummaryOutput.model_validate(data)
            except ValidationError:
                logger.warning("daily summary JSON invalid; fallback")

    return _rule_daily_summary(agent, today_memories)


def _rule_daily_summary(agent: Agent, memories: list[Memory]) -> DailySummaryOutput:
    highlights = sorted(memories, key=lambda m: m.importance, reverse=True)[:3]
    counts = {
        "event": sum(1 for m in memories if m.memory_type == "event"),
        "chat": sum(1 for m in memories if m.memory_type == "chat"),
        "thought": sum(1 for m in memories if m.memory_type == "thought"),
    }
    sentence = (
        f"今天经历了 {counts['event']} 件事、{counts['chat']} 次对话、"
        f"{counts['thought']} 次想法。"
    )
    if highlights:
        sentence += f" 最让我记住的是：{highlights[0].description}"
    return DailySummaryOutput(
        summary=sentence[:240],
        highlights=[h.description[:60] for h in highlights],
        mood=None,
    )


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _profile_block(agent: Agent) -> str:
    parts = [f"- 名字：{agent.name}", f"- 类型：{agent.entity_type}"]
    if agent.occupation:
        parts.append(f"- 职业：{agent.occupation}")
    if agent.personality:
        parts.append(f"- 性格：{', '.join(agent.personality)}")
    if agent.background:
        parts.append(f"- 背景：{agent.background}")
    return "\n".join(parts)


def _memory_block(memories: list[Memory]) -> str:
    return "\n".join(
        f"- [{m.id[:8]} imp={m.importance} {m.memory_type}] {m.description}"
        for m in memories
    )
