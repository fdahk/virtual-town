"""Tool 抽象与通用模型。"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# 通用协议
# ---------------------------------------------------------------------------


class ToolError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class ToolCall(BaseModel):
    """LLM 返回的工具调用。"""

    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.5
    thought: str | None = None


class ToolResult(BaseModel):
    tool: str
    success: bool
    result: dict[str, Any] = Field(default_factory=dict)
    error: ToolError | None = None
    events: list[str] = Field(default_factory=list)
    memory_candidates: list[dict[str, Any]] = Field(default_factory=list)


@dataclass
class ToolContext:
    """
    执行工具时的上下文。
    ToolExecutor 负责构造，Tool 子类只读。
    """

    session: AsyncSession
    agent_id: str
    entity_type: str            # human / animal / player
    scene_id: str
    position: tuple[int, int]
    simulation_id: str
    world_time: Any
    source: str = "llm"         # llm / rule / system，写事件时的 source 字段
    extra: dict[str, Any] = field(default_factory=dict)


class ToolSpec(BaseModel):
    """工具注册元数据。"""

    name: str
    description: str
    owner_module: str
    # 允许使用该工具的 entity_type。None 表示全部。
    allowed_entity_types: list[str] | None = None
    # JSON Schema 片段，直接注入 prompt 给模型看
    parameters_schema: dict[str, Any]
    # 失败时是否触发重规划
    rerun_on_failure: bool = False


# ---------------------------------------------------------------------------
# Tool 基类
# ---------------------------------------------------------------------------


class Tool(abc.ABC):
    """
    具体工具抽象。子类必须实现 `spec()` 与 `execute()`，并为参数提供 Pydantic 模型。

    执行契约：
    - 所有外部副作用（写数据库、改 engine 内存态、广播事件）都由工具自己负责。
    - 异常统一捕获为 ToolError 返回，不抛出。
    - 工具之间不互相调用；需要协同由上层编排。
    """

    name: ClassVar[str]

    @abc.abstractmethod
    def spec(self) -> ToolSpec: ...

    @abc.abstractmethod
    async def execute(self, ctx: ToolContext, call: ToolCall) -> ToolResult: ...

    # 辅助：构造错误结果
    @staticmethod
    def fail(call: ToolCall, code: str, message: str, *, retryable: bool = False) -> ToolResult:
        return ToolResult(
            tool=call.tool,
            success=False,
            error=ToolError(code=code, message=message, retryable=retryable),
        )

    @staticmethod
    def ok(call: ToolCall, result: dict[str, Any] | None = None) -> ToolResult:
        return ToolResult(tool=call.tool, success=True, result=result or {})
