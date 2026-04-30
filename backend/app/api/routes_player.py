"""玩家 REST 路由。单玩家 MVP，不做鉴权；接口仍预留 /me 语义。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.agent import (
    AgentProfile,
    CreatePlayerRequest,
    PlayerInteractRequest,
    PlayerInteractResponse,
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
