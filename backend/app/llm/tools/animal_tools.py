"""动物专属工具：react_to_pet / make_sound。"""

from __future__ import annotations

from typing import Iterable, Literal

from pydantic import BaseModel, Field, ValidationError

from app.llm.tools.base import Tool, ToolCall, ToolContext, ToolResult, ToolSpec


class _ReactArgs(BaseModel):
    actor_entity_id: str | None = None
    reaction: Literal["enjoy", "tolerate", "escape", "threaten", "approach"] = "tolerate"
    emotion: str | None = None


class ReactToPetTool(Tool):
    name = "react_to_pet"

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="动物对人类互动的情绪反应。",
            owner_module="animal",
            allowed_entity_types=["animal"],
            parameters_schema={
                "type": "object",
                "properties": {
                    "actor_entity_id": {"type": "string"},
                    "reaction": {
                        "type": "string",
                        "enum": ["enjoy", "tolerate", "escape", "threaten", "approach"],
                    },
                    "emotion": {"type": "string"},
                },
                "required": ["reaction"],
            },
        )

    async def execute(self, ctx: ToolContext, call: ToolCall) -> ToolResult:
        try:
            args = _ReactArgs.model_validate(call.arguments)
        except ValidationError as e:
            return self.fail(call, "INVALID_ARGUMENTS", str(e))

        from app.services.simulation_runtime import get_simulation_runtime

        engine = get_simulation_runtime().engine
        agent = engine.get_agent(ctx.agent_id)
        if agent is not None:
            if args.emotion:
                agent.emotion = args.emotion
            agent.dirty = True

        return ToolResult(
            tool=call.tool,
            success=True,
            result={"reaction": args.reaction, "emotion": args.emotion},
            memory_candidates=[
                {
                    "memory_type": "event",
                    "scope": "short_term",
                    "description": f"对 {args.actor_entity_id or '对方'} 做出反应：{args.reaction}",
                    "importance": 3,
                    "keywords": [args.reaction],
                    "emotional_valence": 0.5 if args.reaction in {"enjoy", "approach"} else -0.3,
                }
            ],
        )


class _SoundArgs(BaseModel):
    sound: Literal["bark", "meow", "purr", "whine", "growl"] = "bark"
    reason: str = ""


class MakeSoundTool(Tool):
    name = "make_sound"

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="动物发出声音（叫、喵、呼噜等）。",
            owner_module="animal",
            allowed_entity_types=["animal"],
            parameters_schema={
                "type": "object",
                "properties": {
                    "sound": {"type": "string", "enum": ["bark", "meow", "purr", "whine", "growl"]},
                    "reason": {"type": "string"},
                },
                "required": ["sound"],
            },
        )

    async def execute(self, ctx: ToolContext, call: ToolCall) -> ToolResult:
        try:
            args = _SoundArgs.model_validate(call.arguments)
        except ValidationError as e:
            return self.fail(call, "INVALID_ARGUMENTS", str(e))
        return self.ok(call, {"sound": args.sound, "reason": args.reason})


def build_tools() -> Iterable[Tool]:
    return [ReactToPetTool(), MakeSoundTool()]
