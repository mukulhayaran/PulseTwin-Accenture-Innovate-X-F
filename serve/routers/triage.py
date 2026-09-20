"""Agentic triage: trigger a run, poll it, read the verified finding.

The heavy half (`genai/`) is spawned as a DETACHED SUBPROCESS exactly as
`serve/routers/admin.py` spawns `mlops.retrainer` -- `genai` is not on serve/'s import
allowlist and must never be (CLAUDE.md §2 / ADR-036). "Trigger heavy, don't host it."

`tests/test_serve_imports.py` needed NO edit to cover this: `genai` is absent from its
ALLOWED set, so any `import genai` here would already fail the wall test. The wall held
without being widened, which is the point.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from serve import db
from serve.middleware.audit import log_action
from serve.middleware.auth import require_role

router = APIRouter()
ROOT = Path(__file__).resolve().parents[2]

ROLE_ORDER = ("supervisor", "manager", "leadership", "system")
MAX_QUESTION_CHARS = 500

# Mirrors genai/client.py::credential_present(). Duplicated rather than imported for the
# same import-wall reason admin.py duplicates model_registry's SQL -- keep the two in
# sync; a change to one without the other is a bug.
API_KEY_ENV = "XAI_API_KEY"


def _jobs_dir() -> Path:
    base = Path(os.environ.get("PULSETWIN_JOBS_DIR", tempfile.gettempdir()))
    return base / "pulsetwin_triage_runs"


def _credential_present() -> bool:
    return bool(os.environ.get(API_KEY_ENV, "").strip())


def _cap_role(requested: str | None, caller: str) -> str:
    """Never let a run act above the role that triggered it.

    Without this a `manager` could start a run acting as `leadership` and read economics
    they cannot read directly -- privilege escalation through the agent. Capping turns a
    restricted run into a FEATURE: the agent reports what it could not read, rather than
    estimating it.
    """
    if requested is None or requested not in ROLE_ORDER:
        return caller
    return requested if ROLE_ORDER.index(requested) <= ROLE_ORDER.index(caller) else caller


def _loads(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


class TriageRequest(BaseModel):
    plant_id: str = "plant_a"
    question: str = Field(min_length=4, max_length=MAX_QUESTION_CHARS)
    acting_role: str | None = None
    mode: str = Field(default="auto", pattern="^(auto|agent|deterministic)$")


@router.get("/api/triage/availability")
def availability() -> dict[str, Any]:
    """Ungated, like /api/health: the one call every client can always make.

    Reports credential PRESENCE only -- never the value, a prefix, or its length.
    """
    if _credential_present():
        return {"enabled": True, "reason": "ok", "mode": "agent"}
    return {"enabled": True, "reason": "no_provider_credential", "mode": "deterministic"}


@router.post("/api/triage/runs")
def start_run(req: TriageRequest, request: Request,
              role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    config_path = ROOT / "configs" / f"{req.plant_id}.yaml"
    if not config_path.exists():
        raise HTTPException(404, f"unknown plant '{req.plant_id}'")
    if any(ord(c) < 32 for c in req.question):
        raise HTTPException(422, "question must contain no control characters")

    acting = _cap_role(req.acting_role, role)
    mode = req.mode
    if mode == "auto":
        mode = "agent" if _credential_present() else "deterministic"
    if mode == "agent" and not _credential_present():
        raise HTTPException(503, f"{API_KEY_ENV} is not configured")

    run_id = secrets.token_hex(8)
    ops = request.app.state.ops

    # Insert BEFORE spawning so a run is never invisible, even if the worker dies.
    db.execute(
        ops,
        """INSERT INTO triage_runs
           (run_id, plant_id, question, requested_by, acting_role, mode, status)
           VALUES (?, ?, ?, ?, ?, ?, 'queued')""",
        (run_id, req.plant_id, req.question, role, acting, mode),
    )

    port = request.url.port or 7860
    api_base = os.environ.get("PULSETWIN_API_BASE", f"http://127.0.0.1:{port}")

    # A list argv never goes through a shell, so the question carries no injection
    # surface; the length and control-character caps above are defence in depth.
    # The child's stdio goes to a per-run log beside its heartbeat, NOT to DEVNULL.
    # A detached worker whose failures vanish is precisely how this repo ended up with
    # a container nobody had run: if the job dies on import, the only evidence is the
    # row staying `queued` forever, with nothing to read.
    jobs = _jobs_dir()
    jobs.mkdir(parents=True, exist_ok=True)
    log_path = jobs / f"{run_id}.log"
    try:
        log = open(log_path, "w", encoding="utf-8")  # noqa: SIM115 - owned by the child
    except OSError:
        log = None

    subprocess.Popen(
        [sys.executable, "-m", "genai.triage",
         "--plant", req.plant_id, "--question", req.question,
         "--run-id", run_id, "--api-base", api_base,
         "--role", acting, "--requested-by", role, "--mode", mode],
        cwd=str(ROOT),
        env={**os.environ, "OMP_NUM_THREADS": "1", "PYTHONPATH": str(ROOT)},
        stdout=log or subprocess.DEVNULL,
        stderr=subprocess.STDOUT if log else subprocess.DEVNULL,
    )

    log_action(request, ops, role, "triage_run_started", "triage", run_id,
               {"plant_id": req.plant_id, "acting_role": acting, "mode": mode})
    return {"run_id": run_id, "status": "queued", "mode": mode, "acting_role": acting}


@router.get("/api/triage/runs")
def list_runs(request: Request, plant_id: str = "plant_a", limit: int = 10,
              role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    limit = max(1, min(int(limit), 50))
    rows = db.query_all(
        request.app.state.ops,
        """SELECT run_id, created_at, plant_id, question, status, mode, acting_role,
                  claims_total, claims_verified, tool_calls, latency_s, cost_usd
           FROM triage_runs WHERE plant_id = ?
           ORDER BY created_at DESC LIMIT ?""",
        (plant_id, limit),
    )
    return {"runs": rows, "count": len(rows)}


@router.get("/api/triage/runs/{run_id}")
def get_run(run_id: str, request: Request, include_evidence: bool = False,
            role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    ops = request.app.state.ops
    row = db.query_one(ops, "SELECT * FROM triage_runs WHERE run_id = ?", (run_id,))
    if row is None:
        return {"run_id": run_id, "status": "unknown"}

    out = dict(row)
    # Only the POST-REDACTION finding is ever served. `finding_raw` stays in the database
    # for audit and is deliberately not returned: a redacted claim that reaches a screen
    # has not been redacted.
    out.pop("finding_raw", None)
    out["finding"] = _loads(row.get("finding"))
    out["verification"] = _loads(row.get("verification"))

    calls = db.query_all(
        ops,
        """SELECT call_id, seq, tool_name, arguments, result, duration_ms
           FROM triage_tool_calls WHERE run_id = ? ORDER BY seq""",
        (run_id,),
    )
    out["tool_ledger"] = [
        {
            "call_id": c["call_id"], "seq": c["seq"], "tool_name": c["tool_name"],
            "arguments": _loads(c["arguments"]), "duration_ms": c["duration_ms"],
            **({"result": _loads(c["result"])} if include_evidence else {}),
        }
        for c in calls
    ]

    # While the worker is still going the DB row has no ledger yet -- merge the
    # heartbeat so the panel shows tool calls filling in rather than 30s of spinner.
    if row.get("status") in ("queued", "running") and not calls:
        try:
            hb = json.loads((_jobs_dir() / f"{run_id}.json").read_text(encoding="utf-8"))
            out["tool_ledger"] = hb.get("tools", [])
        except (OSError, ValueError):
            pass
    return out
