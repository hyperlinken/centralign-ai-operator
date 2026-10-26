"""
Runtime — the Goal → Plan → Execute → Observe → Adapt → Verify loop.

This is the orchestrator the CLI calls into. It is deliberately small: the
intelligence lives in the planner/verifier, and all world-affecting ops
live in tools. The runtime's only job is sequencing + bookkeeping.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .audit import AuditLog
from .executor import Executor
from .llm import LLM
from .memory import EpisodicMemory, SemanticMemory, Step, WorkingMemory
from .memory.episodic import Episode
from .planner import Planner, StepDecision
from .tools import ToolRegistry
from .verifier import Verdict, Verifier


MAX_CONSECUTIVE_FAILURES = 3


@dataclass
class RunResult:
    run_id: str
    goal: str
    agent_summary: str
    agent_evidence: Dict[str, Any]
    verdict: Optional[Verdict]
    steps: int
    outcome: str                       # "succeeded" | "partial" | "failed" | "aborted"
    run_dir: Path
    plan: List[str] = field(default_factory=list)


class Runtime:
    def __init__(
        self,
        *,
        llm: LLM,
        registry: ToolRegistry,
        semantic: SemanticMemory,
        episodic: EpisodicMemory,
        verifier: Verifier,
        runs_root: Path,
        max_steps: int = 25,
        on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ):
        self.llm = llm
        self.registry = registry
        self.semantic = semantic
        self.episodic = episodic
        self.verifier = verifier
        self.runs_root = runs_root
        self.max_steps = max_steps
        self.on_event = on_event or (lambda kind, data: None)

    # ------------------------------------------------------------------ #

    def execute(self, goal: str) -> RunResult:
        run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        run_dir = self.runs_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        audit = AuditLog(run_dir, run_id)

        def emit(kind: str, payload: Dict[str, Any]):
            audit.log(kind, payload)
            self.on_event(kind, payload)

        emit("run_started", {"goal": goal, "max_steps": self.max_steps})

        semantic_block = self.semantic.load_all()
        episodic_hints = self.episodic.render_hints(goal)
        planner = Planner(
            llm=self.llm,
            tool_spec_block=self.registry.spec_prompt(),
            semantic_context=semantic_block,
            episodic_hints=episodic_hints,
        )

        memory = WorkingMemory(goal=goal)

        try:
            plan = planner.initial_plan(goal)
        except Exception as e:
            emit("planner_error", {"phase": "initial", "error": str(e)})
            plan = []
        memory.plan = plan
        emit("initial_plan", {"plan": plan})

        agent_summary = ""
        agent_evidence: Dict[str, Any] = {}
        outcome = "failed"
        consecutive_failures = 0
        finished_normally = False

        for step_idx in range(1, self.max_steps + 1):
            try:
                decision = planner.next_step(memory)
            except Exception as e:
                emit("planner_error", {"phase": "step", "step": step_idx, "error": str(e)})
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    outcome = "aborted"
                    break
                continue

            emit("decision", {
                "step": step_idx, "thought": decision.thought,
                "tool": decision.tool, "args": decision.args, "plan": decision.plan,
            })

            if decision.plan:
                memory.plan = decision.plan
            for k, v in (decision.set_facts or {}).items():
                memory.set_fact(k, v)

            # Executor dispatches the tool call.
            # One retry inside the executor; replanning handles bigger failures.
            executor = Executor(self.registry, max_retries=1)
            result = executor.run(decision.tool, decision.args)

            emit("observation", {
                "step": step_idx, "tool": decision.tool, "ok": result.ok,
                "observation": result.observation[:1200],
                "error": result.error,
                "evidence_keys": list(result.evidence.keys()),
            })

            memory.record_step(Step(
                index=step_idx,
                thought=decision.thought,
                tool=decision.tool,
                args=decision.args,
                ok=result.ok,
                observation=result.observation,
                error=result.error,
            ))

            if decision.tool == "finish" and result.ok:
                agent_summary = (result.data or {}).get("summary", "")
                agent_evidence = (result.data or {}).get("evidence", {}) or {}
                finished_normally = True
                break

            if result.ok:
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    emit("abort", {"reason": "too_many_failures", "count": consecutive_failures})
                    outcome = "aborted"
                    break
        else:
            emit("abort", {"reason": "max_steps_reached", "steps": self.max_steps})
            outcome = "aborted"

        verdict: Optional[Verdict] = None
        if finished_normally:
            try:
                verdict = self.verifier.check(goal, agent_summary, agent_evidence)
                emit("verification", {
                    "verdict": verdict.verdict, "reasoning": verdict.reasoning,
                    "mismatches": verdict.mismatches,
                })
                outcome = verdict.verdict
            except Exception as e:
                emit("verifier_error", {"error": str(e)})
                outcome = "partial"

        self._record_episode(run_id, goal, outcome, agent_summary, memory)
        emit("run_finished", {"outcome": outcome, "steps": len(memory.steps)})

        return RunResult(
            run_id=run_id,
            goal=goal,
            agent_summary=agent_summary,
            agent_evidence=agent_evidence,
            verdict=verdict,
            steps=len(memory.steps),
            outcome=outcome,
            run_dir=run_dir,
            plan=memory.plan,
        )

    def _record_episode(self, run_id: str, goal: str, outcome: str, summary: str, memory: WorkingMemory):
        try:
            lessons = self._generate_lessons(goal, outcome, memory)
        except Exception:
            lessons = []
        actions = [
            {"tool": s.tool, "args": s.args, "ok": s.ok} for s in memory.steps
        ]
        self.episodic.record(Episode(
            run_id=run_id, goal=goal, outcome=outcome, summary=summary,
            lessons=lessons, actions=actions,
        ))

    def _generate_lessons(self, goal: str, outcome: str, memory: WorkingMemory) -> List[str]:
        """Ask the LLM to produce 1–3 one-line lessons from this run."""
        trace = memory.render_for_planner(max_steps=20)
        from .llm import LLMMessage
        out = self.llm.chat_json(
            system=(
                "Given a completed agent run, extract 1-3 short lessons that would help "
                "a future run of a similar task. Each lesson should be a single sentence, "
                "specific and actionable."
            ),
            messages=[LLMMessage("user", f"OUTCOME: {outcome}\n\n{trace}")],
            schema_hint='{"lessons": ["lesson 1", "lesson 2"]}',
            max_tokens=400, temperature=0.3,
        )
        return [str(l).strip() for l in (out.get("lessons") or []) if str(l).strip()]
