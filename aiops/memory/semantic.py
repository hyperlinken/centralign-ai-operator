"""
Semantic memory — the company's institutional knowledge.

Loads context.md and any extra policy files from company_context/. In
production this would be a vector store with retrieval + ACLs; for the
prototype we load the whole thing into the planner's system prompt because
it fits comfortably in context.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Tuple


class SemanticMemory:
    def __init__(self, root: Path):
        self._root = root

    def load_all(self) -> str:
        """Concatenate every .md under company_context/ in a stable order."""
        files = sorted(self._root.rglob("*.md"))
        chunks: List[str] = []
        for f in files:
            rel = f.relative_to(self._root).as_posix()
            chunks.append(f"=== {rel} ===\n{f.read_text(encoding='utf-8')}")
        return "\n\n".join(chunks) if chunks else "(no company context files found)"

    def list_files(self) -> List[Tuple[str, int]]:
        return [(f.relative_to(self._root).as_posix(), f.stat().st_size)
                for f in sorted(self._root.rglob("*.md"))]
