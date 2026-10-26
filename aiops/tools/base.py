"""
Tool abstraction.

A Tool is anything the agent can call to affect or observe the world. Every
tool exposes:

  - a stable `name` the planner uses to select it
  - a `spec` (natural-language description + parameter contract) that is
    injected into the planner prompt so the LLM knows what's available
  - a `call(**kwargs)` method that returns a `ToolResult`

New capabilities (SAP, Salesforce, Jira, desktop RPA...) are added by
subclassing Tool and registering it. The core runtime does not change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


class ToolError(RuntimeError):
    """Raised by a Tool when the action genuinely failed.

    Distinct from "the LLM asked for something invalid" — that's caught at the
    executor layer and surfaced as a different kind of observation.
    """


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: Dict[str, str]       # param_name -> short description
    requires_approval: bool = False  # HITL gate if True


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error: Optional[str] = None
    observation: str = ""             # text summary for the agent's context
    evidence: Dict[str, Any] = field(default_factory=dict)  # screenshots, DOM snippets, ids

    @classmethod
    def success(cls, observation: str, data: Any = None, evidence: Optional[dict] = None):
        return cls(ok=True, data=data, observation=observation, evidence=evidence or {})

    @classmethod
    def failure(cls, error: str, observation: str = "", evidence: Optional[dict] = None):
        return cls(ok=False, error=error, observation=observation or error, evidence=evidence or {})


class Tool:
    spec: ToolSpec

    def __init__(self, spec: ToolSpec):
        self.spec = spec

    @property
    def name(self) -> str:
        return self.spec.name

    def call(self, **kwargs) -> ToolResult:
        raise NotImplementedError

    def close(self) -> None:
        """Release resources. Default no-op."""
        pass
