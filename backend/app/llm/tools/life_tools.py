"""
阶段 19 PR4：生活类工具。

让 NPC 决策不再只是 move + talk，而是真实"过日子"：

- ``work_at_location``：上班 / 学习 / 营业。期间 ``interruptible=False``、
  ``current_priority=6``，他人请求会被硬拒。
- ``have_meal``：吃饭。降低 hunger，提高 energy / emotion。
- ``rest_at``：休息恢复 energy。
- ``browse_shop``：闲逛商铺，可能产生 trade 邀请的记忆候选。
- ``observe_environment``：写一条带 emotion 的"我看到了..."记忆。

所有工具都通过 ``EngineAgent`` 修改运行态（state / busy_until / current_priority），
持久化由仿真主循环统一处理。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Iterable

from pydantic import BaseModel, Field, ValidationError

from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import Location
from app.llm.tools.base import Tool, ToolCall, ToolContext, ToolResult, ToolSpec

logger = get_logger(__name__)


# 真实秒粗算：1 仿真分钟跨越多长取决于 ``world_tick_hz×speed``（默认 2Hz×1 ⇒ 约 0.5 真实秒/仿真分）。
# 为简化估算，busy_until 直接用游戏时间（world_time）+ minutes 设置。


def _set_busy(
    ctx: ToolContext,
    *,
    state: str,
    minutes: int,
    priority: int,
    interruptible: bool,
    goal: str,
) -> None:
    """更新 EngineAgent 的忙碌字段。worker 进程中引擎可能未起，做容错。"""
    try:
        from app.services.simulation_runtime import get_simulation_runtime

        engine = get_simulation_runtime().engine
        agent = engine.get_agent(ctx.agent_id)
        if agent is None:
            return
        agent.state = state
        agent.current_goal = goal
        agent.path = []
        agent.dirty = True
        agent.busy_until = ctx.world_time + timedelta(minutes=max(1, minutes))
        agent.current_priority = max(0, min(10, priority))
        agent.interruptible = interruptible
    except Exception:
        logger.debug("set_busy failed; engine may be down", exc_info=True)


# ---------------------------------------------------------------------------
# work_at_location
# ---------------------------------------------------------------------------


class _WorkArgs(BaseModel):
    location_id: str
    duration_minutes: int = Field(default=60, ge=5, le=240)
    activity: str = Field(default="工作", max_length=64)


class WorkAtLocationTool(Tool):
    name = "work_at_location"

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="前往指定地点专注工作 / 学习 / 营业，期间不接受打扰。",
            owner_module="life",
            allowed_entity_types=["human"],
            parameters_schema={
                "type": "object",
                "properties": {
                    "location_id": {"type": "string"},
                    "duration_minutes": {
                        "type": "integer",
                        "minimum": 5,
                        "maximum": 240,
                    },
                    "activity": {"type": "string", "description": "活动描述"},
                },
                "required": ["location_id"],
            },
        )

    async def execute(self, ctx: ToolContext, call: ToolCall) -> ToolResult:
        try:
            args = _WorkArgs.model_validate(call.arguments)
        except ValidationError as e:
            return self.fail(call, "INVALID_ARGUMENTS", str(e))
        loc = await ctx.session.get(Location, args.location_id)
        if loc is None:
            return self.fail(call, "TARGET_NOT_FOUND", "location missing")
        _set_busy(
            ctx,
            state="WORKING",
            minutes=args.duration_minutes,
            priority=6,
            interruptible=False,
            goal=f"{args.activity} @ {loc.name}",
        )
        return ToolResult(
            tool=call.tool,
            success=True,
            result={
                "location_id": args.location_id,
                "duration_minutes": args.duration_minutes,
                "activity": args.activity,
            },
            memory_candidates=[
                {
                    "memory_type": "event",
                    "scope": "short_term",
                    "description": f"在 {loc.name} 专心{args.activity} {args.duration_minutes} 分钟",
                    "importance": 3,
                    "keywords": [loc.name, args.activity],
                }
            ],
        )


# ---------------------------------------------------------------------------
# have_meal
# ---------------------------------------------------------------------------


class _MealArgs(BaseModel):
    food_object_id: str | None = None
    duration_minutes: int = Field(default=20, ge=5, le=60)
    description: str = Field(default="吃饭", max_length=64)


class HaveMealTool(Tool):
    name = "have_meal"

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="吃饭，降低饥饿、改善心情。可选 food_object_id 指定食物。",
            owner_module="life",
            allowed_entity_types=["human"],
            parameters_schema={
                "type": "object",
                "properties": {
                    "food_object_id": {"type": "string"},
                    "duration_minutes": {
                        "type": "integer",
                        "minimum": 5,
                        "maximum": 60,
                    },
                    "description": {"type": "string"},
                },
            },
        )

    async def execute(self, ctx: ToolContext, call: ToolCall) -> ToolResult:
        try:
            args = _MealArgs.model_validate(call.arguments)
        except ValidationError as e:
            return self.fail(call, "INVALID_ARGUMENTS", str(e))
        # 修改属性：饥饿下降、精力小幅上升、心情提升
        try:
            from app.services.simulation_runtime import get_simulation_runtime

            engine = get_simulation_runtime().engine
            agent = engine.get_agent(ctx.agent_id)
            if agent is not None:
                agent.hunger = max(0.0, agent.hunger - 0.4)
                agent.energy = min(1.0, agent.energy + 0.1)
                if not agent.emotion or agent.emotion in {"hungry", "tired"}:
                    agent.emotion = "content"
                agent.dirty = True
        except Exception:
            logger.debug("have_meal engine update failed", exc_info=True)

        _set_busy(
            ctx,
            state="EATING",
            minutes=args.duration_minutes,
            priority=4,
            interruptible=True,
            goal=args.description,
        )
        return ToolResult(
            tool=call.tool,
            success=True,
            result={"duration_minutes": args.duration_minutes},
            memory_candidates=[
                {
                    "memory_type": "event",
                    "scope": "short_term",
                    "description": f"我{args.description}{args.duration_minutes} 分钟",
                    "importance": 2,
                    "keywords": ["meal", args.description],
                }
            ],
        )


# ---------------------------------------------------------------------------
# rest_at
# ---------------------------------------------------------------------------


class _RestArgs(BaseModel):
    location_id: str | None = None
    duration_minutes: int = Field(default=30, ge=5, le=180)
    reason: str = Field(default="休息", max_length=64)


class RestAtTool(Tool):
    name = "rest_at"

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="原地或在指定地点休息恢复精力。",
            owner_module="life",
            allowed_entity_types=["human"],
            parameters_schema={
                "type": "object",
                "properties": {
                    "location_id": {"type": "string"},
                    "duration_minutes": {
                        "type": "integer",
                        "minimum": 5,
                        "maximum": 180,
                    },
                    "reason": {"type": "string"},
                },
            },
        )

    async def execute(self, ctx: ToolContext, call: ToolCall) -> ToolResult:
        try:
            args = _RestArgs.model_validate(call.arguments)
        except ValidationError as e:
            return self.fail(call, "INVALID_ARGUMENTS", str(e))
        try:
            from app.services.simulation_runtime import get_simulation_runtime

            engine = get_simulation_runtime().engine
            agent = engine.get_agent(ctx.agent_id)
            if agent is not None:
                agent.energy = min(1.0, agent.energy + 0.25)
                if agent.emotion == "tired":
                    agent.emotion = "calm"
                agent.dirty = True
        except Exception:
            logger.debug("rest engine update failed", exc_info=True)
        _set_busy(
            ctx,
            state="RESTING",
            minutes=args.duration_minutes,
            priority=3,
            interruptible=True,
            goal=args.reason,
        )
        return ToolResult(
            tool=call.tool,
            success=True,
            result={"duration_minutes": args.duration_minutes},
            memory_candidates=[
                {
                    "memory_type": "event",
                    "scope": "short_term",
                    "description": f"我{args.reason}{args.duration_minutes} 分钟",
                    "importance": 2,
                    "keywords": ["rest"],
                }
            ],
        )


# ---------------------------------------------------------------------------
# browse_shop
# ---------------------------------------------------------------------------


class _BrowseArgs(BaseModel):
    location_id: str
    interest: str = Field(default="", max_length=64)


class BrowseShopTool(Tool):
    name = "browse_shop"

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="在商铺 / 集市闲逛，可能引发 trade 邀请。",
            owner_module="life",
            allowed_entity_types=["human"],
            parameters_schema={
                "type": "object",
                "properties": {
                    "location_id": {"type": "string"},
                    "interest": {"type": "string"},
                },
                "required": ["location_id"],
            },
        )

    async def execute(self, ctx: ToolContext, call: ToolCall) -> ToolResult:
        try:
            args = _BrowseArgs.model_validate(call.arguments)
        except ValidationError as e:
            return self.fail(call, "INVALID_ARGUMENTS", str(e))
        loc = await ctx.session.get(Location, args.location_id)
        if loc is None:
            return self.fail(call, "TARGET_NOT_FOUND", "location missing")
        _set_busy(
            ctx,
            state="INTERACTING",
            minutes=15,
            priority=2,
            interruptible=True,
            goal=f"在 {loc.name} 闲逛{(' 关注 ' + args.interest) if args.interest else ''}",
        )
        return ToolResult(
            tool=call.tool,
            success=True,
            result={"location_id": args.location_id, "interest": args.interest},
            memory_candidates=[
                {
                    "memory_type": "event",
                    "scope": "short_term",
                    "description": (
                        f"在 {loc.name} 逛了一圈"
                        + (f"，对 {args.interest} 有兴趣" if args.interest else "")
                    ),
                    "importance": 2,
                    "keywords": [loc.name, "browse"],
                }
            ],
        )


# ---------------------------------------------------------------------------
# observe_environment
# ---------------------------------------------------------------------------


class _ObserveArgs(BaseModel):
    description: str = Field(default="", max_length=200)
    emotion: str = Field(default="curious", max_length=32)


class ObserveEnvironmentTool(Tool):
    name = "observe_environment"

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="留意周围发生的事，写一条带情绪的小记忆。",
            owner_module="life",
            allowed_entity_types=["human", "animal"],
            parameters_schema={
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "emotion": {"type": "string"},
                },
                "required": ["description"],
            },
        )

    async def execute(self, ctx: ToolContext, call: ToolCall) -> ToolResult:
        try:
            args = _ObserveArgs.model_validate(call.arguments)
        except ValidationError as e:
            return self.fail(call, "INVALID_ARGUMENTS", str(e))
        if not args.description.strip():
            return self.fail(call, "INVALID_ARGUMENTS", "description required")
        try:
            from app.services.simulation_runtime import get_simulation_runtime

            engine = get_simulation_runtime().engine
            agent = engine.get_agent(ctx.agent_id)
            if agent is not None and args.emotion:
                agent.emotion = args.emotion
                agent.dirty = True
        except Exception:
            logger.debug("observe engine update failed", exc_info=True)
        return ToolResult(
            tool=call.tool,
            success=True,
            result={"emotion": args.emotion},
            memory_candidates=[
                {
                    "memory_type": "thought",
                    "scope": "short_term",
                    "description": f"我看到了：{args.description}",
                    "importance": 2,
                    "keywords": [args.emotion, "observe"],
                }
            ],
        )


def build_tools() -> Iterable[Tool]:
    return [
        WorkAtLocationTool(),
        HaveMealTool(),
        RestAtTool(),
        BrowseShopTool(),
        ObserveEnvironmentTool(),
    ]
