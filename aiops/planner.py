"""
Planner — the LLM-backed brain.

Two responsibilities:

  1. `initial_plan(goal, context, hints)` — produce a short, high-level plan
     at the start of a run.
  2. `next_step(working_memory)` — on every tick, decide the *next* tool call
     (plus a chain-of-thought `thought` for auditability) or declare the
     task finished.

We deliberately keep plans short and re-plan on every step. The literature
calls this "re-planning on each observation" and it's much more robust to
unexpected UI states than a frozen multi-step plan.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .llm import LLM, LLMMessage
from .memory import WorkingMemory


PLANNER_SYSTEM = """You are the planning brain of an autonomous AI employee for an enterprise.
You achieve business goals by selecting one tool call at a time, observing
the result, and adapting.

CORE OPERATING PRINCIPLES
- Prefer reading over writing. Reads are safe; writes change state and may need approval.
- Verify before you declare success. Never call `finish` without visible evidence of the outcome.
- Honour the company policies in the semantic context. If a policy forbids an action, don't do it.
- If genuinely blocked by missing information, call `ask_human`. Do not guess credentials, URLs, or policies.
- Keep thoughts short (1–2 sentences). The next step is more valuable than more deliberation.

OUTPUT FORMAT
Always respond with a single JSON object. Never add prose outside the JSON.
"""


NEXT_STEP_SCHEMA = """
{
  "thought": "one-sentence rationale for THIS step",
  "plan": ["short", "updated", "high-level steps you still intend to do"],
  "tool": "<name from the tool registry>",
  "args": { "...": "..." },
  "set_facts": { "optional_key": "optional_value" }  // facts to persist into working memory
}
"""

INITIAL_PLAN_SCHEMA = """
{
  "plan": ["high-level step 1", "step 2", "..."],
  "clarifications_needed": ["optional questions that must be answered first"]
}
"""


@dataclass
class StepDecision:
    thought: str
    tool: str
    args: Dict[str, Any]
    plan: List[str]
    set_facts: Dict[str, Any]


class Planner:
    def __init__(self, llm: LLM, tool_spec_block: str, semantic_context: str,
                 episodic_hints: str = ""):
        self.llm = llm
        self._tools = tool_spec_block
        self._semantic = semantic_context
        self._hints = episodic_hints

    # ------------------------------------------------------------------ #

    def initial_plan(self, goal: str) -> List[str]:
        system = PLANNER_SYSTEM + "\n\nAVAILABLE TOOLS:\n" + self._tools + "\n\nCOMPANY CONTEXT:\n" + self._semantic
        if self._hints:
            system += "\n\n" + self._hints
        user = (
            f"The user asked: {goal!r}\n\n"
            "Produce a concise plan (3–7 steps) describing how you will achieve this. "
            "Do not execute anything yet. If any critical information is missing "
            "from company context, add it to clarifications_needed."
        )
        out = self.llm.chat_json(system, [LLMMessage("user", user)],
                                 schema_hint=INITIAL_PLAN_SCHEMA, max_tokens=800, temperature=0.3)
        plan = [str(s) for s in out.get("plan", []) if str(s).strip()]
        return plan

    def next_step(self, memory: WorkingMemory) -> StepDecision:
        system = PLANNER_SYSTEM + "\n\nAVAILABLE TOOLS:\n" + self._tools + "\n\nCOMPANY CONTEXT:\n" + self._semantic
        if self._hints:
            system += "\n\n" + self._hints

        user = (
            memory.render_for_planner()
            + "\n\nDecide the SINGLE next tool call. If the goal is complete AND you have verifiable "
              "evidence (e.g. an id in the target system), call `finish`. Otherwise pick the most useful "
              "next action."
        )

        out = self.llm.chat_json(system, [LLMMessage("user", user)],
                                 schema_hint=NEXT_STEP_SCHEMA, max_tokens=1024, temperature=0.2)

        thought = str(out.get("thought", "")).strip() or "(no thought)"
        tool = str(out.get("tool", "")).strip()
        if not tool:
            raise ValueError(f"planner returned no tool: {out}")

        args = out.get("args") or {}
        if not isinstance(args, dict):
            raise ValueError(f"planner `args` must be dict, got {type(args).__name__}")

        plan = [str(p) for p in (out.get("plan") or []) if str(p).strip()]
        facts = out.get("set_facts") or {}
        if not isinstance(facts, dict):
            facts = {}

        return StepDecision(thought=thought, tool=tool, args=args, plan=plan, set_facts=facts)
