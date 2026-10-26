"""
File tool — scoped read access to the company_context/ directory.

Writes are deliberately not supported. The agent should never dump arbitrary
files; evidence collection goes through the audit log instead.
"""
from __future__ import annotations

from pathlib import Path

from .base import Tool, ToolResult, ToolSpec

FILE_SPEC = ToolSpec(
    name="read_file",
    description="Read a text file from the company_context/ directory (read-only).",
    parameters={"path": "path relative to company_context/, e.g. 'context.md' or 'policies/payment.md'"},
)


class FileTool(Tool):
    def __init__(self, root: Path):
        super().__init__(FILE_SPEC)
        self._root = root.resolve()

    def call(self, **kwargs) -> ToolResult:
        rel = (kwargs.get("path") or "").strip()
        if not rel:
            return ToolResult.failure("missing `path`")
        full = (self._root / rel).resolve()
        # path traversal guard
        try:
            full.relative_to(self._root)
        except ValueError:
            return ToolResult.failure("path escapes company_context/")
        if not full.exists() or not full.is_file():
            return ToolResult.failure(f"not found: {rel}")
        text = full.read_text(encoding="utf-8")
        return ToolResult.success(
            observation=f"read {rel} ({len(text)} chars):\n{text[:2000]}{'…' if len(text) > 2000 else ''}",
            data={"path": rel, "content": text},
        )
