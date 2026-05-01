"""
玩家文本处理调试 REST 路由（阶段 16.5）。

- ``POST /dialogue/rewrite-query`` — 只做 query 改写 + 意图识别；
- ``POST /dialogue/retrieve-context`` — 完整上下文召回（含多轮对话 + 记忆）；

与玩家正式对话 ``POST /players/me/talk`` 独立，主要给观测平台 Query Trace
页面复现一次玩家输入的处理链路。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidArguments, TargetNotFound
from app.core.time import utcnow
from app.db.models import Agent
from app.db.session import get_session
from app.domain.dialogue import (
    classify_intent,
    get_conversation_store,
    rewrite_query,
)
from app.schemas.dialogue import (
    ResolvedEntitySchema,
    RetrieveContextRequest,
    RetrieveContextResponse,
    RewriteQueryRequest,
    RewriteQueryResponse,
)
from app.schemas.memory import MemorySearchRequest
from app.services.memory_service import get_memory_service

router = APIRouter(prefix="/dialogue", tags=["dialogue"])


async def _load_pair(
    session: AsyncSession, player_id: str, target_id: str
) -> tuple[Agent, Agent]:
    player = await session.get(Agent, player_id)
    target = await session.get(Agent, target_id)
    if player is None:
        raise TargetNotFound(f"player {player_id} not found")
    if target is None:
        raise TargetNotFound(f"target {target_id} not found")
    if target.entity_type == "player":
        raise InvalidArguments("target cannot be a player")
    return player, target


@router.post("/rewrite-query", response_model=RewriteQueryResponse)
async def rewrite_query_endpoint(
    request: RewriteQueryRequest,
    session: AsyncSession = Depends(get_session),
) -> RewriteQueryResponse:
    player, target = await _load_pair(
        session, request.player_agent_id, request.target_agent_id
    )
    window = []
    if request.conversation_id:
        try:
            window = await get_conversation_store().recent(
                request.conversation_id, limit=4
            )
        except Exception:
            window = []
    rewrite = await rewrite_query(
        session,
        player=player,
        target=target,
        scene_id=request.scene_id,
        raw_text=request.raw_text,
        recent_dialogue=window,
    )
    intent = await classify_intent(
        raw_text=rewrite.rewritten_query or request.raw_text,
        target_entity_type=target.entity_type,
        rewrite_hint=rewrite.intent_hint,
    )
    return RewriteQueryResponse(
        rewritten_query=rewrite.rewritten_query or request.raw_text,
        resolved_entities=[
            ResolvedEntitySchema(
                mention=e.mention,
                entity_id=e.entity_id,
                confidence=e.confidence,
            )
            for e in rewrite.resolved_entities
        ],
        needs_memory_search=rewrite.needs_memory_search,
        intent_hint=rewrite.intent_hint,
        intent=intent.intent,
        sentiment=intent.sentiment,
    )


@router.post("/retrieve-context", response_model=RetrieveContextResponse)
async def retrieve_context_endpoint(
    request: RetrieveContextRequest,
    session: AsyncSession = Depends(get_session),
) -> RetrieveContextResponse:
    player, target = await _load_pair(
        session, request.player_agent_id, request.target_agent_id
    )
    window = []
    if request.conversation_id:
        try:
            window = await get_conversation_store().recent(
                request.conversation_id, limit=request.limit
            )
        except Exception:
            window = []
    rewrite = await rewrite_query(
        session,
        player=player,
        target=target,
        scene_id=request.scene_id,
        raw_text=request.text,
        recent_dialogue=window,
    )
    query = rewrite.rewritten_query or request.text
    memories_out: list[dict] = []
    if rewrite.needs_memory_search:
        results = await get_memory_service().search(
            session,
            target.id,
            MemorySearchRequest(
                query=query, limit=request.limit, include_short_term=True, include_long_term=True
            ),
            now=utcnow(),
        )
        memories_out = [
            {
                "memory_id": r.memory.id,
                "description": r.memory.description,
                "importance": r.memory.importance,
                "score": r.score,
                "score_detail": r.score_detail.model_dump(),
            }
            for r in results
        ]
    intent = await classify_intent(
        raw_text=query,
        target_entity_type=target.entity_type,
        rewrite_hint=rewrite.intent_hint,
    )
    return RetrieveContextResponse(
        conversation_id=request.conversation_id,
        dialogue_window=window,
        rewritten_query=query,
        memories=memories_out,
        resolved_entities=[
            ResolvedEntitySchema(
                mention=e.mention, entity_id=e.entity_id, confidence=e.confidence
            )
            for e in rewrite.resolved_entities
        ],
        intent=intent.intent,
    )


__all__ = ["router"]
