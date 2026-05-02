"""记忆 schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class Memory(BaseModel):
    id: str
    agent_id: str
    memory_type: Literal["event", "thought", "chat", "summary"]
    # 阶段 20：scope 集合扩展
    # - working    （30 仿真分钟自动过期，承接低重要度的"鸡毛蒜皮"事件）
    # - short_term （24 仿真小时，业务主入口）
    # - long_term  （永驻；TTL 升级、反思、日结、沉思衍生）
    # - archived   （TTL 过期但未合并；不进默认检索，等 consolidation worker 合并）
    # - consolidated（已被某条 summary 收录，仅作为证据；不进默认检索）
    scope: Literal[
        "working", "short_term", "long_term", "archived", "consolidated"
    ]
    subject: str | None = None
    predicate: str | None = None
    object: str | None = None
    description: str
    importance: int
    # 阶段 15.5：五因素明细（base / emotion / relationship / novelty / danger）
    importance_detail: dict[str, Any] = Field(default_factory=dict)
    emotional_valence: float = 0.0
    keywords: list[str] = Field(default_factory=list)
    evidence_memory_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    last_accessed_at: datetime | None = None
    ttl_expires_at: datetime | None = None
    # 阶段 20：被合并到的 summary memory id；非空即代表"已被合并"
    summarized_into_id: str | None = None

    class Config:
        from_attributes = True


class MemorySearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    limit: int = Field(default=8, ge=1, le=50)
    include_short_term: bool = True
    include_long_term: bool = True


class MemoryScoreDetail(BaseModel):
    relevance: float
    importance: float
    recency: float
    relationship_relevance: float | None = None


class MemorySearchResult(BaseModel):
    memory: Memory
    score: float
    score_detail: MemoryScoreDetail
