"""Lifecycle and shared SSE transport for live-synthetic simulation runs."""

from __future__ import annotations

import asyncio
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from serve import db
from serve.live_simulation import SCENARIOS
from serve.middleware.auth import get_role


router = APIRouter(tags=["live-synthetic"])


class StartSimulationRequest(BaseModel):
    plant_id: str = "plant_a"
    seed: int = 42
    scenario: Literal[
        "normal", "torque_degradation", "bad_material_lot", "high_humidity",
        "rushed_checklist", "sensor_dropout", "station_breakdown", "buffer_congestion",
    ] = "normal"
    speed: float = Field(default=10.0, gt=0, le=500)
    severity: float = Field(default=0.7, ge=0, le=1)
    affected_station: int | None = None
    affected_lot: str | None = None
    incident_start_s: float = Field(default=0, ge=0, le=86400)
    max_events: int = Field(default=5000, ge=10, le=100000)


class IncidentRequest(BaseModel):
    scenario: str
    severity: float = Field(default=0.7, ge=0, le=1)
    affected_station: int | None = None
    affected_lot: str | None = None


def _manager(request: Request):
    return request.app.state.live_simulation


def _call(action, *args):
    try:
        return action(*args)
    except KeyError as exc:
        raise HTTPException(404, f"unknown simulation run '{exc.args[0]}'") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/api/simulations/scenarios")
def scenarios() -> dict:
    return {"scenarios": list(SCENARIOS), "source": "synthetic"}


@router.post("/api/simulations")
def start_simulation(req: StartSimulationRequest, request: Request,
                     role: str = Depends(get_role)) -> dict:
    del role
    return _call(
        _manager(request).start,
        req.plant_id, req.seed, req.scenario, req.speed, req.severity,
        req.affected_station, req.affected_lot, req.max_events,
        req.incident_start_s,
    )


@router.get("/api/simulations/active")
def active_simulation(request: Request, plant_id: str = "plant_a") -> dict | None:
    return _manager(request).active(plant_id)


@router.get("/api/simulations/{run_id}")
def simulation(run_id: str, request: Request) -> dict:
    return _call(_manager(request).get, run_id)


@router.get("/api/simulations/{run_id}/metrics")
def simulation_metrics(run_id: str, request: Request) -> dict:
    run = _call(_manager(request).get, run_id)
    alert_rows = db.query_all(
        request.app.state.ops,
        """SELECT opened_at, acknowledged_at, actioned_at, validated_at, closed_at,
                  status, outcome FROM active_alerts
             WHERE run_id=?""",
        (run_id,),
    )
    decisions = db.query_all(
        request.app.state.ops,
        """SELECT selected_action, actual_outcome, validation_result
             FROM containment_decisions WHERE run_id=?""",
        (run_id,),
    )
    acknowledgement_s = []
    containment_s = []
    from datetime import datetime, timezone

    def parsed(value: object) -> datetime:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo is not None else result.replace(tzinfo=timezone.utc)

    for row in alert_rows:
        if row.get("acknowledged_at"):
            acknowledgement_s.append(max(0.0, (
                parsed(row["acknowledged_at"]) - parsed(row["opened_at"])
            ).total_seconds()))
        if row.get("actioned_at"):
            containment_s.append(max(0.0, (
                parsed(row["actioned_at"]) - parsed(row["opened_at"])
            ).total_seconds()))
    validation_counts: dict[str, int] = {}
    for row in decisions:
        result = row.get("validation_result") or "pending"
        validation_counts[result] = validation_counts.get(result, 0) + 1
    avoided = validation_counts.get("correct_intervention", 0)
    validated_alerts = [row for row in alert_rows if row.get("outcome") in (0, 1)]
    correct_alerts = sum(1 for row in validated_alerts if row.get("outcome") == 1)
    false_alerts = sum(1 for row in validated_alerts if row.get("outcome") == 0)
    return {
        "run_id": run_id,
        "source": run["source"],
        "events": int(run.get("event_count") or 0),
        "alerts": len(alert_rows),
        "active_alerts": sum(1 for row in alert_rows if row.get("status") != "CLOSED"),
        "correct_alerts": correct_alerts,
        "false_alerts": false_alerts,
        "alert_correctness_rate": (
            round(correct_alerts / len(validated_alerts), 4) if validated_alerts else None
        ),
        "acknowledged": sum(1 for row in alert_rows if row.get("acknowledged_at")),
        "mean_time_to_acknowledge_s": (
            round(sum(acknowledgement_s) / len(acknowledgement_s), 3)
            if acknowledgement_s else None
        ),
        "mean_time_to_contain_s": (
            round(sum(containment_s) / len(containment_s), 3) if containment_s else None
        ),
        "closed": sum(1 for row in alert_rows if row.get("status") == "CLOSED"),
        "decisions": len(decisions),
        "validated_decisions": sum(1 for row in decisions if row.get("validation_result")),
        "confirmed_defects": sum(1 for row in decisions if row.get("actual_outcome") == 1),
        "unnecessary_inspections": validation_counts.get("unnecessary_intervention", 0),
        "estimated_escaped_defects_avoided": avoided,
        "estimated_cost_avoided": {
            "value": avoided * 8500,
            "currency": "USD",
            "source": "NHTSA recall cost data, averaged across powertrain/body/paint campaigns; synthetic validated interventions only.",
        },
        "validation": validation_counts,
        "note": "Counts are synthetic-run outcomes; no real factory data is connected.",
    }


@router.post("/api/simulations/{run_id}/pause")
def pause_simulation(run_id: str, request: Request,
                     role: str = Depends(get_role)) -> dict:
    del role
    return _call(_manager(request).pause, run_id)


@router.post("/api/simulations/{run_id}/resume")
def resume_simulation(run_id: str, request: Request,
                      role: str = Depends(get_role)) -> dict:
    del role
    return _call(_manager(request).resume, run_id)


@router.post("/api/simulations/{run_id}/stop")
def stop_simulation(run_id: str, request: Request,
                    role: str = Depends(get_role)) -> dict:
    del role
    return _call(_manager(request).stop, run_id)


@router.post("/api/simulations/{run_id}/incidents")
def inject_incident(run_id: str, req: IncidentRequest, request: Request,
                    role: str = Depends(get_role)) -> dict:
    del role
    return _call(
        _manager(request).inject, run_id, req.scenario, req.severity,
        req.affected_station, req.affected_lot,
    )


@router.get("/api/simulations/{run_id}/stream")
async def live_stream(
    run_id: str,
    request: Request,
    after_seq: int = Query(default=0, ge=0),
):
    manager = _manager(request)
    _call(manager.get, run_id)

    async def gen():
        seq = after_seq
        while True:
            if await request.is_disconnected():
                break
            events = manager.events_after(run_id, seq)
            for event in events:
                seq = max(seq, int(event["seq"]))
                event_type = event.get("event_type", "station_update")
                yield f"event: {event_type}\ndata: {json.dumps(event)}\n\n"
            run = manager.get(run_id)
            if run["status"] in ("stopped", "completed", "failed") and not events:
                yield f"event: end\ndata: {json.dumps(run)}\n\n"
                break
            if not events:
                yield ": ping\n\n"
            await asyncio.sleep(0.2)

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
