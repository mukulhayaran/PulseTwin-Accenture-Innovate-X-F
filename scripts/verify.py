"""Master quality gate (LOOP.md). Run it with the project venv's python:

    .venv/Scripts/python scripts/verify.py          # fast tier: the gates an iteration needs
    .venv/Scripts/python scripts/verify.py --full   # everything: full pytest suite + build

`make` is not installed on this machine (CLAUDE.md §8), so this is plain python. The
frontend has no ESLint config; `tsc --noEmit` is its lint/type gate. `next build` wipes
.next under a running `next dev` -- stop dev first (CLAUDE.md §8).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
FAST_TESTS = ["tests/test_serve_imports.py", "tests/test_configs.py", "tests/test_qa_regressions.py",
              "tests/test_demo_reset.py", "tests/test_incident_commander.py"]

GATES = [
    # (name, tier, argv, cwd)
    ("backend-fast", "fast", [PY, "-m", "pytest", *FAST_TESTS, "-q", "-x"], ROOT),
    ("typecheck", "fast", ["npx", "tsc", "--noEmit", "-p", "."], ROOT / "frontend"),
    ("backend-full", "full", [PY, "-m", "pytest", "tests/", "-q"], ROOT),
    ("frontend-build", "full", ["npx", "next", "build"], ROOT / "frontend"),
]


def main() -> int:
    # A Windows console is cp1252: printing a build's '○' or a stage mark crashed the
    # gate itself, not the thing it was checking.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--keep-going", action="store_true")
    args = ap.parse_args()
    env = {**os.environ, "OMP_NUM_THREADS": "1", "PYTHONUTF8": "1"}
    results = []
    for name, tier, argv, cwd in GATES:
        if tier == "full" and not args.full:
            continue
        if name == "backend-fast" and args.full:
            continue                      # the full suite covers it
        t0 = time.time()
        print(f"==> {name}: {' '.join(str(a) for a in argv)}", flush=True)
        proc = subprocess.run(argv, cwd=cwd, env=env, shell=os.name == "nt" and argv[0] == "npx",
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        ok = proc.returncode == 0
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-15:]
        results.append({"gate": name, "status": "PASS" if ok else "FAIL",
                        "seconds": round(time.time() - t0), "tail": tail if not ok else tail[-2:]})
        print("\n".join(tail if not ok else tail[-2:]))
        print(f"<== {name}: {'PASS' if ok else 'FAIL'} ({results[-1]['seconds']} s)\n", flush=True)
        if not ok and not args.keep_going:
            break
    ok = bool(results) and all(r["status"] == "PASS" for r in results)
    (ROOT / ".loop").mkdir(exist_ok=True)
    with (ROOT / ".loop" / "history.jsonl").open("a", encoding="utf-8") as h:
        h.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "event": "verify",
                            "tier": "full" if args.full else "fast", "result": "PASS" if ok else "FAIL",
                            "gates": [(r["gate"], r["status"], r["seconds"]) for r in results]}) + "\n")
    print("RESULT", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
