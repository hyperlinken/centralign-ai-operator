"""
Runs both mock servers in-process on separate threads so a developer can
spin up the whole sandbox with: python sandbox/run_sandbox.py

Vendor portal: http://127.0.0.1:5101
AP system:     http://127.0.0.1:5102
"""
from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vendor_portal.app import app as vendor_app  # noqa: E402
from ap_system.app import app as ap_app  # noqa: E402


def _run(app, port):
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


def main():
    t1 = threading.Thread(target=_run, args=(vendor_app, 5101), daemon=True)
    t2 = threading.Thread(target=_run, args=(ap_app, 5102), daemon=True)
    t1.start()
    t2.start()
    print("Vendor portal: http://127.0.0.1:5101")
    print("AP system:     http://127.0.0.1:5102")
    print("Press Ctrl+C to stop.")
    try:
        t1.join()
        t2.join()
    except KeyboardInterrupt:
        print("\nShutting down sandbox.")


if __name__ == "__main__":
    main()
