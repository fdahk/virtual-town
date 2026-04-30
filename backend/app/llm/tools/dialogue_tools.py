"""对话相关工具。"""

from __future__ import annotations

from typing import Iterable

from pydantic import BaseModel, Field, ValidationError

from app.db.models import AgentState
from app.llm.tools.base import Tool, ToolCall, ToolContext, ToolResult, ToolSpec


class _TalkArgs(BaseModel):
    target_entity_id: str
    topic: str = ""
    tone: str = "friendly"
    message: str | None = Field(default=None, description="可选：直接指定台词")


class TalkToEntityTool(Tool):
    """
    MVP 中 NPC-NPC 对话通过该工具触发：
    - 只负责校验目标是否在交互半径内；
    - 真正的对话内容由 Conversation 服务（已有 generate_npc_reply）异步生成；
    - 工具返回 topic + tone，让引擎在下一 tick 派发对话任务。
    """

    name = "talk_to_entity"

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="与附近的实体发起对话。模型可选提供 topic/tone，台词由服务器生成。",
            owner_module="dialogue",
            allowed_entity_types=["human", "player"],
            parameters_schema={
                "type": "object",
                "properties": {
                    "target_entity_id": {"type": "string"},
                    "topic": {"type": "string", "description": "想说什么主题"},
                    "tone": {
                        "type": "string",
                        "enum": ["friendly", "neutral", "angry", "shy", "curious"],
                    },
                    "message": {
                        "type": "string",
                        "description": "可选：直接写好要说的话",
                    },
                },
                "required": ["target_entity_id"],
            },
            rerun_on_failure=False,
        )

    async def execute(self, ctx: ToolContext, call: ToolCall) -> ToolResult:
        try:
            args = _TalkArgs.model_validate(call.arguments)
        except ValidationError as e:
            return self.fail(call, "INVALID_ARGUMENTS", str(e))

        target = await ctx.session.get(AgentState, args.target_entity_id)
        if target is None:
            return self.fail(call, "TARGET_NOT_FOUND", "entity not found")
        if target.scene_id != ctx.scene_id:
            return self.fail(call, "OUT_OF_RANGE", "target in another scene")
        if abs(target.x - ctx.position[0]) + abs(target.y - ctx.position[1]) > 3:
            return self.fail(call, "OUT_OF_RANGE", "too far to talk", retryable=True)

        return ToolResult(
            tool=call.tool,
            success=True,
            result={
                "target_entity_id": args.target_entity_id,
                "topic": args.topic,
                "tone": args.tone,
                "message": args.message,
            },
            memory_candidates=[
                {
                    "memory_type": "chat",
                    "scope": "short_term",
                    "description": f"想和 {args.target_entity_id} 聊 {args.topic or '日常'}",
                    "importance": 3,
                    "keywords": [args.target_entity_id, args.topic or "闲聊"],
                }
            ],
        )


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
    return [TalkToEntityTool(), FaceEntityTool()]
