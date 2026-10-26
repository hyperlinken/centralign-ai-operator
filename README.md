# CentrAlign AI Operator

> A prototype AI employee that turns a natural-language company request into
> completed work. It plans, drives a real browser, verifies the outcome, and
> returns evidence — all while persisting an audit trail and asking a human
> only when it is actually stuck.

Built against the CentrAlign AI Founding Engineer problem statement.

---

## Demo in one minute

```powershell
# 1. install
pip install -r requirements.txt
playwright install chromium

# 2. set an LLM key (any one is enough)
Copy-Item .env.example .env
# then edit .env — set ANTHROPIC_API_KEY, OPENAI_API_KEY, or GEMINI_API_KEY

# 3. start the sandbox (two mock corporate systems)
python sandbox/run_sandbox.py
# leave running — vendor portal on :5101, AP system on :5102

# 4. in another terminal, run the operator
python -m aiops.cli --reset-ap "Mirror the latest open invoice from Globex Industries from the vendor portal into our AP system."
```

The browser window will open; you can watch the agent sign in, filter, scrape
the invoice, open the AP form, type the fields, submit, and verify the entry
exists in the system of record. The run finishes with a JSON summary and a
verifier verdict (`succeeded` / `partial` / `failed`).

---

## What this prototype demonstrates

Mapped to the Founding Engineer evaluation rubric:

| JD criterion | Where it shows up |
|---|---|
| **Autonomy** — determine & execute meaningful next actions | `aiops/planner.py` re-plans every tick from working memory |
| **Execution** — actually perform work | `aiops/tools/browser.py` drives Chromium via Playwright |
| **Reliability** — handle unexpected states & failures | retry in `aiops/executor.py`, abort on 3 consecutive failures in `aiops/runtime.py` |
| **Verification** — did the outcome happen? | `aiops/verifier.py` queries the system of record + LLM judge |
| **Generalization** — reusable across tasks | `aiops/tools/base.py` + `ToolRegistry` — new tools plug in, runtime unchanged |
| **Engineering Quality** — architecture & code quality | strict layering, no framework glue, every call is audit-logged |
| **Product Thinking** — focused on user outcome | agent must emit `finish` with evidence; verifier reads back from the real system |
| **Technical Understanding** | see [ARCHITECTURE.md](ARCHITECTURE.md) and [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md) |

---

## The task the agent runs

A deliberately narrow but genuinely autonomous workflow:

> *Mirror an invoice from a third-party vendor portal into our internal AP system.*

To do this the agent must:

1. Read company context (which systems, credentials, policies) from the
   semantic memory file.
2. Open the vendor portal in a browser, sign in with the stored service
   account credential.
3. Filter the invoice list by vendor name.
4. Pick the "latest" open invoice (ignoring already-paid rows — this is a
   policy rule, not hard-coded).
5. Open the invoice detail page and scrape vendor, invoice id, amount, due
   date.
6. Open the internal AP system, fill the new-entry form, submit it.
7. Request human approval before submitting (HITL gate — can be auto-approved
   with `--auto-approve` for CI).
8. Confirm the AP system assigned an ID (visible evidence) and call `finish`.
9. The independent verifier queries the AP system's `/api/entries` and asks
   a second LLM to judge whether the ground truth matches the claim.

Try variations:

```powershell
python -m aiops.cli --reset-ap "Record the newest Soylent Logistics invoice in AP."
python -m aiops.cli --reset-ap "Enter the Initech invoice that's due soonest into our AP system."
```

---

## Architecture at a glance

```
            ┌──────────────────────────────────────┐
            │               Runtime                │   Goal → Plan → Execute →
            │   (aiops/runtime.py, orchestrator)   │   Observe → Adapt → Verify
            └──┬────────┬────────┬────────┬────────┘
               │        │        │        │
      ┌────────▼──┐  ┌──▼─────┐ ┌▼───────┐ ┌▼────────┐
      │  Planner  │  │Executor│ │Verifier│ │  Audit  │
      │ (LLM)     │  │        │ │ (LLM)  │ │ (JSONL+ │
      └────┬──────┘  └───┬────┘ └────┬───┘ │  SQLite)│
           │             │           │     └─────────┘
   ┌───────▼─────┐   ┌───▼─────────────▼───┐
   │   Memory    │   │   Tool Registry      │
   │ ┌─────────┐ │   │ ┌──────────────────┐ │
   │ │Working  │ │   │ │ browser (Playwr.)│ │
   │ │Semantic │ │   │ │ read_file        │ │
   │ │Episodic │ │   │ │ ask_human        │ │
   │ └─────────┘ │   │ │ request_approval │ │
   └─────────────┘   │ │ finish           │ │
                     │ └──────────────────┘ │
                     └──────┬───────────────┘
                            │
                 ┌──────────▼──────────┐
                 │  Sandbox / Prod     │
                 │  Vendor portal      │
                 │  AP system          │
                 └─────────────────────┘
```

For the full breakdown read [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Project layout

```
centralign-operator/
├── aiops/                      # the agent runtime (import as `aiops`)
│   ├── runtime.py              # orchestrator
│   ├── planner.py              # LLM-driven step chooser
│   ├── executor.py             # tool dispatcher w/ retries
│   ├── verifier.py             # independent outcome check
│   ├── audit.py                # JSONL + SQLite event log
│   ├── cli.py                  # entry point
│   ├── llm/adapter.py          # Anthropic / OpenAI / Gemini
│   ├── memory/                 # working, semantic, episodic
│   └── tools/                  # browser, files, HITL, finish
├── sandbox/
│   ├── vendor_portal/          # Flask mock supplier portal
│   ├── ap_system/              # Flask mock AP system
│   └── run_sandbox.py          # starts both
├── company_context/            # agent's semantic memory
│   └── context.md
├── episodes/                   # episodic memory JSON files (populated at runtime)
├── runs/                       # per-run artifacts: screenshots, events.jsonl
├── demo/
│   └── scenarios.md
├── tests/
├── requirements.txt
├── .env.example
├── README.md
├── ARCHITECTURE.md
└── DESIGN_DECISIONS.md
```

---

## Configuration

Env vars (see `.env.example`):

| Var | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` | pick one — auto-detected |
| `ANTHROPIC_MODEL` / `OPENAI_MODEL` / `GEMINI_MODEL` | model id override |
| `VENDOR_PORTAL_URL` | default `http://127.0.0.1:5101` |
| `AP_SYSTEM_URL` | default `http://127.0.0.1:5102` |
| `OPERATOR_HEADLESS` | `true` to hide the browser |
| `OPERATOR_MAX_STEPS` | hard cap on agent steps per run |
| `OPERATOR_HITL` | `false` disables the approval gate |

CLI flags override env vars. Full list: `python -m aiops.cli --help`.

---

## Known limitations

See [DESIGN_DECISIONS.md § Known Limitations](DESIGN_DECISIONS.md#known-limitations) — summary:

- Browser-only (no desktop RPA yet)
- Semantic memory loaded whole; needs retrieval + ACLs at scale
- Verifier reads the sandbox API; a real deployment needs per-system
  verifiers
- No parallelism — one task per CLI invocation
- Approval gate is CLI stdin; production needs Slack/email
- Audit log is local filesystem; needs object storage + tamper-evidence
- Costs: ~5–10 LLM calls per run depending on how fast the planner converges

---

## What I would build next

1. **Multi-tab / multi-session browser pool** so one agent can hold state in
   several enterprise systems simultaneously.
2. **Semantic retrieval over company context** (pgvector) with per-user and
   per-document ACLs — context.md doesn't scale past one page.
3. **Replay harness** — step through any past run's `events.jsonl`, pin a
   failing step, and re-run the planner against the pinned observation to
   iterate on the prompt without re-executing the whole task.
4. **Tool-level evals** — synthetic goal/expected-outcome pairs that gate
   releases, with the verifier as the automatic grader.
5. **Policy engine as a tool** — right now policies live in the planner's
   context prompt; promote them to a `check_policy(action)` tool that
   returns deny/allow/escalate so compliance is explicit.
6. **Learnable tool selection** — episodic memory already stores (goal,
   outcome, trace); a retrieval step can inject successful past traces into
   the planner's few-shots.
7. **Human control plane** — a tiny web UI over `runs/` that renders each
   run's trace + screenshots, lets reviewers approve pending actions
   remotely, and lets devs replay a step.

---

## Assumptions

- The target environment permits running a local Chromium instance.
- The LLM provider's API is reachable (no air-gap).
- Credentials passed through `company_context/context.md` are sandbox-only.
  Never commit real credentials; see `.env.example` for the API-key story.
- Dates in the sandbox are stable (fixed `_today = 2026-10-26` in the vendor
  portal) so demos are reproducible.

---

## Models, APIs, frameworks used

- **LLM** — any of Anthropic (default `claude-sonnet-4-5-20250929`), OpenAI
  (`gpt-4o-mini`), Google Gemini (`gemini-2.0-flash-exp`). Called over raw
  HTTP via `requests`; no SDK dependency.
- **Browser automation** — Playwright (Chromium).
- **Sandbox web apps** — Flask + Jinja templates.
- **Persistence** — JSONL + SQLite stdlib.
- **No agent framework** (no LangChain / LlamaIndex / AutoGen). The planning
  loop is written explicitly in `aiops/runtime.py` because being able to
  reason about and debug the control flow was a core requirement.
