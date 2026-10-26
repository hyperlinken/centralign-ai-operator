"""
Mock internal Accounts-Payable system.

The agent must enter invoice data here. The system persists entries to a
JSON file so verification can read them back.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, flash

app = Flask(__name__)
app.secret_key = "sandbox-ap-system-key"

DATA_FILE = Path(__file__).resolve().parent / "ap_entries.json"


def _load():
    if not DATA_FILE.exists():
        return []
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _save(entries):
    DATA_FILE.write_text(json.dumps(entries, indent=2), encoding="utf-8")


@app.route("/")
def index():
    return redirect(url_for("entries"))


@app.route("/entries")
def entries():
    rows = sorted(_load(), key=lambda r: r.get("created_at", ""), reverse=True)
    return render_template("entries.html", entries=rows)


@app.route("/new", methods=["GET", "POST"])
def new_entry():
    if request.method == "POST":
        vendor = request.form.get("vendor", "").strip()
        invoice_id = request.form.get("invoice_id", "").strip()
        amount_raw = request.form.get("amount", "").strip()
        due_date = request.form.get("due_date", "").strip()
        notes = request.form.get("notes", "").strip()

        errors = []
        if not vendor:
            errors.append("vendor is required")
        if not invoice_id:
            errors.append("invoice_id is required")
        try:
            amount = float(amount_raw)
            if amount < 0:
                errors.append("amount must be non-negative")
        except ValueError:
            errors.append("amount must be a number")
            amount = None
        try:
            datetime.strptime(due_date, "%Y-%m-%d")
        except ValueError:
            errors.append("due_date must be YYYY-MM-DD")

        existing = _load()
        if any(e["invoice_id"] == invoice_id and e["vendor"] == vendor for e in existing):
            errors.append(f"invoice {invoice_id} for {vendor} already entered")

        if errors:
            return render_template(
                "new.html",
                errors=errors,
                form={"vendor": vendor, "invoice_id": invoice_id, "amount": amount_raw,
                      "due_date": due_date, "notes": notes},
            )

        entry = {
            "id": f"AP-{len(existing) + 1001}",
            "vendor": vendor,
            "invoice_id": invoice_id,
            "amount": amount,
            "due_date": due_date,
            "notes": notes,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "status": "Pending Review",
        }
        existing.append(entry)
        _save(existing)
        return redirect(url_for("entry_detail", ap_id=entry["id"]))

    return render_template("new.html", errors=[], form={})


@app.route("/entry/<ap_id>")
def entry_detail(ap_id):
    entry = next((e for e in _load() if e["id"] == ap_id), None)
    if not entry:
        return render_template("entries.html", entries=_load(), missing=ap_id), 404
    return render_template("entry.html", entry=entry)


@app.route("/api/entries")
def api_entries():
    return {"entries": _load()}


@app.route("/api/reset", methods=["POST"])
def api_reset():
    _save([])
    return {"ok": True}


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5102, debug=False)
