"""
Minimal smoke tests. These do NOT touch the LLM — they verify the
scaffolding is wired correctly.

Run with: pytest tests/
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "sandbox"))


def test_sandbox_vendor_portal_login():
    from vendor_portal.app import app
    with app.test_client() as c:
        r = c.post("/login", data={"username": "acme-ops", "password": "sandbox123"},
                   follow_redirects=True)
        assert r.status_code == 200
        assert b"Globex Industries" in r.data


def test_sandbox_vendor_portal_rejects_bad_creds():
    from vendor_portal.app import app
    with app.test_client() as c:
        r = c.post("/login", data={"username": "x", "password": "y"})
        assert b"Invalid credentials" in r.data


def test_sandbox_ap_system_round_trip():
    from ap_system.app import app
    with app.test_client() as c:
        c.post("/api/reset")
        r = c.post("/new", data={
            "vendor": "Globex Industries", "invoice_id": "INV-2041",
            "amount": "4820.50", "due_date": "2026-11-09", "notes": "pytest",
        }, follow_redirects=True)
        assert r.status_code == 200
        entries = c.get("/api/entries").get_json()["entries"]
        assert len(entries) == 1
        assert entries[0]["invoice_id"] == "INV-2041"
        assert entries[0]["amount"] == 4820.50
        c.post("/api/reset")


def test_tool_registry_rendering():
    from aiops.tools import ToolRegistry
    from aiops.tools.finish import FinishTool
    reg = ToolRegistry()
    reg.register(FinishTool())
    block = reg.spec_prompt()
    assert "finish" in block
    assert "summary" in block


def test_working_memory_render():
    from aiops.memory import WorkingMemory, Step
    m = WorkingMemory("test goal")
    m.record_step(Step(index=1, thought="t", tool="browser",
                       args={"action": "goto"}, ok=True, observation="done"))
    out = m.render_for_planner()
    assert "test goal" in out
    assert "step 1" in out


def test_episodic_similar_retrieval():
    import tempfile
    from aiops.memory import EpisodicMemory
    from aiops.memory.episodic import Episode
    with tempfile.TemporaryDirectory() as d:
        mem = EpisodicMemory(Path(d))
        mem.record(Episode(run_id="a", goal="Mirror a Globex invoice",
                           outcome="succeeded", summary="ok",
                           lessons=["prefer reading before writing"]))
        mem.record(Episode(run_id="b", goal="Order lunch",
                           outcome="failed", summary="n/a"))
        sims = mem.similar("mirror the latest Globex invoice")
        assert sims and sims[0].run_id == "a"


def test_llm_extract_json_from_fences():
    from aiops.llm.adapter import LLM
    out = LLM._extract_json('```json\n{"x": 1, "y": [2,3]}\n```')
    assert out == {"x": 1, "y": [2, 3]}


def test_file_tool_path_traversal_guard():
    from aiops.tools.files import FileTool
    t = FileTool(root=ROOT / "company_context")
    r = t.call(path="../README.md")
    assert not r.ok
    assert "escape" in (r.error or "")
