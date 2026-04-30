"""记忆相关工具：写入与检索。"""

from __future__ import annotations

from typing import Iterable, Literal

from pydantic import BaseModel, Field, ValidationError

from app.llm.tools.base import Tool, ToolCall, ToolContext, ToolResult, ToolSpec
from app.schemas.memory import MemorySearchRequest
from app.services.memory_service import get_memory_service


class _WriteMemoryArgs(BaseModel):
    memory_type: Literal["event", "thought", "chat", "summary"] = "event"
    scope: Literal["working", "short_term", "long_term"] = "short_term"
    description: str = Field(..., max_length=400)
    importance: int = Field(default=3, ge=1, le=10)
    subject: str | None = None
    predicate: str | None = None
    object: str | None = None
    keywords: list[str] = Field(default_factory=list)
    emotional_valence: float = 0.0


class WriteMemoryTool(Tool):
    """
    模型主动写记忆。MemoryService 会再做二次裁剪：
    - 重要度低于 2 的 thought 会自动降级为 working，不持久。
    - 过长 description 会被截断。
    """

    name = "write_memory"

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="写入一条记忆（事件、想法、对话或总结）。",
            owner_module="memory",
            allowed_entity_types=["human", "animal", "player"],
            parameters_schema={
                "type": "object",
                "properties": {
                    "memory_type": {"type": "string", "enum": ["event", "thought", "chat", "summary"]},
                    "scope": {"type": "string", "enum": ["working", "short_term", "long_term"]},
                    "description": {"type": "string"},
                    "importance": {"type": "integer", "minimum": 1, "maximum": 10},
                    "subject": {"type": "string"},
                    "predicate": {"type": "string"},
                    "object": {"type": "string"},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                    "emotional_valence": {"type": "number"},
                },
                "required": ["description"],
            },
        )

    async def execute(self, ctx: ToolContext, call: ToolCall) -> ToolResult:
        try:
            args = _WriteMemoryArgs.model_validate(call.arguments)
        except ValidationError as e:
            return self.fail(call, "INVALID_ARGUMENTS", str(e))

        if args.memory_type == "thought" and args.importance < 2:
            args.scope = "working"

        mem = await get_memory_service().write(
            ctx.session,
            agent_id=ctx.agent_id,
            memory_type=args.memory_type,
            scope=args.scope,
            description=args.description[:400],
            importance=args.importance,
            subject=args.subject,
            predicate=args.predicate,
            object_=args.object,
            keywords=args.keywords[:8],
            emotional_valence=args.emotional_valence,
            commit=False,
        )
        return self.ok(call, {"memory_id": mem.id})


class _SearchMemoryArgs(BaseModel):
    query: str = Field(..., max_length=300)
    limit: int = Field(default=6, ge=1, le=20)


class SearchMemoryTool(Tool):
    name = "search_memory"

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="根据关键字或语义检索当前 Agent 的记忆。",
            owner_module="memory",
            allowed_entity_types=["human", "animal", "player"],
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": ["query"],
            },
        )

    async def execute(self, ctx: ToolContext, call: ToolCall) -> ToolResult:
        try:
            args = _SearchMemoryArgs.model_validate(call.arguments)
        except ValidationError as e:
            return self.fail(call, "INVALID_ARGUMENTS", str(e))
        results = await get_memory_service().search(
            ctx.session,
            ctx.agent_id,
            MemorySearchRequest(query=args.query, limit=args.limit),
            now=ctx.world_time,
        )
        return self.ok(
            call,
            {
                "results": [
                    {
                        "memory_id": r.memory.id,
                        "description": r.memory.description,
                        "score": r.score,
                    }
                    for r in results
                ]
            },
        )


def build_tools() -> Iterable[Tool]:
    return [WriteMemoryTool(), SearchMemoryTool()]
