"""REST 路由聚合。"""

from fastapi import APIRouter

from app.api.routes_agents import router as agents_router
from app.api.routes_memory import router as memory_router
from app.api.routes_player import router as player_router
from app.api.routes_simulation import router as simulation_router
from app.api.routes_world import router as world_router

api_router = APIRouter()
api_router.include_router(world_router)
api_router.include_router(agents_router)
api_router.include_router(player_router)
api_router.include_router(simulation_router)
api_router.include_router(memory_router)
