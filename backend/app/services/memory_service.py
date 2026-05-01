"""
MemoryService：
- 写入短期 / 长期记忆到 Postgres。
- 使用三因素评分（relevance + importance + recency）检索。
- MVP 使用基于关键字的相关性；当 Embedding 可用时切换到 pgvector 余弦相似度。

关键约束：
- Embedding 失败不能阻塞记忆写入。记忆先持久化，再由后台任务补算 embedding。
- 三因素权重从 `React版AI小镇实施方案.md` 推荐值：
  recency=0.5, importance=3.0, relevance=2.0
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Iterable

from datetime import timedelta

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.redis_client import get_redis, key_short_memory
from app.core.time import utcnow
from app.db.models import Memory
from app.llm.embedding import get_embedding_service
from app.schemas.memory import Memory as MemorySchema
from app.schemas.memory import (
    MemoryScoreDetail,
    MemorySearchRequest,
    MemorySearchResult,
)

SHORT_TERM_TTL_SECONDS = 3600 * 24  # 24 小时（与方案 §5 对齐）

logger = get_logger(__name__)

RECENCY_WEIGHT = 0.5
IMPORTANCE_WEIGHT = 3.0
RELEVANCE_WEIGHT = 2.0

RECENCY_HALF_LIFE_HOURS = 24


def _tokenize(text: str) -> list[str]:
    # 中英混排的极简分词：按非中英数字字符切分，再过滤空串。
    # 对于生产使用可引入 jieba；MVP 已足够从"小王 美式 咖啡"等关键词里命中。
    tokens = re.findall(r"[\w\u4e00-\u9fff]+", text.lower())
    # 对 2-3 字的短词再切片，帮助中文匹配（"小王喜欢" -> 小王/王喜/喜欢）
    extra: list[str] = []
    for t in tokens:
        if all("\u4e00" <= ch <= "\u9fff" for ch in t) and len(t) >= 2:
            for i in range(len(t) - 1):
                extra.append(t[i : i + 2])
    return tokens + extra


def _keyword_relevance(query: str, text: str, keywords: list[str]) -> float:
    q_tokens = set(_tokenize(query))
    if not q_tokens:
        return 0.0
    t_tokens = set(_tokenize(text))
    t_tokens.update(k.lower() for k in keywords)
    if not t_tokens:
        return 0.0
    overlap = q_tokens & t_tokens
    return len(overlap) / math.sqrt(len(q_tokens) * max(len(t_tokens), 1))


def _cosine(a: Iterable[float], b: Iterable[float]) -> float:
    va = list(a)
    vb = list(b)
    if len(va) != len(vb) or not va:
        return 0.0
    dot = sum(x * y for x, y in zip(va, vb))
    na = math.sqrt(sum(x * x for x in va))
    nb = math.sqrt(sum(x * x for x in vb))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _recency_score(created_at: datetime, now: datetime) -> float:
    delta_hours = max((now - created_at).total_seconds() / 3600.0, 0.0)
    return math.exp(-delta_hours / RECENCY_HALF_LIFE_HOURS)


def _importance_score(importance: int) -> float:
    return max(min(importance, 10), 0) / 10.0


class MemoryService:
    async def write(
        self,
        session: AsyncSession,
        *,
        agent_id: str,
        memory_type: str,
        scope: str,
        description: str,
        importance: int = 3,
        importance_detail: dict | None = None,
        subject: str | None = None,
        predicate: str | None = None,
        object_: str | None = None,
        keywords: list[str] | None = None,
        evidence_memory_ids: list[str] | None = None,
        emotional_valence: float = 0.0,
        commit: bool = True,
        defer_embedding: bool = True,
    ) -> Memory:
        now = utcnow()
        ttl_expires_at = None
        if scope == "short_term":
            ttl_expires_at = now + timedelta(seconds=SHORT_TERM_TTL_SECONDS)
        mem = Memory(
            agent_id=agent_id,
            memory_type=memory_type,
            scope=scope,
            subject=subject,
            predicate=predicate,
            object=object_,
            description=description,
            importance=max(1, min(importance, 10)),
            importance_detail=importance_detail or {},
            emotional_valence=emotional_valence,
            keywords=keywords or [],
            evidence_memory_ids=evidence_memory_ids or [],
            created_at=now,
            ttl_expires_at=ttl_expires_at,
        )
        session.add(mem)
        if commit:
            await session.commit()
            await session.refresh(mem)
        else:
            await session.flush()

        # Redis 短期记忆镜像（§13 键空间）：方便 query 不命中 Postgres 时也能召回
        if scope in {"short_term", "working"}:
            try:
                await get_redis().list_push(
                    key_short_memory(agent_id),
                    {
                        "memory_id": mem.id,
                        "description": description,
                        "importance": mem.importance,
                        "memory_type": memory_type,
                        "created_at": now.isoformat(),
                    },
                    max_len=50,
                    ttl_seconds=SHORT_TERM_TTL_SECONDS,
                )
            except Exception:
                pass

        # Embedding：
        # - defer_embedding=True（默认）：投递 ``write_memory_embedding`` 任务，
        #   让 worker 异步补算，避免写路径阻塞在网络（§12 §8）。
        # - 若任务队列不可用（模块未启动），立即 inline 尝试一次。
        if defer_embedding:
            try:
                from app.domain.tasks.queue import get_task_queue

                queue = get_task_queue()
                if queue is not None:
                    # priority=9 → low 队列，让 agent_decision（high 队列 priority≤3）
                    # 始终优先于 embedding 任务被 worker 消费，消除 embedding 积压
                    # 导致决策任务等待的问题（性能优化 §P1）。
                    await queue.enqueue(
                        task_type="write_memory_embedding",
                        payload={"memory_id": mem.id},
                        entity_id=agent_id,
                        idempotency_extra=mem.id,
                        priority=9,
                        max_retries=2,
                        deadline_seconds=120.0,
                    )
                    return mem
            except Exception:
                logger.debug("enqueue embedding task failed", exc_info=True)

        # Fallback inline embedding
        try:
            emb_service = get_embedding_service()
            vector = await emb_service.embed(description)
            if vector is not None:
                mem.embedding = vector
                if commit:
                    await session.commit()
        except Exception as exc:
            logger.warning("embedding failed; memory kept without vector: %s", exc)

        return mem

    async def search(
        self,
        session: AsyncSession,
        agent_id: str,
        request: MemorySearchRequest,
        *,
        now: datetime,
    ) -> list[MemorySearchResult]:
        scopes: list[str] = []
        if request.include_short_term:
            scopes.extend(["working", "short_term"])
        if request.include_long_term:
            scopes.append("long_term")
        if not scopes:
            return []

        stmt = (
            select(Memory)
            .where(Memory.agent_id == agent_id, Memory.scope.in_(scopes))
            .order_by(desc(Memory.created_at))
            .limit(max(request.limit * 4, 32))
        )
        rows: list[Memory] = list((await session.execute(stmt)).scalars().all())
        if not rows:
            return []

        # 尝试获取 query 的 embedding
        query_vec: list[float] | None = None
        try:
            query_vec = await get_embedding_service().embed(request.query)
        except Exception as exc:
            logger.debug("query embed failed: %s", exc)

        scored: list[MemorySearchResult] = []
        for mem in rows:
            importance = _importance_score(mem.importance)
            recency = _recency_score(mem.created_at, now)
            if query_vec is not None and mem.embedding is not None:
                relevance = _cosine(query_vec, list(mem.embedding))
            else:
                relevance = _keyword_relevance(request.query, mem.description, mem.keywords or [])
            score = (
                RECENCY_WEIGHT * recency
                + IMPORTANCE_WEIGHT * importance
                + RELEVANCE_WEIGHT * relevance
            )
            scored.append(
                MemorySearchResult(
                    memory=MemorySchema.model_validate(mem),
                    score=score,
                    score_detail=MemoryScoreDetail(
                        relevance=relevance,
                        importance=importance,
                        recency=recency,
                    ),
                )
            )

        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[: request.limit]


_memory_service: MemoryService | None = None


def get_memory_service() -> MemoryService:
    global _memory_service
    if _memory_service is None:
        _memory_service = MemoryService()
    return _memory_service
