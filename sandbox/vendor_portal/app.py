"""
Mock Vendor Portal — simulates a third-party supplier's billing portal.

Flows the agent must handle:
  1. Login screen (username + password)
  2. Dashboard listing invoices with filters by vendor name
  3. Invoice detail page with amount, due date, line items

Credentials are intentionally in context memory (not hard-coded in the agent).
"""
from __future__ import annotations

from datetime import date, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, abort

app = Flask(__name__)
app.secret_key = "sandbox-vendor-portal-key"

USERS = {"acme-ops": "sandbox123"}

_today = date(2026, 10, 26)

INVOICES = [
    {
        "id": "INV-2041",
        "vendor": "Globex Industries",
        "amount": 4820.50,
        "due_date": (_today + timedelta(days=14)).isoformat(),
        "issued": (_today - timedelta(days=3)).isoformat(),
        "status": "Open",
        "line_items": [
            {"desc": "Industrial coolant, 55 gal drum", "qty": 2, "unit": 1850.00},
            {"desc": "Hazmat handling fee", "qty": 1, "unit": 1120.50},
        ],
    },
    {
        "id": "INV-2039",
        "vendor": "Globex Industries",
        "amount": 1200.00,
        "due_date": (_today - timedelta(days=10)).isoformat(),
        "issued": (_today - timedelta(days=40)).isoformat(),
        "status": "Paid",
        "line_items": [{"desc": "Monthly service retainer", "qty": 1, "unit": 1200.00}],
    },
    {
        "id": "INV-7781",
        "vendor": "Initech Supplies",
        "amount": 342.17,
        "due_date": (_today + timedelta(days=5)).isoformat(),
        "issued": (_today - timedelta(days=25)).isoformat(),
        "status": "Open",
        "line_items": [{"desc": "Office supplies — Q4 restock", "qty": 1, "unit": 342.17}],
    },
    {
        "id": "INV-5520",
        "vendor": "Soylent Logistics",
        "amount": 9980.00,
        "due_date": (_today + timedelta(days=21)).isoformat(),
        "issued": (_today - timedelta(days=1)).isoformat(),
        "status": "Open",
        "line_items": [{"desc": "LTL shipment, 4 pallets", "qty": 4, "unit": 2495.00}],
    },
]


def _requires_login():
    return session.get("user") is None


@app.route("/")
def index():
    if _requires_login():
        return redirect(url_for("login"))
    return redirect(url_for("dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "").strip()
        if USERS.get(u) == p:
            session["user"] = u
            return redirect(url_for("dashboard"))
        error = "Invalid credentials"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("login"))


@app.route("/dashboard")
def dashboard():
    if _requires_login():
        return redirect(url_for("login"))
    vendor_filter = request.args.get("vendor", "").strip().lower()
    rows = [i for i in INVOICES if (not vendor_filter) or vendor_filter in i["vendor"].lower()]
    rows = sorted(rows, key=lambda r: r["issued"], reverse=True)
    return render_template("dashboard.html", invoices=rows, vendor_filter=vendor_filter)


@app.route("/invoice/<invoice_id>")
def invoice_detail(invoice_id):
    if _requires_login():
        return redirect(url_for("login"))
    inv = next((i for i in INVOICES if i["id"] == invoice_id), None)
    if not inv:
        abort(404)
    return render_template("invoice.html", inv=inv)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5101, debug=False)
