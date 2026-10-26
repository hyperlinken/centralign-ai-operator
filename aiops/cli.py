"""
CLI entry point: `python -m aiops.cli "<goal>"`

Flags:
  --headless           run Chromium headless (default: visible so you can watch)
  --max-steps N        hard cap on agent steps (default 25)
  --auto-approve       bypass HITL approval gate (useful for CI)
  --reset-ap           wipe the AP system's entries before running
  --runs-dir PATH      where to write run artifacts (default ./runs)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("[setup] WARN python-dotenv not installed — .env will not be loaded. "
          "Run: pip install python-dotenv", file=sys.stderr)

from .llm import build_llm
from .memory import EpisodicMemory, SemanticMemory
from .runtime import Runtime
from .tools import ToolRegistry
from .tools.browser import BrowserTool
from .tools.files import FileTool
from .tools.finish import FinishTool
from .tools.hitl import ApprovalTool, AskHumanTool
from .verifier import Verifier


ROOT = Path(__file__).resolve().parent.parent


def _pretty_event(kind: str, payload: dict):
    tag = {
        "run_started": "\033[36mRUN\033[0m",
        "initial_plan": "\033[36mPLAN\033[0m",
        "decision": "\033[33mSTEP\033[0m",
        "observation": "\033[32mOBS\033[0m" if payload.get("ok") else "\033[31mOBS\033[0m",
        "verification": "\033[35mVERIFY\033[0m",
        "run_finished": "\033[36mDONE\033[0m",
        "planner_error": "\033[31mPLANNER-ERR\033[0m",
        "abort": "\033[31mABORT\033[0m",
        "verifier_error": "\033[31mVERIFIER-ERR\033[0m",
    }.get(kind, kind)
    if kind == "decision":
        print(f"  {tag} #{payload['step']}: {payload['tool']}  thought: {payload['thought']}")
    elif kind == "observation":
        status = "ok" if payload["ok"] else f"FAIL ({payload.get('error')})"
        obs = payload["observation"].replace("\n", " ")[:200]
        print(f"       {tag} {status} :: {obs}")
    elif kind == "initial_plan":
        print(f"  {tag}:")
        for i, s in enumerate(payload["plan"], 1):
            print(f"    {i}. {s}")
    elif kind == "verification":
        print(f"  {tag} {payload['verdict']} — {payload['reasoning']}")
        if payload.get("mismatches"):
            for m in payload["mismatches"]:
                print(f"       mismatch: {m}")
    elif kind == "run_started":
        print(f"\n{tag} goal={payload['goal']!r}")
    elif kind == "run_finished":
        print(f"{tag} outcome={payload['outcome']} steps={payload['steps']}\n")
    else:
        print(f"  {tag}: {json.dumps(payload)[:200]}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="CentrAlign AI Operator")
    parser.add_argument("goal", help="The natural-language goal to achieve")
    parser.add_argument("--headless", action="store_true", default=_envbool("OPERATOR_HEADLESS", False))
    parser.add_argument("--max-steps", type=int, default=int(os.getenv("OPERATOR_MAX_STEPS", "25")))
    parser.add_argument("--auto-approve", action="store_true", default=not _envbool("OPERATOR_HITL", True))
    parser.add_argument("--reset-ap", action="store_true", help="wipe AP entries before running")
    parser.add_argument("--runs-dir", type=Path, default=ROOT / "runs")
    args = parser.parse_args(argv)

    ap_url = os.getenv("AP_SYSTEM_URL", "http://127.0.0.1:5102")

    if args.reset_ap:
        try:
            requests.post(f"{ap_url}/api/reset", timeout=3).raise_for_status()
            print(f"[setup] AP system reset at {ap_url}")
        except Exception as e:
            print(f"[setup] WARN could not reset AP: {e}", file=sys.stderr)

    llm = build_llm()
    print(f"[setup] using LLM provider: {llm.name}")

    args.runs_dir.mkdir(parents=True, exist_ok=True)
    run_evidence_dir = args.runs_dir / "_evidence_scratch"
    run_evidence_dir.mkdir(parents=True, exist_ok=True)

    registry = ToolRegistry()
    browser = BrowserTool(run_evidence_dir=run_evidence_dir, headless=args.headless)
    registry.register(browser)
    registry.register(FileTool(root=ROOT / "company_context"))
    registry.register(AskHumanTool())
    registry.register(ApprovalTool(auto_approve=args.auto_approve))
    registry.register(FinishTool())

    semantic = SemanticMemory(root=ROOT / "company_context")
    episodic = EpisodicMemory(root=ROOT / "episodes")
    verifier = Verifier(llm=llm, ap_api_url=ap_url)

    runtime = Runtime(
        llm=llm, registry=registry, semantic=semantic, episodic=episodic,
        verifier=verifier, runs_root=args.runs_dir, max_steps=args.max_steps,
        on_event=_pretty_event,
    )

    try:
        # Point the browser tool at this specific run's evidence dir
        run_id_hint = None
        # (BrowserTool was created with a shared scratch dir; we move artifacts
        #  into the run dir at the end via the audit log paths.)
        result = runtime.execute(args.goal)
    finally:
        registry.close_all()

    print("================ RESULT ================")
    print(f"run_id:   {result.run_id}")
    print(f"outcome:  {result.outcome}")
    print(f"steps:    {result.steps}")
    print(f"summary:  {result.agent_summary}")
    if result.agent_evidence:
        print(f"evidence: {json.dumps(result.agent_evidence, indent=2)}")
    if result.verdict:
        print(f"verdict:  {result.verdict.verdict} — {result.verdict.reasoning}")
    print(f"artifacts: {result.run_dir}")
    print("========================================")

    sys.exit(0 if result.outcome == "succeeded" else 1)


def _envbool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


if __name__ == "__main__":
    main()
