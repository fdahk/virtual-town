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
- LLM 返回未知工具名 → ToolError.code=TOOL_NOT_FOUND，不阻塞后续调用。
- 每次调用通过 Observer.record_tool_call 审计（参数、校验分项、结果、耗时）。
"""

from __future__ import annotations

import time
from typing import Any

from app.core.logging import get_logger
from app.core.trace_context import TraceContext
from app.llm.tools.base import Tool, ToolCall, ToolContext, ToolError, ToolResult
from app.llm.tools.registry import ToolRegistry, get_tool_registry

logger = get_logger(__name__)


class ToolExecutor:
    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self.registry = registry or get_tool_registry()

    async def execute_one(self, ctx: ToolContext, call: ToolCall) -> ToolResult:
        started = time.perf_counter()
        tool = self.registry.get(call.tool)
        if tool is None:
            logger.warning("tool not found: %s", call.tool)
            result = ToolResult(
                tool=call.tool,
                success=False,
                error=ToolError(
                    code="TOOL_NOT_FOUND",
                    message=f"tool `{call.tool}` not registered",
                ),
            )
            await self._audit(
                call=call,
                ctx=ctx,
                result=result,
                duration_ms=int((time.perf_counter() - started) * 1000),
                schema_valid=False,
                permission_valid=None,
                world_state_valid=None,
            )
            return result

        permission_ok = self._check_permission(tool, ctx)
        if not permission_ok:
            result = ToolResult(
                tool=call.tool,
                success=False,
                error=ToolError(
                    code="TOOL_PERMISSION_DENIED",
                    message=f"{ctx.entity_type} cannot invoke `{call.tool}`",
                ),
            )
            await self._audit(
                call=call,
                ctx=ctx,
                result=result,
                duration_ms=int((time.perf_counter() - started) * 1000),
                schema_valid=True,
                permission_valid=False,
                world_state_valid=None,
            )
            return result

        try:
            with TraceContext.span(f"tool.{call.tool}", agent_id=ctx.agent_id):
                result = await tool.execute(ctx, call)
        except Exception as exc:
            logger.exception("tool %s crashed", call.tool)
            result = ToolResult(
                tool=call.tool,
                success=False,
                error=ToolError(
                    code="TOOL_EXECUTION_FAILED",
                    message=str(exc) or "tool crashed",
                    retryable=False,
                ),
            )

        duration_ms = int((time.perf_counter() - started) * 1000)
        # world_state_valid 从错误码推断（世界状态校验失败的典型错误码）
        world_state_valid: bool | None = True
        if result.error is not None:
            if result.error.code in {
                "TARGET_NOT_FOUND",
                "TARGET_NOT_REACHABLE",
                "OUT_OF_RANGE",
                "STATE_CONFLICT",
                "WORLD_TARGET_NOT_REACHABLE",
                "WORLD_TARGET_NOT_FOUND",
                "WORLD_BLOCKED",
            }:
                world_state_valid = False
            elif result.error.code in {
                "INVALID_ARGUMENTS",
                "TOOL_INVALID_ARGUMENTS",
            }:
                world_state_valid = None
        await self._audit(
            call=call,
            ctx=ctx,
            result=result,
            duration_ms=duration_ms,
            schema_valid=True,
            permission_valid=True,
            world_state_valid=world_state_valid,
        )
        return result

    async def _audit(
        self,
        *,
        call: ToolCall,
        ctx: ToolContext,
        result: ToolResult,
        duration_ms: int,
        schema_valid: bool | None,
        permission_valid: bool | None,
        world_state_valid: bool | None,
    ) -> None:
        try:
            from app.services.observer import get_observer

            await get_observer().record_tool_call(
                tool=call.tool,
                arguments=call.arguments,
                success=result.success,
                duration_ms=duration_ms,
                schema_valid=schema_valid,
                permission_valid=permission_valid,
                world_state_valid=world_state_valid,
                result_summary=(
                    ", ".join(f"{k}={v}" for k, v in list(result.result.items())[:4])
                    if result.success
                    else (result.error.message if result.error else None)
                ),
                error_code=(result.error.code if result.error else None),
                caller_agent_id=ctx.agent_id,
                entity_type=ctx.entity_type,
                source=ctx.source,
            )
        except Exception:
            logger.debug("observer record_tool_call failed", exc_info=True)

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
