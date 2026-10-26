# Design decisions

Each decision below includes the alternatives I considered and why I chose
this path. The rubric this is written against is the CentrAlign AI Founding
Engineer evaluation criteria (Autonomy, Execution, Reliability, Verification,
Generalisation, Engineering Quality, Product Thinking, Technical Understanding).

---

## 1. No agent framework

**Decision:** build the planner → executor → verifier loop by hand; no
LangChain, LlamaIndex, AutoGen, CrewAI, or provider-native "agents" API.

**Why:**
- The JD specifically asks for first-principles reasoning about autonomy. A
  framework hides exactly the control flow the interview will probe.
- Frameworks lock the project to one abstraction (chains, DAGs, graphs); the
  requirements will evolve and the control loop needs to too.
- Debugging a stuck agent inside a framework usually means reading someone
  else's prompt templates. Here every string the model sees is one `grep`
  away.
- The resulting runtime is ~250 lines in `aiops/runtime.py`. That's a cost
  I can afford for the clarity.

**Trade-off:** I rebuild things frameworks give you (tool registry, retry,
provider adapter). But each piece is explicit and testable, and nothing
drags in a tree of transitive dependencies.

---

## 2. Re-plan every step instead of frozen multi-step plans

**Decision:** On every tick the planner receives the full working memory
and returns a single next tool call. It also returns an "updated plan" for
transparency, but only the next step is executed.

**Why:**
- Enterprise UIs return unexpected states constantly (duplicate warnings,
  session timeouts, A/B'd layouts). A plan written at step 0 is stale by
  step 3.
- Observations are the ground truth. The LLM should use them.
- It makes failure recovery free: a failed step just shows up in the
  trace and the next call sees it.

**Trade-off:** More LLM calls per task (one per step). Mitigated by using
small/fast models (`gpt-4o-mini`, `gemini-flash`) and short prompts (the
trace is capped at the last 12 steps).

---

## 3. One `browser` tool with an `action` discriminator (not one tool per action)

**Decision:** `browser` takes an `action ∈ {goto, click, fill, read_page,
read_elements, press, wait_for, screenshot}`.

**Why:**
- Keeps the tool spec block in the planner prompt small. Each extra tool
  pushes the registry list longer and crowds out semantic context.
- The browser is one stateful resource (one tab). Modelling eight tools
  that all mutate the same hidden state is misleading.
- Scales — adding a new action means adding one `if action == ...` branch,
  not a new class.

**Trade-off:** The `args` schema is union-typed; the planner can pass
`url` to `click` and we have to validate at runtime. In practice LLMs
handle this fine when the parameter table explicitly says "(goto) url".

---

## 4. Verifier is an *independent* LLM call that reads the system of record

**Decision:** After the agent calls `finish`, a separate `Verifier` object
fetches the system-of-record state (AP system's `/api/entries` in the
sandbox) and runs a strict-prompted LLM judge over `(goal, claimed_summary,
claimed_evidence, ground_truth)`.

**Why:**
- The JD specifically asks: "Does the system determine whether the
  requested outcome was actually achieved?" The planner saying "I did it"
  is not evidence.
- Prevents the common failure where an agent hallucinates completion
  because the final page just happened to look like success.
- Keeping the verifier prompt distinct means the planner can't learn to
  satisfy its own grader.
- Returns one of `{succeeded, partial, failed}` with explicit field-level
  mismatches, so partial successes are visible rather than being flattened
  into "pass".

**Trade-off:** Costs one extra LLM call per run. Negligible compared to
the cost of a wrong result in production AP data.

---

## 5. Memory split into three layers

**Decision:** `WorkingMemory` (per-run), `SemanticMemory` (company
context), `EpisodicMemory` (past runs + lessons).

**Why:**
- They have different lifetimes, access patterns, and threat models:
  - working memory is ephemeral and large,
  - semantic memory is permanent and compact (per deployment),
  - episodic memory is permanent and append-only.
- Keeps the planner prompt tidy: one block per layer.
- Allows swapping each independently (semantic → vector DB, episodic →
  object store).

**Trade-off:** For a 2-week prototype three classes is more than you need.
I'd pay a small complexity tax for a sketch of production shape.

---

## 6. Semantic memory loaded whole (prototype) with pluggable retrieval later

**Decision:** `SemanticMemory.load_all()` concatenates every `.md` under
`company_context/`.

**Why:**
- One-page policies fit in context. Modern model context windows (200k+)
  easily absorb a handful of docs.
- Retrieval adds moving parts (embedding model, store, chunking) that are
  not worth building until there is content to retrieve.
- The planner prompt builder treats the semantic block as an opaque
  string, so dropping in a `retrieve(query, k)` later changes nothing
  about the loop.

**Trade-off:** Breaks when policies exceed ~10 pages. A clean upgrade
path exists.

---

## 7. HITL approval as a tool, not a hidden runtime hook

**Decision:** `request_approval` is a regular tool the planner can
choose to call. The runtime does *not* intercept writes to auto-prompt.

**Why:**
- Puts the decision ("is this sensitive?") where it belongs — in the
  reasoning layer with full context. A naive "block all writes" rule is
  wrong in both directions (over-blocks reads that are gated, misses
  writes via indirect routes).
- Lets the agent batch approvals ("I'm about to submit the form AND
  update status — approve both").
- Makes HITL visible in the trace and audit log.

**Trade-off:** The agent could decide not to request approval. Mitigated
by (a) the policy explicitly saying "writes to vendor portal require
human approval" in semantic memory, and (b) the planner's system prompt
telling it to prefer reads.

---

## 8. Append-only JSONL + SQLite audit log

**Decision:** every runtime event lands in both `events.jsonl` (per run)
and a shared `audit.sqlite`.

**Why:**
- JSONL is `tail -f`-able during a demo. SQLite enables cross-run
  queries ("all failed observations in the last 50 runs where
  tool=browser").
- No external dependency — SQLite ships with Python.
- Append-only semantics mean a crash mid-step leaves the trace intact.

**Trade-off:** Not tamper-evident. Production needs object storage +
signed audit chains.

---

## 9. Fixed sandbox "today" for reproducibility

**Decision:** `vendor_portal/app.py` fixes `_today = 2026-10-26`.

**Why:**
- Demos must be reproducible. "The latest invoice" depends on the clock;
  freezing the clock keeps the demo stable across days.
- Reviewers running the project a week later see the same answer.

**Trade-off:** The sandbox doesn't exercise date-math brittleness. A
real deployment would.

---

## 10. Narrow task over broad simulation

**Decision:** one end-to-end workflow (invoice mirroring) that genuinely
runs end-to-end, over a wider demo that only works for one scripted
input.

**Why:**
- The JD states this explicitly: *"A narrow system that genuinely
  demonstrates autonomy is considerably more valuable than a broad
  prototype that only simulates it."*
- Depth makes the architecture decisions real. Breadth tempts you to
  hard-code.
- Variation lives in the goal — "latest Globex invoice", "newest Soylent
  invoice", "most-overdue invoice" all work with no code changes because
  the planner interprets natural language against company context.

**Trade-off:** The demo doesn't show desktop apps or files. If the
reviewer asks for those, they'd be new `Tool` subclasses — ~100 lines
each — not architecture changes.

---

## Known limitations

- **No desktop RPA.** Browser only. Adding `pyautogui`-backed `desktop`
  tool is straightforward but was outside the time budget.
- **No parallelism.** One task per CLI invocation. Multi-tenant / queue
  model is a future addition.
- **Credentials in `context.md`.** Fine for a sandbox demo. Production
  needs a vault + per-request short-lived tokens.
- **Verifier is coupled to the AP system's API.** Each system of record
  needs its own verifier connector.
- **Approval gate is CLI stdin.** Non-interactive deployments need Slack/
  email/webhook.
- **Audit log is local filesystem** — not tamper-evident, not replicated.
- **Semantic retrieval not implemented.** `context.md` is loaded whole.
- **No cost accounting.** Each run makes 5-15 LLM calls (planner per step,
  verifier, episodic lesson). Needs a per-run budget cap.
- **Playwright timeout is a flat 10s.** Should be per-tool configurable.
- **Episodic retrieval uses keyword overlap.** Fine at low volume; needs
  embeddings at scale.
