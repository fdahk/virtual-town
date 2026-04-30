"""
LLM Tool Calling：统一工具注册、参数校验、权限校验、业务执行和结果封装。

设计对齐：
- `docs/实施方案/ToolCalling工具契约V1.md`
- `docs/实施方案/LLM与ToolCalling模块实施方案.md`

职责划分：
- `base.py`     Tool 抽象、Context、ToolCall / ToolResult / ToolError
- `registry.py` ToolRegistry：集中注册、按身份授权
- `executor.py` ToolExecutor：校验 + 执行 + 写事件/记忆
- `world_tools.py` / `dialogue_tools.py` / `memory_tools.py` / `animal_tools.py` / `state_tools.py`
  具体工具实现

LLM 决策流程：
1. Agent 服务根据 Agent 身份从 ToolRegistry 取出可用工具集。
2. 把工具 JSON Schema 注入 prompt，要求 LLM 返回 ToolCall 数组。
3. ToolExecutor 按顺序执行、校验、写事件。
4. 执行结果汇总为 AgentDecision，进入 SimulationEngine 改变世界。
"""

from app.llm.tools.base import (
    Tool,
    ToolCall,
    ToolContext,
    ToolError,
    ToolResult,
    ToolSpec,
)
from app.llm.tools.executor import ToolExecutor, get_tool_executor
from app.llm.tools.registry import ToolRegistry, get_tool_registry

__all__ = [
    "Tool",
    "ToolCall",
    "ToolContext",
    "ToolError",
    "ToolExecutor",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "get_tool_executor",
    "get_tool_registry",
]
