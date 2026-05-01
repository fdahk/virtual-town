"""
关系升温与摘要更新（阶段 15.4）。

- ``apply_relationship_delta``：累加一次互动对 familiarity / trust / affection / fear
  的 delta，并把累计绝对值写入 Redis 计数器。
- ``should_update_summary``：当累计 delta 超阈值时，返回 True，调用方应投递
  ``relationship_update`` 任务；成功后清零计数器。

设计意图：即使异步任务队列不可用，关系数值仍会实时生效；
只是 summary 文字暂时落后，下一次阈值触发时再刷新。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis_client import get_redis
from app.db.models import Relationship


RELATIONSHIP_DELTA_THRESHOLD = 1.0


def _counter_key(from_id: str, to_id: str) -> str:
    return f"relationship:{from_id}:{to_id}:delta_accum"


@dataclass
class RelationshipChange:
    familiarity: float = 0.0
    trust: float = 0.0
    affection: float = 0.0
    fear: float = 0.0

    @property
    def magnitude(self) -> float:
        return (
            abs(self.familiarity)
            + abs(self.trust)
            + abs(self.affection)
            + abs(self.fear)
        )


def _clamp(value: float, lo: float = 0.0, hi: float = 10.0) -> float:
    return max(lo, min(hi, value))


async def apply_relationship_delta(
    session: AsyncSession,
    *,
    from_agent_id: str,
    to_entity_id: str,
    change: RelationshipChange,
    flush: bool = True,
) -> Relationship:
    """把一次 delta 累加到关系数值上；同时累计绝对变化量到 Redis。

    - 数值字段范围采用 [0, 10]，affection 允许负（[-1, 10]）的旧语义延续。
      这里统一 clamp 到 [0, 10] 便于前端展示；需要负向可直接减。
    - 返回更新后的 Relationship 行。
    """
    stmt = select(Relationship).where(
        Relationship.from_agent_id == from_agent_id,
        Relationship.to_entity_id == to_entity_id,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        row = Relationship(
            from_agent_id=from_agent_id,
            to_entity_id=to_entity_id,
            familiarity=0.0,
            trust=0.0,
            affection=0.0,
            fear=0.0,
        )
        session.add(row)

    row.familiarity = _clamp(float(row.familiarity) + change.familiarity)
    row.trust = _clamp(float(row.trust) + change.trust)
    row.affection = _clamp(float(row.affection) + change.affection, lo=-5.0)
    row.fear = _clamp(float(row.fear) + change.fear)
    if flush:
        await session.flush()

    # Redis 累计（best-effort）
    if change.magnitude > 0:
        try:
            redis = get_redis()
            client = await redis._ensure_client()  # type: ignore[attr-defined]
            if client is not None:
                await client.incrbyfloat(
                    _counter_key(from_agent_id, to_entity_id),
                    change.magnitude,
                )
                await client.expire(
                    _counter_key(from_agent_id, to_entity_id),
                    60 * 60 * 24,
                )
        except Exception:
            pass
    return row


async def should_update_summary(
    *,
    from_agent_id: str,
    to_entity_id: str,
    threshold: float = RELATIONSHIP_DELTA_THRESHOLD,
) -> bool:
    """读取 Redis 累计 delta，判断是否需要刷新 summary。"""
    try:
        redis = get_redis()
        client = await redis._ensure_client()  # type: ignore[attr-defined]
        if client is None:
            return False
        raw = await client.get(_counter_key(from_agent_id, to_entity_id))
        if raw is None:
            return False
        return float(raw) >= threshold
    except Exception:
        return False


async def reset_summary_counter(
    *,
    from_agent_id: str,
    to_entity_id: str,
) -> None:
    try:
        await get_redis().delete(_counter_key(from_agent_id, to_entity_id))
    except Exception:
        pass


__all__ = [
    "RELATIONSHIP_DELTA_THRESHOLD",
    "RelationshipChange",
    "apply_relationship_delta",
    "reset_summary_counter",
    "should_update_summary",
]
