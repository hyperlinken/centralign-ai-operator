"""
Tool registry — the planner's view of what the agent can do.
"""
from __future__ import annotations

from typing import Dict, List

from .base import Tool, ToolResult


class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(name)
        return self._tools[name]

    def names(self) -> List[str]:
        return list(self._tools.keys())

    def spec_prompt(self) -> str:
        """Render the registry as a block the planner can read."""
        lines = []
        for t in self._tools.values():
            params = ", ".join(f"{k}: {v}" for k, v in t.spec.parameters.items()) or "(none)"
            approval = " [REQUIRES APPROVAL]" if t.spec.requires_approval else ""
            lines.append(f"- {t.name}{approval}: {t.spec.description}\n    params: {params}")
        return "\n".join(lines)

    def close_all(self) -> None:
        for t in self._tools.values():
            try:
                t.close()
            except Exception:
                pass
