"""
Episodic memory — persistent log of past runs so the agent can learn.

Each completed run writes a JSON blob with:
  - the goal
  - final outcome (success / partial / failure)
  - the sequence of (tool, args, ok) tuples (no raw screenshots)
  - lessons: short LLM-generated notes about what worked / what didn't

Future runs can retrieve similar past episodes to pre-seed the planner.
We implement a simple keyword-overlap retriever here; a vector store slots
in behind the same interface later.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, List


_WORD = re.compile(r"[a-z0-9]{3,}")


def _tokens(s: str) -> set[str]:
    return set(_WORD.findall(s.lower()))


@dataclass
class Episode:
    run_id: str
    goal: str
    outcome: str           # success | partial | failure
    summary: str
    lessons: List[str] = field(default_factory=list)
    actions: List[Dict[str, Any]] = field(default_factory=list)


class EpisodicMemory:
    def __init__(self, root: Path):
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def record(self, episode: Episode) -> None:
        path = self._root / f"{episode.run_id}.json"
        path.write_text(json.dumps(asdict(episode), indent=2), encoding="utf-8")

    def all(self) -> List[Episode]:
        out = []
        for f in sorted(self._root.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                out.append(Episode(**data))
            except Exception:
                continue
        return out

    def similar(self, goal: str, k: int = 3) -> List[Episode]:
        goal_toks = _tokens(goal)
        if not goal_toks:
            return []
        scored = []
        for e in self.all():
            score = len(goal_toks & _tokens(e.goal))
            if score:
                scored.append((score, e))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:k]]

    def render_hints(self, goal: str) -> str:
        past = self.similar(goal)
        if not past:
            return ""
        lines = ["Past similar runs:"]
        for e in past:
            lines.append(f"- [{e.outcome}] {e.goal}")
            for lesson in e.lessons[:3]:
                lines.append(f"    lesson: {lesson}")
        return "\n".join(lines)
