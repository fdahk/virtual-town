"""
阶段 20：记忆沉思（rumination）。

每个 NPC 每仿真日 ~12:00 触发一次：抽样 ``importance >= threshold`` 且
``last_accessed_at`` 较旧的长期记忆，让 LLM 重新感悟，产出一条新的 thought
记忆作为"我又想起了…"的衍生品。同时给被回顾的原记忆 ``importance += 1``
并刷新 ``last_accessed_at``，模拟"反复回忆 → 强化"的心理过程。

设计要点
--------
1. 输入只包含 long_term 且重要度高的记忆（importance ≥ ``settings.memory_rumination_importance_threshold``）。
2. 每次最多抽 ``settings.memory_rumination_sample_size`` 条；< 2 条不触发。
3. 新 thought 的 importance 落在 4-6，避免污染长期高优先级带。
4. 原记忆 ``importance`` 上限 10；同时刷新 ``last_accessed_at``，
   配合 ``MemoryService._recency_score`` 的访问强化保持检索权重。
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import Agent, Memory
from app.llm.client import get_llm_client
from app.prompts import render_prompt
from app.services.memory_service import get_memory_service

logger = get_logger(__name__)


class _RuminationOutput(BaseModel):
    thought: str = Field(default="", max_length=400)
    importance: int = Field(default=5, ge=1, le=10)
    evidence_memory_ids: list[str] = Field(default_factory=list)


async def ruminate_important_memories(
    session: AsyncSession,
    agent_id: str,
    *,
    world_time: datetime,
) -> Memory | None:
    """挑出几条该 NPC 的重要长期记忆，让 LLM 重新感悟产生新 thought。"""
    settings = get_settings()
    if not settings.memory_rumination_enabled:
        return None

    agent = await session.get(Agent, agent_id)
    if agent is None:
        return None

    threshold = settings.memory_rumination_importance_threshold
    sample_size = max(2, settings.memory_rumination_sample_size)

    # 抽样：long_term，importance ≥ threshold；优先 last_accessed_at 较旧的
    rows = (
        await session.execute(
            select(Memory)
            .where(
                Memory.agent_id == agent_id,
                Memory.scope == "long_term",
                Memory.importance >= threshold,
                # 排除今天已被访问过的（避免反复沉思同一批）
                (Memory.last_accessed_at.is_(None))
                | (Memory.last_accessed_at < world_time - timedelta(hours=12)),
            )
            .order_by(desc(Memory.importance))
            .limit(sample_size * 3)
        )
    ).scalars().all()
    if len(rows) < 2:
        return None

    # 在 importance Top 里随机取最多 sample_size 条，让多样性更好
    rng = random.Random(int(world_time.timestamp()) ^ hash(agent_id))
    candidates = rng.sample(list(rows), k=min(sample_size, len(rows)))
    candidate_ids = {m.id for m in candidates}

    output = await _generate_rumination(agent, candidates)
    if not output.thought.strip():
        return None

    ms = get_memory_service()
    new_mem = await ms.write(
        session,
        agent_id=agent_id,
        memory_type="thought",
        scope="long_term",
        description=output.thought[:400],
        importance=max(4, min(output.importance, 6)),
        evidence_memory_ids=[mid for mid in output.evidence_memory_ids if mid in candidate_ids][:5]
        or [m.id for m in candidates[:3]],
        keywords=["rumination"],
        commit=False,
    )

    # 强化原记忆：importance +1（cap 10），last_accessed_at = now
    await session.execute(
        update(Memory)
        .where(Memory.id.in_(list(candidate_ids)))
        .values(
            importance=Memory.importance + 1,
            last_accessed_at=world_time,
        )
    )
    # cap 处理：把任何超过 10 的拉回 10
    await session.execute(
        update(Memory)
        .where(Memory.id.in_(list(candidate_ids)), Memory.importance > 10)
        .values(importance=10)
    )
    await session.commit()

    logger.info(
        "ruminate agent=%s candidates=%d new_thought_id=%s",
        agent.name,
        len(candidates),
        new_mem.id,
    )
    return new_mem


async def _generate_rumination(
    agent: Agent, memories: list[Memory]
) -> _RuminationOutput:
    client = get_llm_client()
    if client.settings.llm_is_configured:
        prompt, meta = render_prompt(
            "memory_rumination",
            {
                "profile_block": _profile_block(agent),
                "memory_block": _memory_block(memories),
            },
        )
        data = await client.chat_json(
            system="你必须只输出 JSON。",
            user=prompt,
            model=client.settings.model_for_role(meta.model_role or "reasoning"),
            temperature=0.5,
            max_tokens=400,
            prompt_template_id=meta.id,
            prompt_version=meta.version,
            caller_module="memory.rumination",
        )
        if data is not None:
            try:
                return _RuminationOutput.model_validate(data)
            except ValidationError:
                logger.warning("rumination JSON invalid; fallback to rule")

    return _rule_rumination(agent, memories)


def _rule_rumination(agent: Agent, memories: list[Memory]) -> _RuminationOutput:
    top = sorted(memories, key=lambda m: m.importance, reverse=True)[:2]
    desc = (
        f"我最近又想起了几件让我念念不忘的事："
        + "；".join(m.description[:50] for m in top)
        + "。这些事在我心里留下了痕迹。"
    )
    return _RuminationOutput(
        thought=desc[:240],
        importance=5,
        evidence_memory_ids=[m.id for m in top],
    )


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
        f"- [{m.id} imp={m.importance} {m.memory_type}] {m.description}"
        for m in memories
    )


__all__ = ["ruminate_important_memories"]
