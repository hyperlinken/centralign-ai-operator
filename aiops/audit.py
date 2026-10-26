"""
Audit log — append-only JSONL + SQLite for every runtime event.

Why both?
  - JSONL is dead-simple to tail with `tail -f` while debugging.
  - SQLite gives queryable "all steps where tool=browser and ok=false"
    joins later, which matters once we have more than a handful of runs.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


class AuditLog:
    def __init__(self, run_dir: Path, run_id: str):
        self._run_dir = run_dir
        self._run_id = run_id
        self._jsonl = run_dir / "events.jsonl"
        self._db_path = run_dir.parent / "audit.sqlite"
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self._db_path) as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            c.execute("CREATE INDEX IF NOT EXISTS ix_events_run ON events(run_id)")
            c.execute("CREATE INDEX IF NOT EXISTS ix_events_kind ON events(kind)")

    def log(self, kind: str, payload: Dict[str, Any]) -> None:
        event = {
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "run_id": self._run_id,
            "kind": kind,
            **payload,
        }
        line = json.dumps(event, default=str)
        with self._lock:
            with self._jsonl.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            with sqlite3.connect(self._db_path) as c:
                c.execute(
                    "INSERT INTO events(run_id, ts, kind, payload) VALUES (?,?,?,?)",
                    (self._run_id, event["ts"], kind, json.dumps(payload, default=str)),
                )
