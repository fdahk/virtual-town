"""玩家 REST 路由。单玩家 MVP，不做鉴权；接口仍预留 /me 语义。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.agent import (
    AgentProfile,
    ApproachNpcRequest,
    CreatePlayerRequest,
    EndChatRequest,
    PlayerInteractRequest,
    PlayerInteractResponse,
    PlayerInteractionRequestCreate,
    PlayerInteractionRequestResponse,
    PlayerMoveRequest,
    PlayerMoveResponse,
    PlayerTalkRequest,
    PlayerTalkResponse,
)
from app.services.player_service import get_player_service

router = APIRouter(prefix="/players", tags=["player"])


@router.post("", response_model=AgentProfile)
async def create_player(
    request: CreatePlayerRequest, session: AsyncSession = Depends(get_session)
) -> AgentProfile:
    return await get_player_service().create_player(session, request)


@router.get("/me", response_model=AgentProfile)
async def get_me(session: AsyncSession = Depends(get_session)) -> AgentProfile:
    return await get_player_service().get_current_player(session)


@router.post("/me/move", response_model=PlayerMoveResponse)
async def move_player(
    request: PlayerMoveRequest, session: AsyncSession = Depends(get_session)
) -> PlayerMoveResponse:
    return await get_player_service().move(session, request)


@router.post("/me/interact", response_model=PlayerInteractResponse)
async def player_interact(
    request: PlayerInteractRequest, session: AsyncSession = Depends(get_session)
) -> PlayerInteractResponse:
    return await get_player_service().interact(session, request)


@router.post("/me/talk", response_model=PlayerTalkResponse)
async def player_talk(
    request: PlayerTalkRequest, session: AsyncSession = Depends(get_session)
) -> PlayerTalkResponse:
    return await get_player_service().talk(session, request)


@router.post("/me/approach", response_model=PlayerMoveResponse)
async def approach_npc(
    request: ApproachNpcRequest, session: AsyncSession = Depends(get_session)
) -> PlayerMoveResponse:
    """计算并下发玩家朝目标 NPC 靠近的路径；已在交互半径内时返回 reason='in_range'。"""
    return await get_player_service().approach_npc(session, request)


@router.post("/me/end_chat", response_model=dict)
async def end_chat(
    request: EndChatRequest, session: AsyncSession = Depends(get_session)
) -> dict:
    """玩家关闭对话界面时调用，释放 NPC 的 CHATTING 状态。"""
    return await get_player_service().end_chat(session, request)


# ----- 阶段 19：交互请求 / 同意 / 拒绝协议 -----


@router.post(
    "/me/interaction-requests",
    response_model=PlayerInteractionRequestResponse,
)
async def create_interaction_request(
    request: PlayerInteractionRequestCreate,
    session: AsyncSession = Depends(get_session),
) -> PlayerInteractionRequestResponse:
    """玩家发起 NPC 交互请求；返回同步评估结果（accept / decline）。"""
    return await get_player_service().request_interaction(session, request)


@router.post(
    "/me/interaction-requests/{request_id}/cancel",
    response_model=dict,
)
async def cancel_interaction_request(
    request_id: str, session: AsyncSession = Depends(get_session)
) -> dict:
    """玩家取消尚未处理完的交互请求。"""
    return await get_player_service().cancel_interaction_request(session, request_id)
