"""ToolRegistry：集中注册工具并按身份授权。"""

from __future__ import annotations

from typing import Any

from app.llm.tools.base import Tool, ToolSpec


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        spec = tool.spec()
        if spec.name in self._tools:
            raise ValueError(f"duplicate tool registration: {spec.name}")
        self._tools[spec.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def list_specs(self, entity_type: str | None = None) -> list[ToolSpec]:
        """按身份筛选可用工具规格。"""
        out: list[ToolSpec] = []
        for t in self._tools.values():
            s = t.spec()
            if entity_type is None:
                out.append(s)
                continue
            if s.allowed_entity_types is None or entity_type in s.allowed_entity_types:
                out.append(s)
        return out

    def openai_functions(self, entity_type: str | None = None) -> list[dict[str, Any]]:
        """返回 OpenAI 兼容的 functions JSON 片段。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": s.name,
                    "description": s.description,
                    "parameters": s.parameters_schema,
                },
            }
            for s in self.list_specs(entity_type)
        ]

    def prompt_catalog(self, entity_type: str | None = None) -> str:
        """把工具信息格式化成可直接嵌入 prompt 的文本。"""
        lines: list[str] = []
        for s in self.list_specs(entity_type):
            lines.append(f"- {s.name}: {s.description}")
            props = (s.parameters_schema or {}).get("properties", {})
            for key, meta in props.items():
                required = key in ((s.parameters_schema or {}).get("required") or [])
                lines.append(
                    "    "
                    + f"{key} ({meta.get('type', 'any')}{', required' if required else ''}):"
                    + f" {meta.get('description', '')}"
                )
        return "\n".join(lines)


_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    """返回全局单例。首次调用会懒加载所有内置工具。"""
    global _registry
    if _registry is None:
        reg = ToolRegistry()

        # 导入放在这里避免循环依赖
        from app.llm.tools import (
            animal_tools,
            dialogue_tools,
            memory_tools,
            state_tools,
            world_tools,
        )

        for mod in (world_tools, dialogue_tools, animal_tools, memory_tools, state_tools):
            for tool in mod.build_tools():
                reg.register(tool)
        _registry = reg
    return _registry
