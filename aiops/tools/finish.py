"""
Finish tool — a sentinel that the agent uses to declare the task complete.

The runtime treats a successful `finish` call as the exit condition for the
Goal→Plan→Execute→Observe loop. The verifier then independently checks the
claimed outcome.
"""
from __future__ import annotations

from .base import Tool, ToolResult, ToolSpec


FINISH_SPEC = ToolSpec(
    name="finish",
    description=(
        "Call this ONLY when you believe the user's goal has been achieved. "
        "Pass a concise summary and the evidence (ids, URLs, file paths) that proves it. "
        "The runtime will independently verify your claim."
    ),
    parameters={
        "summary": "one-paragraph summary of what was done",
        "evidence": "dict of ids/URLs/paths that prove completion (e.g. {'ap_entry_id': 'AP-1001'})",
    },
)


class FinishTool(Tool):
    def __init__(self):
        super().__init__(FINISH_SPEC)

    def call(self, **kwargs) -> ToolResult:
        summary = (kwargs.get("summary") or "").strip()
        if not summary:
            return ToolResult.failure("missing `summary`")
        evidence = kwargs.get("evidence") or {}
        if not isinstance(evidence, dict):
            return ToolResult.failure("`evidence` must be a dict")
        return ToolResult.success(
            observation=f"agent declared task complete: {summary}",
            data={"summary": summary, "evidence": evidence, "finished": True},
            evidence=evidence,
        )
