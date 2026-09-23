"""Admin actions: trigger retraining, promote/rollback a model, poll job status. `system`
role only.

Heavy work (training: pandas + sklearn) is spawned as a DETACHED SUBPROCESS
(`python -m mlops.retrainer`) rather than imported into serve/ -- `mlops` is not on
serve/'s import allowlist (only `serve` is first-party there, CLAUDE.md SS2 / ADR-033).
"Precompute heavy, serve thin" extends to "trigger heavy, don't host it."

promote_model()/rollback_model() below duplicate the SQL in mlops/model_registry.py's
promote()/rollback() rather than importing that module, for the same import-wall reason.
Keep both copies in sync -- a change to one without the other is a bug.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from serve import db
from serve.middleware.audit import log_action
from serve.middleware.auth import require_role

router = APIRouter()
ROOT = Path(__file__).resolve().parents[2]


def _activate_scorer(request: Request, plant_id: str, artifact_path: str | None) -> dict[str, Any]:
    """Load a promoted model's .npz into the live scorer map -- the "hot-swap" half of
    promotion (docs/MLOPS_LIFECYCLE.md stage 7, and the `promote + hot-swap` node in its
    lifecycle diagram).

    Without this, promote/rollback only rewrote `model_registry` rows: a promoted
    challenger never scored a single request, and /api/predict/defect returned a
    byte-identical probability before and after promotion. The doc's claim that this
    endpoint "is the only code path that changes what /api/predict/defect actually serves"
    was true only in the sense that no code path did.

    Deliberately does not overwrite the committed `artifacts/model_<plant>.npz` -- that
    file is a build output under version control (ADR-027) and a demo action should not
    rewrite it. A restart no longer loses this promotion, though: `serve/main.py::lifespan`
    (ADR-041) asks `model_registry` for the current champion's `artifact_path` at startup
    and loads THAT, falling back to the committed artifact only if no champion row exists
    or its file is missing. This function's hot-swap and that startup lookup read the same
    row, so a promotion made once is what both a live process and its next restart serve.

    Never raises: a missing or corrupt artifact must not roll back a registry change that
    already committed. The outcome is reported to the caller either way.
    """
    scorers = getattr(request.app.state, "scorers", None)
    if scorers is None:
        return {"swapped": False, "reason": "scorer map unavailable"}
    if not artifact_path:
        return {"swapped": False, "reason": "registry row has no artifact_path"}
    path = Path(artifact_path)
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        return {"swapped": False, "reason": f"artifact missing: {path.name}"}
    try:
        from serve.scorer import Scorer
        scorers[plant_id] = Scorer(path)
    except Exception as e:  # noqa: BLE001 - any load failure must leave serving intact
        return {"swapped": False, "reason": f"could not load artifact: {e}"}
    # True since ADR-041: `lifespan` now loads whatever `model_registry` names as champion,
    # so a restart re-reads the SAME row this call just wrote rather than the committed
    # default. Verified against a real `docker kill` + restart of the backend container,
    # not just the unit tests. The one edge this doesn't cover -- the promoted artifact's
    # .npz being pruned off disk -- is guarded separately: mlops/retrainer.py keeps the 10
    # most recent challenger files per plant specifically so a live champion/rollback
    # target is never the one pruning deletes.
    return {"swapped": True, "artifact": path.name, "persists_across_restart": True}


def _utc_now() -> str:
    """UTC, not local. `time.strftime("%Y-%m-%dT%H:%M:%S")` is LOCAL naive, while the
    audit_log/model_registry column DEFAULTs (serve/db.py, migrations/002-004) stamp UTC
    and serve/routers/model_health.py reads these columns back as UTC. Mixing the two made
    a model trained minutes ago report `age_days: -1` on a UTC+5:30 host.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _jobs_dir() -> Path:
    base = Path(os.environ.get("PULSETWIN_JOBS_DIR", tempfile.gettempdir()))
    out = base / "pulsetwin_retrain_jobs"
    out.mkdir(parents=True, exist_ok=True)
    return out


class RetrainRequest(BaseModel):
    plant_id: str = "plant_a"


@router.post("/api/admin/trigger-retrain")
def trigger_retrain(req: RetrainRequest, request: Request,
                     role: str = Depends(require_role("system"))) -> dict[str, Any]:
    if os.environ.get("PULSETWIN_JOBS_ENABLED", "1") == "0":
        raise HTTPException(503, "Retraining is disabled in this deployment")
    config_path = ROOT / "configs" / f"{req.plant_id}.yaml"
    if not config_path.exists():
        raise HTTPException(404, f"unknown plant '{req.plant_id}'")

    job_id = secrets.token_hex(8)
    env = {**os.environ, "OMP_NUM_THREADS": "1"}
    subprocess.Popen(
        [sys.executable, "-m", "mlops.retrainer", "--config", str(config_path),
         "--job-id", job_id],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    log_action(request, request.app.state.ops, role, "retrain_triggered", "model",
               req.plant_id, {"job_id": job_id})
    return {"job_id": job_id, "status": "queued", "plant_id": req.plant_id}


@router.get("/api/admin/retrain-status/{job_id}")
def retrain_status(job_id: str, role: str = Depends(require_role("system"))) -> dict[str, Any]:
    path = _jobs_dir() / f"{job_id}.json"
    if not path.exists():
        return {"job_id": job_id, "status": "unknown"}
    return json.loads(path.read_text(encoding="utf-8"))


class PromoteRequest(BaseModel):
    model_id: str


@router.post("/api/admin/promote-model")
def promote_model(req: PromoteRequest, request: Request,
                   role: str = Depends(require_role("system"))) -> dict[str, Any]:
    ops = request.app.state.ops
    challenger = db.query_one(ops, "SELECT * FROM model_registry WHERE model_id=?",
                               (req.model_id,))
    if not challenger:
        raise HTTPException(404, f"unknown model '{req.model_id}'")
    if challenger["status"] != "challenger":
        raise HTTPException(
            400, f"model '{req.model_id}' is '{challenger['status']}', not a challenger"
        )

    now = _utc_now()
    previous = db.query_one(
        ops,
        "SELECT model_id FROM model_registry WHERE plant_id=? AND model_type=? "
        "AND status='champion'",
        (challenger["plant_id"], challenger["model_type"]),
    )
    if previous:
        db.execute(ops, "UPDATE model_registry SET status='retired', retired_at=? "
                        "WHERE model_id=?", (now, previous["model_id"]))
    db.execute(
        ops,
        "UPDATE model_registry SET status='champion', promoted_at=?, promoted_by=? "
        "WHERE model_id=?",
        (now, role, req.model_id),
    )
    swap = _activate_scorer(request, challenger["plant_id"], challenger.get("artifact_path"))
    model_info = getattr(request.app.state, "model_info", None)
    if swap["swapped"] and model_info is not None:
        model_info[challenger["plant_id"]] = {
            "model_id": req.model_id,
            "version": int(challenger.get("version") or 1),
        }
    log_action(request, ops, role, "model_promoted", "model", req.model_id,
               {"previous_champion": previous["model_id"] if previous else None,
                "scorer_swapped": swap["swapped"]})
    return {"promoted": req.model_id,
            "previous_champion": previous["model_id"] if previous else None,
            "serving": swap}


class RollbackRequest(BaseModel):
    plant_id: str = "plant_a"
    model_type: str = "defect_risk"


@router.post("/api/admin/rollback-model")
def rollback_model(req: RollbackRequest, request: Request,
                    role: str = Depends(require_role("system"))) -> dict[str, Any]:
    ops = request.app.state.ops
    current = db.query_one(
        ops,
        "SELECT model_id FROM model_registry WHERE plant_id=? AND model_type=? "
        "AND status='champion'",
        (req.plant_id, req.model_type),
    )
    previous = db.query_one(
        ops,
        "SELECT * FROM model_registry WHERE plant_id=? AND model_type=? AND status='retired' "
        "ORDER BY retired_at DESC LIMIT 1",
        (req.plant_id, req.model_type),
    )
    if not previous:
        raise HTTPException(404, f"no retired model to roll back to for "
                                  f"{req.plant_id}/{req.model_type}")
    now = _utc_now()
    if current:
        db.execute(ops, "UPDATE model_registry SET status='retired', retired_at=? "
                        "WHERE model_id=?", (now, current["model_id"]))
    db.execute(
        ops, "UPDATE model_registry SET status='champion', promoted_at=?, promoted_by=? "
             "WHERE model_id=?", (now, role, previous["model_id"]),
    )
    swap = _activate_scorer(request, req.plant_id, previous.get("artifact_path"))
    model_info = getattr(request.app.state, "model_info", None)
    if swap["swapped"] and model_info is not None:
        model_info[req.plant_id] = {
            "model_id": previous["model_id"],
            "version": int(previous.get("version") or 1),
        }
    log_action(request, ops, role, "model_rolled_back", "model", previous["model_id"],
               {"replaced": current["model_id"] if current else None,
                "scorer_swapped": swap["swapped"]})
    return {"rolled_back_to": previous["model_id"],
            "replaced": current["model_id"] if current else None,
            "serving": swap}
