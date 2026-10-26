# Acme Corp — Company Context

This file is the agent's **semantic memory**. The planner loads it before every
run so the agent knows how this organisation operates, which systems exist,
and which credentials/policies apply. In a real deployment this would be a
versioned, access-controlled store (vector DB + structured facts). For the
prototype it is a single Markdown file.

## Systems

- **Globex Vendor Portal** — third-party portal where suppliers upload invoices.
  - URL: http://127.0.0.1:5101
  - Credentials for the shared service account:
    - username: `acme-ops`
    - password: `sandbox123`
  - Invoices can be filtered by vendor name on the dashboard.

- **Acme AP System** — Acme's internal accounts-payable platform.
  - URL: http://127.0.0.1:5102
  - No login required inside the corporate network.
  - New entries go to "Pending Review" status until a human approves.

## Known vendors

- Globex Industries
- Initech Supplies
- Soylent Logistics
- Umbrella Analytics

## Policies

1. Every vendor invoice must be mirrored into the AP system within 48 hours of
   receiving it in the vendor portal.
2. The amount, invoice ID, vendor name, and due date must match **exactly**
   between the two systems. Any mismatch is a verification failure.
3. The agent must **not** mark an invoice as paid in the vendor portal — only
   Finance can do that. Reading is fine; writes to the vendor portal require
   human approval.
4. Only **Open** invoices should be mirrored. Already-paid invoices are
   skipped.
5. If the AP system reports a duplicate (same vendor + invoice id), the agent
   should treat the task as already complete rather than retrying.

## Terminology

- "Latest" invoice = the one with the most recent `issued` date.
- "Mirror" = copy from vendor portal into the AP system.
