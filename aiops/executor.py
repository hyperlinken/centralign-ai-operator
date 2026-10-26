"""
Executor — dispatches a planner decision to the tool registry.

Responsibilities:
  - resolve the tool by name (returns a clean failure if unknown so the
    planner can re-plan instead of crashing)
  - apply retry policy for transient failures (e.g. Playwright timeouts)
  - never catch programmer bugs — those should surface
"""
from __future__ import annotations

import time
from typing import Dict

from .tools import ToolRegistry, ToolResult


class Executor:
    def __init__(self, registry: ToolRegistry, *, max_retries: int = 1):
        self._registry = registry
        self._max_retries = max_retries

    def run(self, tool_name: str, args: Dict) -> ToolResult:
        try:
            tool = self._registry.get(tool_name)
        except KeyError:
            available = ", ".join(self._registry.names())
            return ToolResult.failure(
                f"unknown tool {tool_name!r}; available: {available}"
            )

        last: ToolResult = ToolResult.failure("not executed")
        for attempt in range(self._max_retries + 1):
            try:
                result = tool.call(**args)
            except TypeError as e:
                # bad argument shape → planner mistake, don't retry
                return ToolResult.failure(f"invalid args for {tool_name}: {e}")
            except Exception as e:
                # unexpected tool crash — count as retryable
                result = ToolResult.failure(f"tool crashed: {e}")

            if result.ok:
                return result
            last = result
            # back off then retry on failure (transient browser issues etc.)
            if attempt < self._max_retries:
                time.sleep(0.5 * (attempt + 1))
        return last
