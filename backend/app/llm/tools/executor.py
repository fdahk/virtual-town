"""
ToolExecutor：执行流水线。

流程（严格按 `LLM与ToolCalling模块实施方案.md` 第 6 节）：
    ToolCall
      ↓ registry 查找
      ↓ Pydantic 参数校验（在具体工具内部）
      ↓ 权限校验（基于 entity_type）
      ↓ 世界状态校验（由工具 execute 内部完成）
      ↓ 调用业务
      ↓ 返回 ToolResult
      ↓ 写事件 / 记忆候选由上层消费

关键约束：
- 任何异常都转换为 ToolResult(success=False)，不抛到仿真主循环。
- LLM 返回未知工具名 → ToolError.code=INVALID_ARGUMENTS，不阻塞后续调用。
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.llm.tools.base import Tool, ToolCall, ToolContext, ToolError, ToolResult
from app.llm.tools.registry import ToolRegistry, get_tool_registry

logger = get_logger(__name__)


class ToolExecutor:
    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self.registry = registry or get_tool_registry()

    async def execute_one(self, ctx: ToolContext, call: ToolCall) -> ToolResult:
        tool = self.registry.get(call.tool)
        if tool is None:
            logger.warning("tool not found: %s", call.tool)
            return ToolResult(
                tool=call.tool,
                success=False,
                error=ToolError(
                    code="TARGET_NOT_FOUND",
                    message=f"tool `{call.tool}` not registered",
                ),
            )
        if not self._check_permission(tool, ctx):
            return ToolResult(
                tool=call.tool,
                success=False,
                error=ToolError(
                    code="PERMISSION_DENIED",
                    message=f"{ctx.entity_type} cannot invoke `{call.tool}`",
                ),
            )
        try:
            result = await tool.execute(ctx, call)
        except Exception as exc:
            logger.exception("tool %s crashed", call.tool)
            result = ToolResult(
                tool=call.tool,
                success=False,
                error=ToolError(
                    code="INTERNAL_ERROR",
                    message=str(exc) or "tool crashed",
                    retryable=False,
                ),
            )
        return result

    async def execute_batch(
        self, ctx: ToolContext, calls: list[ToolCall]
    ) -> list[ToolResult]:
        results: list[ToolResult] = []
        for call in calls:
            results.append(await self.execute_one(ctx, call))
        return results

    def _check_permission(self, tool: Tool, ctx: ToolContext) -> bool:
        allowed = tool.spec().allowed_entity_types
        if allowed is None:
            return True
        return ctx.entity_type in allowed


_executor: ToolExecutor | None = None


def get_tool_executor() -> ToolExecutor:
    global _executor
    if _executor is None:
        _executor = ToolExecutor()
    return _executor
