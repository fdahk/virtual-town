"""新游戏 / 模板 / 存档 路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.db.templates import (
    ANIMAL_COLOR_CATALOG,
    DEFAULT_ANIMAL_TEMPLATES,
    DEFAULT_HUMAN_TEMPLATES,
    DEFAULT_PLAYER_TEMPLATE,
    LPC_LAYER_CATALOG,
    OCCUPATION_PRESETS,
    SCHEDULE_TEMPLATES,
)
from app.core.config import get_settings
from app.services.game_service import (
    SAVE_FORMAT_VERSION,
    SaveValidationError,
    create_new_game,
    export_save,
    import_save,
)

router = APIRouter(prefix="/games", tags=["games"])


def _default_req_outdoor_width() -> int:
    return get_settings().world_gen_outdoor_default_width


def _default_req_outdoor_height() -> int:
    return get_settings().world_gen_outdoor_default_height


# ---------------------------------------------------------------------------
# 请求 / 响应模型
# ---------------------------------------------------------------------------


class NewGameRequest(BaseModel):
    seed: int = 42
    outdoor_width: int = Field(default_factory=_default_req_outdoor_width, ge=40, le=200)
    outdoor_height: int = Field(default_factory=_default_req_outdoor_height, ge=30, le=200)
    humans: list[dict[str, Any]] | None = None  # 不传则使用默认 18 人
    animals: list[dict[str, Any]] | None = None
    player: dict[str, Any] | None = None
    auto_start: bool = True


class NewGameResponse(BaseModel):
    simulation_id: str | None
    outdoor_scene_id: str
    agent_count: int
    scene_count: int
    object_count: int
    relationship_count: int


class TemplateListResponse(BaseModel):
    humans: list[dict[str, Any]]
    animals: list[dict[str, Any]]
    player: dict[str, Any]


class ArtCatalogResponse(BaseModel):
    lpc_layers: dict[str, list[dict[str, Any]]]
    animal_colors: dict[str, list[dict[str, Any]]]
    occupation_presets: dict[str, dict[str, Any]]
    schedule_templates: dict[str, list[dict[str, Any]]]


class WorldDefaultsResponse(BaseModel):
    outdoor_width: int
    outdoor_height: int


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


@router.get("/world-defaults", response_model=WorldDefaultsResponse)
async def get_world_defaults() -> WorldDefaultsResponse:
    """新游戏向导用的默认室外地图尺寸（与 ``WORLD_GEN_OUTDOOR_*`` 配置一致）。"""
    s = get_settings()
    return WorldDefaultsResponse(
        outdoor_width=s.world_gen_outdoor_default_width,
        outdoor_height=s.world_gen_outdoor_default_height,
    )


@router.post("/new", response_model=NewGameResponse)
async def post_new_game(
    request: NewGameRequest, session: AsyncSession = Depends(get_session)
) -> NewGameResponse:
    """创建新游戏：清空 DB → 生成新世界 → 重载引擎 → 切到 running。"""
    result = await create_new_game(
        session,
        seed=request.seed,
        outdoor_width=request.outdoor_width,
        outdoor_height=request.outdoor_height,
        humans=request.humans,
        animals=request.animals,
        player=request.player,
        auto_start=request.auto_start,
    )
    return NewGameResponse(**result)


@router.get("/templates/npcs", response_model=TemplateListResponse)
async def get_npc_templates() -> TemplateListResponse:
    """返回 22 默认 NPC + 默认玩家的模板（前端编辑器用作起点）。"""
    return TemplateListResponse(
        humans=[t.to_dict() for t in DEFAULT_HUMAN_TEMPLATES],
        animals=[t.to_dict() for t in DEFAULT_ANIMAL_TEMPLATES],
        player=DEFAULT_PLAYER_TEMPLATE.to_dict(),
    )


@router.get("/templates/art", response_model=ArtCatalogResponse)
async def get_art_catalog() -> ArtCatalogResponse:
    """返回 LPC 层目录 / 动物颜色 / 职业预设 / schedule 模板，
    供 NPCEditor 渲染下拉选项与实时预览。"""
    return ArtCatalogResponse(
        lpc_layers=LPC_LAYER_CATALOG,
        animal_colors=ANIMAL_COLOR_CATALOG,
        occupation_presets=OCCUPATION_PRESETS,
        schedule_templates=SCHEDULE_TEMPLATES,
    )


@router.get("/save")
async def get_save(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """导出当前世界为 JSON 快照（前端可下载存盘）。"""
    return await export_save(session)


class LoadSaveResponse(BaseModel):
    simulation_id: str
    status: str
    current_step: int
    imported_counts: dict[str, int] = Field(default_factory=dict)


@router.post("/load", response_model=LoadSaveResponse)
async def post_load_save(
    payload: dict[str, Any] = Body(...),
    session: AsyncSession = Depends(get_session),
) -> LoadSaveResponse:
    """读档：清空当前 DB → 把 JSON 快照写回 → reload 引擎。

    Body 直接是 ``GET /api/games/save`` 返回的 JSON 对象。

    校验失败返回 400（``SaveValidationError``），其它异常按 FastAPI 默认 500。
    """
    try:
        result = await import_save(session, payload)
    except SaveValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return LoadSaveResponse(**result)


@router.get("/save/format-version")
async def get_save_format_version() -> dict[str, str]:
    """返回当前服务端期望的存档格式版本号。

    前端在读档前可先调用此接口对比版本，给用户更清晰的提示
    （而不是上传完才发现 400）。
    """
    return {"version": SAVE_FORMAT_VERSION}
