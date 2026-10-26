"""
Working memory — the per-task scratchpad.

Holds the running trace of (plan step → action → observation) tuples that the
planner sees on every turn. Also stores structured facts the agent has
"learned" during the run (e.g. an invoice amount it scraped) so it does not
have to re-derive them from scratched page text.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Step:
    index: int
    thought: str
    tool: str
    args: Dict[str, Any]
    ok: bool
    observation: str
    error: Optional[str] = None


class WorkingMemory:
    def __init__(self, goal: str):
        self.goal: str = goal
        self.plan: List[str] = []
        self.steps: List[Step] = []
        self.facts: Dict[str, Any] = {}  # key -> value, agent-controlled

    def record_step(self, step: Step) -> None:
        self.steps.append(step)

    def set_fact(self, key: str, value: Any) -> None:
        self.facts[key] = value

    def render_for_planner(self, max_steps: int = 12) -> str:
        """
        Compress the trace for inclusion in the planner prompt.

        We keep the full tail — the planner cares most about recent
        observations — and summarise the head if it got long.
        """
        parts = [f"GOAL: {self.goal}"]
        if self.plan:
            parts.append("CURRENT PLAN:\n" + "\n".join(f"  {i+1}. {p}" for i, p in enumerate(self.plan)))
        if self.facts:
            parts.append("KNOWN FACTS:\n" + "\n".join(f"  - {k}: {v}" for k, v in self.facts.items()))
        if self.steps:
            kept = self.steps[-max_steps:]
            if len(self.steps) > max_steps:
                parts.append(f"[{len(self.steps) - max_steps} earlier step(s) omitted]")
            trace = []
            for s in kept:
                head = f"step {s.index} — tool={s.tool} ok={s.ok}"
                trace.append(f"{head}\n  thought: {s.thought}\n  args: {s.args}\n  observation: {s.observation[:600]}")
                if s.error:
                    trace.append(f"  error: {s.error}")
            parts.append("TRACE:\n" + "\n".join(trace))
        return "\n\n".join(parts)
