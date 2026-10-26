# Architecture

## The loop

Everything in this prototype is organised around a single control loop:

```
Goal
  │
  ▼
┌──────────┐
│ Planner  │ ← semantic context + episodic hints + working memory
└────┬─────┘
     │  decision: {thought, tool, args, updated_plan, set_facts}
     ▼
┌──────────┐
│ Executor │ → ToolRegistry → Tool.call(**args) → ToolResult
└────┬─────┘
     │  observation + evidence
     ▼
┌─────────────────┐
│ Working Memory  │   append (thought, tool, args, ok, observation)
└────┬────────────┘
     │
     ▼
  loop until: tool == finish AND result.ok   OR   failures > N   OR   steps > N
     │
     ▼
┌──────────┐
│ Verifier │  reads system of record + LLM judge
└────┬─────┘
     │  verdict ∈ {succeeded, partial, failed}
     ▼
  Episodic log + RunResult
```

### Why re-plan every step

A frozen multi-step plan ("1. login, 2. click row, 3. ...") breaks the first
time the UI returns an unexpected state. By re-planning on each observation,
the agent can:

- recover from a failed selector by trying a different one next turn,
- notice policy violations surfaced by observation (e.g. "duplicate entry")
  and skip the retry,
- shorten the remaining plan when it realises some steps are unnecessary.

The LLM still produces a *plan* on every tick (and we store it), so the user
can watch the plan evolve. But only the *next step* is actually executed.

---

## Layers

### `aiops.runtime` — orchestrator

Thin. The intelligence lives in the planner and verifier; the runtime's job
is sequencing + bookkeeping:

- open an `AuditLog` and a `WorkingMemory` keyed on a run id
- build the `Planner` with the current semantic & episodic context
- loop: ask planner → dispatch to executor → append observation
- stop on `finish`, max_steps, or N consecutive failures
- hand the final claim to the `Verifier` for an independent check
- write an `Episode` to episodic memory with LLM-generated lessons

### `aiops.planner` — the brain

Two LLM calls:

1. **initial_plan(goal)** — returns a 3-7 step plan. Not executed, just
   persisted for user transparency.
2. **next_step(memory)** — returns a single `(thought, tool, args)`. The
   planner receives:
   - the goal,
   - the current plan,
   - all known facts,
   - a trimmed trace of recent steps,
   - the full tool spec block,
   - the full company semantic context,
   - episodic hints from similar past runs.

We deliberately avoid provider-native "function calling" so tool selection
logic is auditable and provider-portable.

### `aiops.executor` — dispatcher

Looks up the tool by name, calls it, and handles:

- unknown tool → clean failure (planner can re-plan)
- bad args (TypeError) → clean failure, no retry (planner mistake)
- unexpected tool crash → one retry with backoff

### `aiops.verifier` — independent outcome check

Two layers of defence against the "the agent said it worked" problem:

1. **Ground truth** — HTTP GET against the AP system's `/api/entries`. In
   production this is a connector per system of record.
2. **LLM judge** — a *second* LLM call with a strict system prompt that
   sees the goal, the agent's claim, and the ground truth and returns
   `succeeded | partial | failed` + reasoning + explicit mismatches.

The verifier is not the same prompt as the planner, so a flawed planner
cannot rubber-stamp its own output.

### `aiops.memory`

| Layer | Lifetime | Purpose |
|---|---|---|
| `WorkingMemory` | per run | goal, plan, known facts, trace of steps |
| `SemanticMemory` | permanent | company context, policies — loaded whole in prototype |
| `EpisodicMemory` | permanent | past runs + LLM-generated lessons, keyword-retrieved |

### `aiops.tools`

Every tool implements:

```python
class Tool:
    spec: ToolSpec           # name, description, params, requires_approval
    def call(**kwargs) -> ToolResult
    def close() -> None
```

A `ToolResult` always carries both a free-text `observation` (what the LLM
sees next turn) and a structured `data` dict + `evidence` dict (what the
auditor sees).

Built-in tools:

- **browser** — Playwright-driven Chromium with actions `goto / click /
  fill / press / wait_for / read_page / read_elements / screenshot`. Single
  shared tab, so state persists across calls. Every action captures a
  screenshot into the run's evidence dir.
- **read_file** — scoped read-only access to `company_context/` with path
  traversal guard.
- **ask_human** — pause and prompt a human when context is insufficient.
- **request_approval** — HITL gate for sensitive actions, bypassed with
  `--auto-approve`.
- **finish** — sentinel the agent uses to declare completion with a summary
  and structured evidence.

### `aiops.audit`

Every runtime event (`run_started`, `initial_plan`, `decision`,
`observation`, `verification`, `run_finished`, errors) is written to:

- `runs/<run_id>/events.jsonl` — human-tailable
- a shared `runs/audit.sqlite` — queryable across runs

Browser screenshots land under `runs/_evidence_scratch/` and are referenced
by path in each `observation` event's evidence block.

### `aiops.llm.adapter`

Provider-agnostic. Auto-detects based on which API key is set. Each
provider implementation returns a parsed JSON object (planner and verifier
both need structured output). Retries 5xx/429 with exponential backoff.

---

## Data flow for one step

```
┌────────────────────────────────────────────────────────────────┐
│ Planner prompt =                                               │
│   system = PLANNER_SYSTEM                                      │
│            + tool_spec_block (dynamic from ToolRegistry)       │
│            + semantic_context (company_context/*.md)           │
│            + episodic_hints  (keyword-matched past runs)       │
│   user   = memory.render_for_planner()                         │
│            + "pick the single next tool call"                  │
└────────────────────────────────────────────────────────────────┘
                            │
                            ▼
            JSON { thought, tool, args, plan, set_facts }
                            │
                            ▼
           Executor.run(tool, args) → ToolResult
                            │
                            ▼
       WorkingMemory.record_step(Step(…))     AuditLog.log("observation", …)
                            │
                            ▼
                         next loop
```

---

## Extension points

### Adding a new tool

```python
class SalesforceTool(Tool):
    def __init__(self, token):
        super().__init__(ToolSpec(
            name="salesforce",
            description="Query/mutate Salesforce. action=query|create|update.",
            parameters={"action": "...", "object": "...", "fields": "..."},
            requires_approval=True,  # all writes gated
        ))
        self._token = token

    def call(self, **kw) -> ToolResult:
        ...

registry.register(SalesforceTool(token=...))
```

No change to runtime, planner, executor, or verifier. The planner prompt
automatically includes the new tool's spec on the next run.

### Adding a new system of record for verification

Subclass `Verifier` or inject a verifier factory keyed on the goal domain.
The ground-truth fetch is the only piece that varies.

### Swapping the LLM provider

Set a different API key. No code change.

### Scaling semantic memory

Replace `SemanticMemory.load_all()` with a retrieval call. The planner's
prompt builder treats the semantic block as an opaque string, so a vector
query that returns top-k chunks slots in with no changes to the loop.

---

## Non-obvious invariants

- The agent **cannot** declare success without calling `finish` with
  structured `evidence`. The verifier then independently checks it. If the
  agent lies, the verdict is `failed`.
- The browser tool reuses a single tab for the lifetime of the run, so
  cookies and form state are preserved across LLM turns.
- `WorkingMemory.render_for_planner` truncates older steps but keeps the
  full tail. The LLM cares most about recent observations.
- Episodic lessons are generated by an LLM call *after* the run so they
  reflect the actual outcome, not the agent's live rationalisation.
- Audit entries are written *before* the next loop tick, so even if the
  process crashes you can replay what happened.
