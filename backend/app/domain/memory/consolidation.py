"""
阶段 20：记忆合并（consolidation）。

每仿真日 22:00 左右触发：把过去 N 仿真天内 ``scope='archived'`` 且尚未被合并
（``summarized_into_id IS NULL``）的低重要度记忆，按主题分桶，每个桶 ≥3 条
就调用 LLM 提炼成一条 ``memory_type='summary'`` 的长期记忆，并把原文 scope 改
为 ``consolidated``，``summarized_into_id`` 指向新 summary。

设计要点
--------
1. **不删原文**：archived 记忆永远在 DB 里；只是默认检索不进 archived/consolidated。
2. **去重锚点**：``summarized_into_id`` IS NULL 才参与合并（防止反复合并）。
3. **降级策略**：LLM 不可用时走规则兜底——前 3 条 description 截断 + 主题前缀。
4. **importance 上限**：summary 的重要度 ≤ 5（避免低价值合并污染 long_term 检索）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import Agent, Memory
from app.llm.client import get_llm_client
from app.prompts import render_prompt
from app.services.memory_service import get_memory_service

logger = get_logger(__name__)


class _ConsolidationOutput(BaseModel):
    summary: str = Field(default="", max_length=500)
    importance: int = Field(default=4, ge=1, le=10)
    keywords: list[str] = Field(default_factory=list)


async def consolidate_archived_memories(
    session: AsyncSession,
    agent_id: str,
    *,
    world_time: datetime,
) -> list[Memory]:
    """合并某个 agent 过去 N 仿真天的 archived 记忆。

    返回新写入的 summary 记忆列表。
    """
    settings = get_settings()
    if not settings.memory_consolidation_enabled:
        return []

    agent = await session.get(Agent, agent_id)
    if agent is None:
        return []

    cutoff = world_time - timedelta(days=settings.memory_consolidation_lookback_days)
    rows = (
        await session.execute(
            select(Memory)
            .where(
                Memory.agent_id == agent_id,
                Memory.scope == "archived",
                Memory.summarized_into_id.is_(None),
                Memory.created_at >= cutoff,
            )
            .order_by(Memory.created_at)
        )
    ).scalars().all()
    if len(rows) < settings.memory_consolidation_min_bucket_size:
        return []

    buckets = _bucket_by_theme(list(rows))
    written: list[Memory] = []
    ms = get_memory_service()

    for theme, members in buckets.items():
        if len(members) < settings.memory_consolidation_min_bucket_size:
            continue
        try:
            output = await _generate_summary(agent, theme, members)
        except Exception:
            logger.exception("consolidation summary generation crashed")
            output = _rule_summary(theme, members)
        if not output.summary.strip():
            continue
        cap_importance = min(
            5, max(1, max((m.importance for m in members), default=3) + 1, output.importance)
        )
        summary_mem = await ms.write(
            session,
            agent_id=agent_id,
            memory_type="summary",
            scope="long_term",
            description=output.summary[:400],
            importance=cap_importance,
            keywords=([theme] + (output.keywords or []))[:6],
            evidence_memory_ids=[m.id for m in members][:20],
            commit=False,
        )
        await session.flush()
        # 回填原文：scope='consolidated'，summarized_into_id 指向 summary
        member_ids = [m.id for m in members]
        await session.execute(
            update(Memory)
            .where(Memory.id.in_(member_ids))
            .values(scope="consolidated", summarized_into_id=summary_mem.id)
        )
        written.append(summary_mem)

    if written:
        await session.commit()
    logger.info(
        "consolidate agent=%s buckets=%d summaries=%d archived_scanned=%d",
        agent.name,
        len(buckets),
        len(written),
        len(rows),
    )
    return written


def _bucket_by_theme(rows: list[Memory]) -> dict[str, list[Memory]]:
    """按主题分桶。

    主题来源（优先级递减）：
    1. ``keywords[0]`` —— 工具/投影器写入时通常会塞进去。
    2. ``subject`` —— SPO 主语。
    3. ``memory_type`` —— 兜底，避免无主题。
    """
    buckets: dict[str, list[Memory]] = {}
    for mem in rows:
        if mem.keywords:
            theme = str(mem.keywords[0])[:32].strip().lower()
        elif mem.subject:
            theme = str(mem.subject)[:32].strip().lower()
        else:
            theme = mem.memory_type or "misc"
        if not theme:
            theme = "misc"
        buckets.setdefault(theme, []).append(mem)
    return buckets


async def _generate_summary(
    agent: Agent, theme: str, members: list[Memory]
) -> _ConsolidationOutput:
    client = get_llm_client()
    if client.settings.llm_is_configured:
        prompt, meta = render_prompt(
            "memory_consolidation",
            {
                "theme": theme,
                "memory_block": _memory_block(members),
            },
        )
        data = await client.chat_json(
            system="你必须只输出 JSON。",
            user=prompt,
            model=client.settings.model_for_role(meta.model_role or "reasoning"),
            temperature=0.3,
            max_tokens=300,
            prompt_template_id=meta.id,
            prompt_version=meta.version,
            caller_module="memory.consolidation",
        )
        if data is not None:
            try:
                return _ConsolidationOutput.model_validate(data)
            except ValidationError:
                logger.warning("consolidation JSON invalid; fallback to rule")

    return _rule_summary(theme, members)


def _rule_summary(theme: str, members: list[Memory]) -> _ConsolidationOutput:
    top = sorted(members, key=lambda m: m.importance, reverse=True)[:3]
    desc = (
        f"过去这阵子的「{theme}」相关琐事："
        + "；".join(m.description[:40] for m in top)
    )
    return _ConsolidationOutput(
        summary=desc[:240],
        importance=min(4, max((m.importance for m in members), default=3)),
        keywords=[theme],
    )


def _memory_block(memories: list[Memory]) -> str:
    return "\n".join(
        f"- [{m.id[:8]} imp={m.importance} {m.memory_type}] {m.description}"
        for m in memories
    )


__all__ = ["consolidate_archived_memories"]
