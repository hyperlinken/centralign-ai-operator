# Demo scenarios

Each scenario below is a single CLI invocation. The sandbox state is reset
by `--reset-ap`.

## Scenario 1 — The happy path

**Goal:**
> Mirror the latest open invoice from Globex Industries from the vendor
> portal into our AP system.

**Expected trace:**
1. `read_file` → `context.md` to recall credentials and policies.
2. `browser goto` → `http://127.0.0.1:5101`.
3. `browser fill` × 2 → username, password.
4. `browser click` → sign in.
5. `browser fill` → vendor filter "Globex", `click` apply.
6. Agent reads the dashboard, identifies **INV-2041** as the latest *Open*
   invoice (INV-2039 is paid and skipped — this is the policy).
7. `browser click` on the invoice link.
8. `browser read_page` to scrape amount `4820.50`, due date `2026-11-09`.
9. `browser goto` → `http://127.0.0.1:5102/new`.
10. `browser fill` × 4 → vendor, invoice id, amount, due date.
11. `request_approval` → human approves.
12. `browser click` → submit.
13. `browser read_page` → confirms `AP-1001 Pending Review`.
14. `finish` with `evidence = { ap_entry_id: "AP-1001", amount: 4820.50, ... }`.
15. **Verifier** fetches `/api/entries`, LLM judge returns `succeeded`.

## Scenario 2 — Ambiguous "latest"

**Goal:**
> Record the Globex invoice that's due soonest into our AP system.

**What differs:** the agent must sort by `due_date` not `issued`. The
planner reads both fields from the page and picks correctly.

## Scenario 3 — Policy-driven skip

**Goal:**
> Mirror ALL open invoices into the AP system.

**What differs:** the agent iterates through the dashboard and, per policy
(§4 of `context.md`), skips rows with status `Paid`. Each iteration is a
mini-plan; episodic memory helps the second iteration be faster.

## Scenario 4 — Duplicate detection

**Goal:**
> Enter INV-2041 from Globex into AP.
> (Run twice without `--reset-ap`.)

**Expected:** second run observes the "already entered" error from the AP
system; per policy §5, the agent treats it as already-complete and calls
`finish` without retrying.

## Scenario 5 — Human in the loop fires

**Goal:**
> Mark the latest Globex invoice as paid in the vendor portal.

**Expected:** per policy §3, the agent requires human approval before any
write to the vendor portal. With default CLI the human can reject and the
agent cleanly reports a `rejected` outcome rather than looping.

## Scenario 6 — Recovery after bad selector

Simulated by renaming a selector in a template before running Scenario 1.
The browser tool returns a timeout observation; the planner tries
`read_page` next, learns the new UI, and recovers.
