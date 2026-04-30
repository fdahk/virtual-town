"""记忆检索 REST 路由（通用）。"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("/healthz")
async def memory_healthz() -> dict[str, str]:
    return {"status": "ok"}
