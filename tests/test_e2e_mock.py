"""
End-to-end test with a scripted LLM.

Validates that the full Goal→Plan→Execute→Observe→Verify loop stitches
together: planner → executor → tool → working memory → verifier → episode.

The scripted LLM returns a canned sequence of decisions that drive the
agent through the mock vendor portal and AP system. No network calls.
"""
from __future__ import annotations

import threading
import time
import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "sandbox"))

from aiops.llm import LLM
from aiops.memory import EpisodicMemory, SemanticMemory
from aiops.runtime import Runtime
from aiops.tools import ToolRegistry
from aiops.tools.browser import BrowserTool
from aiops.tools.files import FileTool
from aiops.tools.finish import FinishTool
from aiops.tools.hitl import ApprovalTool, AskHumanTool
from aiops.verifier import Verifier


class ScriptedLLM(LLM):
    """Deterministic LLM that pops decisions off a FIFO."""
    name = "scripted"

    def __init__(self, script):
        self._script = list(script)
        self._lesson_calls = 0

    def chat_json(self, system, messages, *, schema_hint=None, max_tokens=2048, temperature=0.2):
        # The verifier call is a dict matching VERIFIER_SCHEMA.
        if "verifier" in system.lower() or "verdict" in (schema_hint or "").lower():
            return {"verdict": "succeeded",
                    "reasoning": "AP system shows a matching entry.",
                    "mismatches": []}
        # Lesson-generation call after the run.
        if "lesson" in system.lower():
            self._lesson_calls += 1
            return {"lessons": ["bootstrap-lesson"]}
        # Initial-plan call: planner asks for a plan with no trace yet.
        if schema_hint and "plan" in schema_hint and "tool" not in schema_hint:
            return {"plan": ["read context", "login portal", "find invoice",
                             "scrape", "submit AP", "verify"],
                    "clarifications_needed": []}
        # Step calls — pop next decision.
        if not self._script:
            raise RuntimeError("scripted LLM exhausted")
        return self._script.pop(0)


@pytest.fixture(scope="module")
def sandbox_servers():
    from vendor_portal.app import app as va
    from ap_system.app import app as aa
    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    t1 = threading.Thread(target=lambda: va.run(host="127.0.0.1", port=5201,
                                                debug=False, use_reloader=False),
                          daemon=True)
    t2 = threading.Thread(target=lambda: aa.run(host="127.0.0.1", port=5202,
                                                debug=False, use_reloader=False),
                          daemon=True)
    t1.start(); t2.start()
    for _ in range(30):
        try:
            requests.get("http://127.0.0.1:5201/", timeout=0.3)
            requests.get("http://127.0.0.1:5202/", timeout=0.3)
            break
        except Exception:
            time.sleep(0.2)
    requests.post("http://127.0.0.1:5202/api/reset", timeout=2)
    yield "http://127.0.0.1:5201", "http://127.0.0.1:5202"
    try:
        requests.post("http://127.0.0.1:5202/api/reset", timeout=2)
    except Exception:
        pass


def _step(tool, args, thought="t"):
    return {"thought": thought, "plan": ["continue"], "tool": tool,
            "args": args, "set_facts": {}}


def test_end_to_end_mirror_invoice(sandbox_servers, tmp_path):
    vendor_url, ap_url = sandbox_servers

    script = [
        _step("read_file", {"path": "context.md"}, "load company context"),
        _step("browser", {"action": "goto", "url": f"{vendor_url}/login"}),
        _step("browser", {"action": "fill", "selector": "#username", "value": "acme-ops"}),
        _step("browser", {"action": "fill", "selector": "#password", "value": "sandbox123"}),
        _step("browser", {"action": "click", "selector": "#submit-login"}),
        _step("browser", {"action": "fill", "selector": "#vendor-filter", "value": "Globex"}),
        _step("browser", {"action": "click", "selector": "#apply-filter"}),
        _step("browser", {"action": "click", "selector": "a[href='/invoice/INV-2041']"}),
        _step("browser", {"action": "read_page"}, "scrape invoice fields"),
        _step("browser", {"action": "goto", "url": f"{ap_url}/new"}),
        _step("browser", {"action": "fill", "selector": "#vendor", "value": "Globex Industries"}),
        _step("browser", {"action": "fill", "selector": "#invoice_id", "value": "INV-2041"}),
        _step("browser", {"action": "fill", "selector": "#amount", "value": "4820.50"}),
        _step("browser", {"action": "fill", "selector": "#due_date", "value": "2026-11-09"}),
        _step("request_approval", {"action_summary": "submit AP entry INV-2041",
                                   "detail": "amount 4820.50"}),
        _step("browser", {"action": "click", "selector": "#submit-entry"}),
        _step("browser", {"action": "read_page"}, "confirm AP entry"),
        _step("finish",
              {"summary": "Mirrored INV-2041 from Globex into AP as AP-1001.",
               "evidence": {"vendor": "Globex Industries", "invoice_id": "INV-2041",
                            "amount": 4820.50, "due_date": "2026-11-09",
                            "ap_entry_id": "AP-1001"}}),
    ]
    llm = ScriptedLLM(script)

    run_dir = tmp_path / "runs"
    evidence_dir = run_dir / "evidence"
    registry = ToolRegistry()
    browser = BrowserTool(run_evidence_dir=evidence_dir, headless=True)
    registry.register(browser)
    registry.register(FileTool(root=ROOT / "company_context"))
    registry.register(AskHumanTool())
    registry.register(ApprovalTool(auto_approve=True))
    registry.register(FinishTool())

    semantic = SemanticMemory(root=ROOT / "company_context")
    episodic = EpisodicMemory(root=tmp_path / "episodes")
    verifier = Verifier(llm=llm, ap_api_url=ap_url)

    runtime = Runtime(llm=llm, registry=registry, semantic=semantic,
                      episodic=episodic, verifier=verifier,
                      runs_root=run_dir, max_steps=30)

    try:
        result = runtime.execute(
            "Mirror the latest open invoice from Globex Industries into the AP system."
        )
    finally:
        registry.close_all()

    assert result.outcome == "succeeded", f"got {result.outcome}: {result.agent_summary}"
    assert result.agent_evidence.get("ap_entry_id") == "AP-1001"
    # Ground truth: AP system should have exactly one entry for INV-2041.
    entries = requests.get(f"{ap_url}/api/entries").json()["entries"]
    assert len(entries) == 1
    assert entries[0]["invoice_id"] == "INV-2041"
    assert abs(entries[0]["amount"] - 4820.50) < 1e-6
    # Audit log exists.
    events = (result.run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "run_started" in events and "verification" in events and "run_finished" in events
    # Episode recorded.
    assert episodic.all(), "episode should be recorded"
