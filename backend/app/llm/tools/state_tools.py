"""状态类工具：update_emotion / wait。"""

from __future__ import annotations

from typing import Iterable

from pydantic import BaseModel, Field, ValidationError

from app.llm.tools.base import Tool, ToolCall, ToolContext, ToolResult, ToolSpec


class _EmotionArgs(BaseModel):
    emotion: str = Field(..., max_length=32)
    reason: str = ""
    duration_minutes: int = Field(default=30, ge=1, le=360)


class UpdateEmotionTool(Tool):
    name = "update_emotion"

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="更新自身的当前情绪标签。",
            owner_module="agent",
            allowed_entity_types=["human", "animal", "player"],
            parameters_schema={
                "type": "object",
                "properties": {
                    "emotion": {"type": "string"},
                    "reason": {"type": "string"},
                    "duration_minutes": {"type": "integer"},
                },
                "required": ["emotion"],
            },
        )

    async def execute(self, ctx: ToolContext, call: ToolCall) -> ToolResult:
        try:
            args = _EmotionArgs.model_validate(call.arguments)
        except ValidationError as e:
            return self.fail(call, "INVALID_ARGUMENTS", str(e))

        from app.services.simulation_runtime import get_simulation_runtime

        agent = get_simulation_runtime().engine.get_agent(ctx.agent_id)
        if agent is not None:
            agent.emotion = args.emotion
            agent.dirty = True
        return self.ok(call, {"emotion": args.emotion})


class _WaitArgs(BaseModel):
    duration_minutes: int = Field(default=5, ge=1, le=120)
    reason: str = ""


class WaitTool(Tool):
    name = "wait"

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="原地等待（什么也不做），适用于等人、等食物等。",
            owner_module="simulation",
            allowed_entity_types=["human", "animal", "player"],
            parameters_schema={
                "type": "object",
                "properties": {
                    "duration_minutes": {"type": "integer"},
                    "reason": {"type": "string"},
                },
            },
        )

    async def execute(self, ctx: ToolContext, call: ToolCall) -> ToolResult:
        try:
            args = _WaitArgs.model_validate(call.arguments)
        except ValidationError as e:
            return self.fail(call, "INVALID_ARGUMENTS", str(e))

        from app.services.simulation_runtime import get_simulation_runtime

        agent = get_simulation_runtime().engine.get_agent(ctx.agent_id)
        if agent is not None:
            agent.state = "WAITING"
            agent.current_goal = args.reason or "等待中"
            agent.path = []
            agent.dirty = True
        return self.ok(call, {"duration_minutes": args.duration_minutes})


def build_tools() -> Iterable[Tool]:
    return [UpdateEmotionTool(), WaitTool()]
