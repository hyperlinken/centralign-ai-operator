"""
Human-in-the-loop approval gate + clarification channel.

Two tools:
  - `ask_human`: when the agent needs a missing fact or policy decision
  - `request_approval`: before executing an irreversible/sensitive action

The transport is pluggable (CLI stdin for the prototype, Slack/Teams in prod).
"""
from __future__ import annotations

from typing import Callable, Optional

from .base import Tool, ToolResult, ToolSpec


ASK_SPEC = ToolSpec(
    name="ask_human",
    description=(
        "Ask the operator a question when context memory is insufficient to proceed. "
        "Use sparingly — exhaust context lookups first."
    ),
    parameters={"question": "the specific question to ask the human"},
)

APPROVAL_SPEC = ToolSpec(
    name="request_approval",
    description=(
        "Pause and request human approval before executing a sensitive action "
        "(e.g. submitting a form, writing to an external system). "
        "Returns ok=true if approved, ok=false if rejected."
    ),
    parameters={
        "action_summary": "one-line description of what will happen if approved",
        "detail": "optional extra context the human should see before deciding",
    },
    requires_approval=True,
)


Prompter = Callable[[str], str]


def _default_prompter(message: str) -> str:
    print("\n\033[33m[HUMAN NEEDED]\033[0m " + message)
    return input("> ").strip()


class AskHumanTool(Tool):
    def __init__(self, prompter: Optional[Prompter] = None):
        super().__init__(ASK_SPEC)
        self._prompt = prompter or _default_prompter

    def call(self, **kwargs) -> ToolResult:
        q = (kwargs.get("question") or "").strip()
        if not q:
            return ToolResult.failure("missing `question`")
        answer = self._prompt(q)
        return ToolResult.success(
            observation=f"human answered: {answer!r}",
            data={"question": q, "answer": answer},
        )


class ApprovalTool(Tool):
    def __init__(self, prompter: Optional[Prompter] = None, auto_approve: bool = False):
        super().__init__(APPROVAL_SPEC)
        self._prompt = prompter or _default_prompter
        self._auto_approve = auto_approve

    def call(self, **kwargs) -> ToolResult:
        summary = (kwargs.get("action_summary") or "").strip()
        detail = (kwargs.get("detail") or "").strip()
        if not summary:
            return ToolResult.failure("missing `action_summary`")
        if self._auto_approve:
            return ToolResult.success(
                observation=f"[auto-approved] {summary}",
                data={"approved": True, "summary": summary, "detail": detail, "mode": "auto"},
            )
        msg = f"APPROVAL REQUIRED: {summary}"
        if detail:
            msg += f"\nDetail: {detail}"
        msg += "\nType 'yes' to approve, anything else to reject:"
        reply = self._prompt(msg).lower()
        approved = reply in ("y", "yes", "approve", "ok")
        return ToolResult(
            ok=approved,
            observation=("approved" if approved else "rejected") + f" — {summary}",
            data={"approved": approved, "summary": summary, "detail": detail, "mode": "manual"},
            error=None if approved else "human rejected the action",
        )
