"""Role-shaped reads for the two operational personas (ADR-071).

    GET /api/vehicles                          supervisor  the Vehicle Timeline list
    GET /api/vehicles/{unit_id}/timeline       supervisor  one vehicle's story (lazy)
    GET /api/decisions/pending                 manager     the Decision Queue
    GET /api/decisions/history                 manager     Decision History
    GET /api/decisions/{rec_id}/evidence       manager     "why is the AI recommending this"

Read-only. Decisions themselves still go through serve/routers/incidents.py (approve /
reject / modify / request-investigation / execute), where the governor re-checks the
caller's authority on every call.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from serve import db, decisions, vehicles
from serve.middleware.auth import require_role
from serve.routers.incidents import _plant_acl

router = APIRouter(tags=["operations"])


def _plant(request: Request, plant_id: str) -> None:
    _plant_acl(request, plant_id)
    if plant_id not in request.app.state.plants:
        raise HTTPException(404, f"unknown plant '{plant_id}'")


@router.get("/api/vehicles")
def list_vehicles(request: Request, plant_id: str = "plant_a", run_id: str | None = None,
                  status: Literal["all", "normal", "at_risk", "inspection_required", "incident",
                                  "completed"] = "all",
                  q: str | None = Query(default=None, max_length=40),
                  limit: int = Query(default=25, ge=1, le=100), offset: int = Query(default=0, ge=0),
                  role: str = Depends(require_role("supervisor"))) -> dict[str, Any]:
    """Server-side filter, search and pagination: the list never ships every unit."""
    _plant(request, plant_id)
    s = request.app.state
    return vehicles.list_vehicles(s.ops, s.main_con, s.plants, plant_id, run_id=run_id,
                                  status=status, q=q, limit=limit, offset=offset)


@router.get("/api/vehicles/{unit_id}/timeline")
def vehicle_timeline(unit_id: str, request: Request, plant_id: str = "plant_a",
                     run_id: str | None = None,
                     role: str = Depends(require_role("supervisor"))) -> dict[str, Any]:
    _plant(request, plant_id)
    s = request.app.state
    out = vehicles.timeline(s.ops, s.main_con, s.artifacts, s.plants, plant_id, unit_id, run_id)
    if out is None:
        raise HTTPException(404, f"no vehicle '{unit_id}' in this run")
    return out


@router.get("/api/decisions/pending")
def pending(request: Request, plant_id: str = "plant_a",
            role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    """Only decisions needing THIS caller's authority -- evaluated server-side by the
    governor for `role` -- so the queue is never "everything, with some greyed out"."""
    _plant(request, plant_id)
    queue = decisions.pending(request.app.state.ops, plant_id, role)
    return {"plant_id": plant_id, "role": role, "decisions": queue,
            "summary": decisions.summary(request.app.state.ops, plant_id, queue)}


@router.get("/api/decisions/history")
def history(request: Request, plant_id: str = "plant_a", limit: int = Query(default=50, ge=1, le=200),
            role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    _plant(request, plant_id)
    return {"plant_id": plant_id, "history": decisions.history(request.app.state.ops, plant_id, limit)}


@router.get("/api/decisions/{rec_id}/evidence")
def evidence(rec_id: str, request: Request, role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    rec = db.query_one(request.app.state.ops, "SELECT * FROM recommendations WHERE recommendation_id=?",
                       (rec_id,))
    if rec is None:
        raise HTTPException(404, f"unknown recommendation '{rec_id}'")
    _plant_acl(request, rec["plant_id"])
    return decisions.evidence(request.app.state.ops, rec, role)
