"""对话调试 API schema（阶段 16）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class RewriteQueryRequest(BaseModel):
    player_agent_id: str
    target_agent_id: str
    scene_id: str
    raw_text: str = Field(..., min_length=1, max_length=500)
    conversation_id: str | None = None


class ResolvedEntitySchema(BaseModel):
    mention: str = ""
    entity_id: str = ""
    confidence: float = 0.5


class RewriteQueryResponse(BaseModel):
    rewritten_query: str
    resolved_entities: list[ResolvedEntitySchema] = Field(default_factory=list)
    needs_memory_search: bool = False
    intent_hint: str | None = None
    intent: Literal[
        "chat",
        "ask_memory",
        "ask_location",
        "request_action",
        "give_item",
        "trade",
        "comfort",
        "threaten",
        "pet_animal",
    ] | None = None
    sentiment: Literal["positive", "neutral", "negative"] | None = None


class RetrieveContextRequest(BaseModel):
    player_agent_id: str
    target_agent_id: str
    scene_id: str
    text: str = Field(..., min_length=1, max_length=500)
    conversation_id: str | None = None
    limit: int = Field(default=6, ge=1, le=20)


class RetrieveContextResponse(BaseModel):
    conversation_id: str | None = None
    dialogue_window: list[dict[str, Any]] = Field(default_factory=list)
    rewritten_query: str
    memories: list[dict[str, Any]] = Field(default_factory=list)
    resolved_entities: list[ResolvedEntitySchema] = Field(default_factory=list)
    intent: str | None = None


__all__ = [
    "ResolvedEntitySchema",
    "RetrieveContextRequest",
    "RetrieveContextResponse",
    "RewriteQueryRequest",
    "RewriteQueryResponse",
]
