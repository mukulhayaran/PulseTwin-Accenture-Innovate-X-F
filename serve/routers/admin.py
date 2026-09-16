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
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from serve import db
from serve.middleware.audit import log_action
from serve.middleware.auth import require_role

router = APIRouter()
ROOT = Path(__file__).resolve().parents[2]


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

    now = time.strftime("%Y-%m-%dT%H:%M:%S")
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
    log_action(request, ops, role, "model_promoted", "model", req.model_id,
               {"previous_champion": previous["model_id"] if previous else None})
    return {"promoted": req.model_id,
            "previous_champion": previous["model_id"] if previous else None}


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
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    if current:
        db.execute(ops, "UPDATE model_registry SET status='retired', retired_at=? "
                        "WHERE model_id=?", (now, current["model_id"]))
    db.execute(
        ops, "UPDATE model_registry SET status='champion', promoted_at=?, promoted_by=? "
             "WHERE model_id=?", (now, role, previous["model_id"]),
    )
    log_action(request, ops, role, "model_rolled_back", "model", previous["model_id"],
               {"replaced": current["model_id"] if current else None})
    return {"rolled_back_to": previous["model_id"],
            "replaced": current["model_id"] if current else None}
