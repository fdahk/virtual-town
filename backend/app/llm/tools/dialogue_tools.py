"""对话相关工具。

阶段 19 起 NPC-NPC 与玩家-NPC 对话**统一通过请求-同意-拒绝协议**：见
``app.llm.tools.social_tools.RequestInteractionTool``。本模块只保留辅助工具
（如 ``face_entity`` 让 NPC 转身面向某个实体）。
"""

from __future__ import annotations

from typing import Iterable

from pydantic import BaseModel, ValidationError

from app.db.models import AgentState
from app.llm.tools.base import Tool, ToolCall, ToolContext, ToolResult, ToolSpec


class _FaceArgs(BaseModel):
    target_entity_id: str


class FaceEntityTool(Tool):
    name = "face_entity"

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="让当前 Agent 面向某个实体。",
            owner_module="dialogue",
            allowed_entity_types=["human", "animal", "player"],
            parameters_schema={
                "type": "object",
                "properties": {"target_entity_id": {"type": "string"}},
                "required": ["target_entity_id"],
            },
        )

    async def execute(self, ctx: ToolContext, call: ToolCall) -> ToolResult:
        try:
            args = _FaceArgs.model_validate(call.arguments)
        except ValidationError as e:
            return self.fail(call, "INVALID_ARGUMENTS", str(e))
        target = await ctx.session.get(AgentState, args.target_entity_id)
        if target is None:
            return self.fail(call, "TARGET_NOT_FOUND", "entity not found")

        from app.services.simulation_runtime import get_simulation_runtime

        engine = get_simulation_runtime().engine
        agent = engine.get_agent(ctx.agent_id)
        if agent is not None:
            dx = target.x - agent.x
            dy = target.y - agent.y
            if abs(dx) >= abs(dy):
                agent.facing = "right" if dx > 0 else "left"
            else:
                agent.facing = "down" if dy > 0 else "up"
            agent.dirty = True
        return self.ok(call, {"facing": agent.facing if agent else "down"})


def build_tools() -> Iterable[Tool]:
    return [FaceEntityTool()]
