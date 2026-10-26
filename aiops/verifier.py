"""
Verifier — an independent check that the agent actually achieved the goal.

Two layers:

  1. Programmatic ground-truth check against the target system of record.
     For the sandbox this is a GET to the AP system's /api/entries. In
     production this is a connector call to the real system.

  2. An LLM "judge" that reads the goal + the agent's claimed summary +
     evidence + ground-truth snapshot, and returns a verdict with reasoning.

The LLM judge is deliberately separate from the planner call so a flawed
planner can't rubber-stamp its own output.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

from .llm import LLM, LLMMessage


VERIFIER_SYSTEM = """You are an independent verifier for an autonomous AI employee.
Given (a) the user's original goal, (b) the agent's claimed completion summary
and evidence, and (c) a snapshot of the system of record, decide whether the
goal was actually achieved.

Be strict:
- If the agent says it entered a value but the system of record does not show it, verdict is "failed".
- If values match but a policy was violated (e.g. duplicate), verdict is "partial" with reasoning.
- If everything checks out, verdict is "succeeded".
Return ONLY a JSON object matching the schema.
"""

VERIFIER_SCHEMA = """
{
  "verdict": "succeeded" | "partial" | "failed",
  "reasoning": "two-sentence explanation",
  "mismatches": ["any specific field mismatches, empty list if none"]
}
"""


@dataclass
class Verdict:
    verdict: str
    reasoning: str
    mismatches: List[str]
    ground_truth: Dict[str, Any]


class Verifier:
    def __init__(self, llm: LLM, ap_api_url: str):
        self._llm = llm
        self._ap_url = ap_api_url.rstrip("/")

    def _ground_truth(self) -> Dict[str, Any]:
        """Pull latest state from the AP system of record."""
        try:
            r = requests.get(f"{self._ap_url}/api/entries", timeout=5)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            return {"error": str(e)}

    def check(self, goal: str, claimed_summary: str, claimed_evidence: Dict[str, Any]) -> Verdict:
        ground = self._ground_truth()
        prompt = (
            f"GOAL:\n{goal}\n\n"
            f"AGENT'S CLAIMED SUMMARY:\n{claimed_summary}\n\n"
            f"AGENT'S CLAIMED EVIDENCE:\n{json.dumps(claimed_evidence, indent=2)}\n\n"
            f"SYSTEM OF RECORD (AP system current state):\n{json.dumps(ground, indent=2)}"
        )
        out = self._llm.chat_json(
            VERIFIER_SYSTEM,
            [LLMMessage("user", prompt)],
            schema_hint=VERIFIER_SCHEMA,
            max_tokens=600,
            temperature=0.0,
        )
        verdict = str(out.get("verdict", "")).lower()
        if verdict not in ("succeeded", "partial", "failed"):
            verdict = "failed"
        return Verdict(
            verdict=verdict,
            reasoning=str(out.get("reasoning", "")).strip(),
            mismatches=[str(m) for m in (out.get("mismatches") or [])],
            ground_truth=ground,
        )
