"""PulseTwin API.

IMPORT WALL (CLAUDE.md §2): fastapi, uvicorn, pydantic, numpy, orjson and stdlib only.
No pandas, no sklearn, no shap, no psutil. Enforced by tests/test_serve_imports.py.

Everything served here was precomputed by offline/build_artifact.py. No model library is
loaded, no training happens at startup, and the artifact database is opened read-only.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse, StreamingResponse

from serve import db
from serve.schemas import (
    BottleneckResponse,
    ContainmentResponse,
    FeedbackRequest,
    HealthResponse,
    MetricsResponse,
    PredictRequest,
    PredictResponse,
    StationConfigResponse,
)
from serve.scorer import Scorer

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = Path(os.environ.get("PULSETWIN_ARTIFACTS", ROOT / "artifacts"))

STATE: dict = {"con": None, "feedback": None, "scorers": {}, "plants": {}, "started": 0.0}


def rss_mb() -> tuple[float | None, str]:
    """Resident set size without psutil (a banned import).

    /proc/self/statm does not exist on Windows, so local development must not crash on
    a health check (docs/AUDIT.md M18).
    """
    try:
        with open("/proc/self/statm", "r", encoding="utf-8") as fh:
            pages = int(fh.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE") / 1e6, "proc"
    except (FileNotFoundError, OSError, AttributeError, ValueError, IndexError):
        return None, "unavailable"


@asynccontextmanager
async def lifespan(app: FastAPI):
    STATE["started"] = time.time()
    STATE["con"] = db.open_readonly(ARTIFACTS / "pulsetwin.db")
    STATE["feedback"] = db.open_feedback_db()
    for row in db.query_all(STATE["con"], "SELECT * FROM plant_meta"):
        pid = row["plant_id"]
        STATE["plants"][pid] = row
        npz = ARTIFACTS / f"model_{pid}.npz"
        if npz.exists():
            STATE["scorers"][pid] = Scorer(npz)
    mb, src = rss_mb()
    print(f"[pulsetwin] {len(STATE['plants'])} plants, "
          f"{len(STATE['scorers'])} scorers, RSS={mb if mb else 'n/a'} ({src})")
    yield
    for key in ("con", "feedback"):
        if STATE[key] is not None:
            STATE[key].close()


app = FastAPI(title="PulseTwin API", default_response_class=ORJSONResponse, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],      # demo posture -- see docs/ARCHITECTURE.md
    allow_methods=["*"],
    allow_headers=["*"],
)


def _plant(plant_id: str) -> dict:
    p = STATE["plants"].get(plant_id)
    if not p:
        raise HTTPException(404, f"unknown plant '{plant_id}'")
    return p


# ---------------------------------------------------------------------------- health


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    mb, src = rss_mb()
    plants = list(STATE["plants"].values())
    return HealthResponse(
        status="ok",
        rss_mb=mb,
        rss_source=src,
        uptime_s=time.time() - STATE["started"],
        plants=[p["plant_id"] for p in plants],
        station_counts={p["plant_id"]: p["station_count"] for p in plants},
        artifact_built_at=plants[0]["built_at"] if plants else None,
    )


@app.get("/api/config/plants")
def plants() -> list[dict]:
    return [dict(p) for p in STATE["plants"].values()]


@app.get("/api/config/stations", response_model=StationConfigResponse)
def stations(plant_id: str = "plant_a") -> StationConfigResponse:
    _plant(plant_id)
    rows = db.query_all(
        STATE["con"],
        "SELECT * FROM station_config WHERE plant_id = ? ORDER BY position",
        (plant_id,),
    )
    zones: dict[str, dict] = {}
    for r in rows:
        r["sensors"] = json.loads(r["sensors"])
        z = zones.setdefault(r["zone"], {"id": r["zone"], "name": r["zone_name"], "stations": []})
        z["stations"].append(r["station_id"])
    return StationConfigResponse(stations=rows, zones=list(zones.values()))


# ---------------------------------------------------------------------------- stream


def _shift_window(ts: str) -> tuple[str, str]:
    """8-hour shift boundaries containing ts. Used for the server-side alert budget."""
    dt = datetime.fromisoformat(ts)
    start_h = (dt.hour // 8) * 8
    start = dt.replace(hour=start_h, minute=0, second=0, microsecond=0)
    end = start.replace(hour=start_h + 7, minute=59, second=59)
    return start.isoformat(), end.isoformat()


@app.get("/api/stream")
async def stream(
    plant_id: str = "plant_a",
    speed: float = Query(10.0, gt=0, le=500),
    from_ts: str | None = None,
):
    """Server-sent events replay of the committed JSONL.

    ADR-009: `await asyncio.sleep`, never `time.sleep`. A blocking sleep here freezes the
    whole event loop under `--workers 1`, so a second browser tab would make every other
    endpoint unresponsive.
    """
    _plant(plant_id)
    path = ARTIFACTS / f"stream_{plant_id}.jsonl"
    if not path.exists():
        raise HTTPException(404, "no stream artifact for this plant")

    offset = 0
    if from_ts:
        row = db.query_one(
            STATE["con"],
            "SELECT byte_offset FROM stream_index WHERE plant_id=? AND ts>=? "
            "ORDER BY ts LIMIT 1",
            (plant_id, from_ts),
        )
        offset = row["byte_offset"] if row else 0

    # Above this multiplier the browser cannot render every event; downsample server-side
    # rather than shipping ~1,400 events/sec into React state (docs/AUDIT.md L25).
    stride = 1 if speed < 25 else int(speed // 12)

    async def gen():
        budget = int(STATE["plants"][plant_id]["alert_budget"])
        with path.open("r", encoding="utf-8") as fh:
            fh.seek(offset)
            prev_ts = None
            last_ping = time.time()
            n = 0
            alerts_by_shift: dict[str, set] = {}
            for line in fh:
                n += 1
                rec = json.loads(line)
                if prev_ts is not None:
                    delta = (
                        datetime.fromisoformat(rec["ts"]) - datetime.fromisoformat(prev_ts)
                    ).total_seconds()
                    if delta > 0:
                        await asyncio.sleep(min(delta / speed, 1.0))
                prev_ts = rec["ts"]

                if rec.get("alert"):
                    key, _ = _shift_window(rec["ts"])
                    alerts_by_shift.setdefault(key, set()).add(rec["unit_id"])
                    used = len(alerts_by_shift[key])
                    rec["alerts_used"] = used
                    rec["alerts_budget"] = budget
                    yield f"event: alert\ndata: {json.dumps(rec)}\n\n"
                elif n % stride == 0:
                    key, _ = _shift_window(rec["ts"])
                    rec["alerts_used"] = len(alerts_by_shift.get(key, ()))
                    rec["alerts_budget"] = budget
                    yield f"event: station_update\ndata: {json.dumps(rec)}\n\n"

                if time.time() - last_ping > 15:
                    last_ping = time.time()
                    yield ": ping\n\n"
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # HF Spaces' proxy buffers streamed responses without this: the stream works
            # perfectly on localhost and delivers nothing in the deployed demo.
            "X-Accel-Buffering": "no",
        },
    )


# --------------------------------------------------------------------------- predict


@app.post("/api/predict/defect", response_model=PredictResponse)
def predict(req: PredictRequest) -> PredictResponse:
    scorer = STATE["scorers"].get(req.plant_id)
    if scorer is None:
        raise HTTPException(404, f"no model for plant '{req.plant_id}'")

    x = scorer.vector_from(req.features)
    p = float(scorer.predict(x.reshape(1, -1))[0])
    factors = scorer.explain(x, top=5)

    known = set(req.features)
    measured = sorted(f for f in known if f in scorer.feature_names)
    half = 0.5 * p * (1.0 - p) ** 0.5 + 0.02

    return PredictResponse(
        probability=p,
        calibrated=True,
        confidence_interval=[max(0.0, p - half), min(1.0, p + half)],
        explanation_mode="decision_path",
        provenance={"measured": measured, "inferred": []},
        top_factors=[
            {
                "feature": f["feature"],
                "direction": f["direction"],
                "contribution": f["contribution"],
                "value": f["value"],
                "plain_language": f"{f['feature'].replace('_', ' ')} "
                                  f"{'above' if f['direction'] == 'high' else 'below'} baseline",
            }
            for f in factors
        ],
        recommended_action=(
            "Hold for inspection" if p >= STATE["plants"][req.plant_id]["budget_threshold"]
            else "Monitor - below the alarm-budget threshold"
        ),
    )


# ------------------------------------------------------------------------ unit/trace


@app.get("/api/units")
def units(plant_id: str = "plant_a", alerts_only: bool = False, limit: int = 100) -> list[dict]:
    _plant(plant_id)
    sql = "SELECT * FROM units WHERE plant_id = ?"
    if alerts_only:
        sql += " AND alert_fired = 1"
    sql += " ORDER BY calibrated_risk DESC LIMIT ?"
    return db.query_all(STATE["con"], sql, (plant_id, limit))


@app.get("/api/units/{unit_id}")
def unit_detail(unit_id: str, plant_id: str = "plant_a") -> dict:
    u = db.query_one(
        STATE["con"], "SELECT * FROM units WHERE plant_id=? AND unit_id=?", (plant_id, unit_id)
    )
    if not u:
        raise HTTPException(404, f"unknown unit '{unit_id}'")
    u["factors"] = db.query_all(
        STATE["con"],
        "SELECT * FROM unit_factors WHERE plant_id=? AND unit_id=? ORDER BY rank",
        (plant_id, unit_id),
    )
    u["journey"] = db.query_all(
        STATE["con"],
        "SELECT * FROM genealogy WHERE plant_id=? AND unit_id=? ORDER BY station_id",
        (plant_id, unit_id),
    )
    u["readings"] = db.query_all(
        STATE["con"],
        "SELECT * FROM station_readings WHERE plant_id=? AND unit_id=? ORDER BY station_id",
        (plant_id, unit_id),
    )
    return u


@app.get("/api/trace/containment/{unit_id}", response_model=ContainmentResponse)
def containment(unit_id: str, plant_id: str = "plant_a") -> ContainmentResponse:
    """Every other in-flight unit sharing the flagged unit's causal fingerprint.

    ADR-017: shared factors are reported as opaque operator GROUPS, never a stable
    operator identity. The linkage is needed for containment; the identity is not.
    """
    flagged = db.query_one(
        STATE["con"], "SELECT * FROM units WHERE plant_id=? AND unit_id=?", (plant_id, unit_id)
    )
    if not flagged:
        raise HTTPException(404, f"unknown unit '{unit_id}'")

    factors = db.query_all(
        STATE["con"],
        "SELECT feature, plain_language FROM unit_factors WHERE plant_id=? AND unit_id=? "
        "ORDER BY rank",
        (plant_id, unit_id),
    )

    window_min = 30
    peers = db.query_all(
        STATE["con"],
        """SELECT u.unit_id, u.calibrated_risk, u.lot_id, u.variant, u.prediction_ts,
                  (SELECT MAX(station_id) FROM genealogy g
                    WHERE g.plant_id=u.plant_id AND g.unit_id=u.unit_id) AS current_station
             FROM units u
            WHERE u.plant_id = ? AND u.unit_id != ?
              AND ABS(julianday(u.prediction_ts) - julianday(?)) * 1440 <= ?
              AND (u.lot_id = ? OR u.calibrated_risk >= ?)
            ORDER BY u.calibrated_risk DESC LIMIT 12""",
        (plant_id, unit_id, flagged["prediction_ts"], window_min, flagged["lot_id"],
         float(STATE["plants"][plant_id]["budget_threshold"]) * 0.6),
    )

    at_risk = []
    for p in peers:
        shared = []
        if p["lot_id"] == flagged["lot_id"]:
            shared.append("same_lot")
        if p["variant"] == flagged["variant"]:
            shared.append("same_variant")
        shared.append("same_time_window")
        risk = p["calibrated_risk"]
        at_risk.append({
            "unit_id": p["unit_id"],
            "current_station": p["current_station"],
            "risk": risk,
            "shared_factors": shared,
            "action": "HOLD" if risk >= 0.5 else ("INSPECT" if risk >= 0.25 else "MONITOR"),
        })

    return ContainmentResponse(
        flagged_unit=unit_id,
        flagged_risk=flagged["calibrated_risk"],
        flagged_factors=[f["plain_language"] for f in factors],
        at_risk_units=at_risk,
        containment_window_min=window_min,
        total_at_risk=len(at_risk),
    )


# ------------------------------------------------------------------------ bottleneck


@app.get("/api/bottleneck/current", response_model=BottleneckResponse)
def bottleneck_current(plant_id: str = "plant_a") -> BottleneckResponse:
    rows = db.query_all(
        STATE["con"],
        "SELECT * FROM bottleneck_windows WHERE plant_id=? ORDER BY window_start DESC LIMIT 40",
        (plant_id,),
    )
    if not rows:
        raise HTTPException(404, "no bottleneck data")
    cur = rows[0]
    prev = next((r for r in rows if r["bottleneck_station"] != cur["bottleneck_station"]), None)
    name = db.query_one(
        STATE["con"],
        "SELECT station_name FROM station_config WHERE plant_id=? AND station_id=?",
        (plant_id, cur["bottleneck_station"]),
    )
    return BottleneckResponse(
        current_station=cur["bottleneck_station"],
        current_station_name=name["station_name"] if name else "",
        confidence=cur["confidence"],
        since=cur["window_start"],
        previous_station=prev["bottleneck_station"] if prev else None,
        shifted_at=prev["window_end"] if prev else None,
        detection_accuracy=_metric(plant_id, "bottleneck_detection_accuracy"),
    )


@app.get("/api/bottleneck/history")
def bottleneck_history(plant_id: str = "plant_a", limit: int = 700) -> list[dict]:
    return db.query_all(
        STATE["con"],
        "SELECT * FROM bottleneck_windows WHERE plant_id=? ORDER BY window_start LIMIT ?",
        (plant_id, limit),
    )


@app.get("/api/stations/stats")
def station_stats(plant_id: str = "plant_a") -> list[dict]:
    return db.query_all(
        STATE["con"],
        """SELECT station_id, provenance,
                  AVG(utilisation) util, AVG(blocked_pct) blocked, AVG(starved_pct) starved,
                  AVG(down_pct) down, AVG(lost_throughput_uph) lost_uph
             FROM station_stats WHERE plant_id=? GROUP BY station_id ORDER BY lost_uph DESC""",
        (plant_id,),
    )


# --------------------------------------------------------------------------- metrics


def _metric(plant_id: str, key: str) -> float | None:
    r = db.query_one(
        STATE["con"], "SELECT value FROM metrics WHERE plant_id=? AND key=?", (plant_id, key)
    )
    return r["value"] if r else None


@app.get("/api/metrics", response_model=MetricsResponse)
def metrics(plant_id: str = "plant_a") -> MetricsResponse:
    _plant(plant_id)
    vals = {
        r["key"]: r["value"]
        for r in db.query_all(
            STATE["con"], "SELECT key, value FROM metrics WHERE plant_id=?", (plant_id,)
        )
    }
    blobs = {
        r["key"]: json.loads(r["payload"])
        for r in db.query_all(
            STATE["con"], "SELECT key, payload FROM metrics_json WHERE plant_id=?", (plant_id,)
        )
    }
    return MetricsResponse(plant_id=plant_id, values=vals, **blobs)


@app.get("/api/economics/roi")
def roi(plant_id: str = "plant_a", scenario: str = "base") -> dict:
    """ROI derived from measured test-set performance. Every figure carries its source."""
    _plant(plant_id)
    econ = {
        r["key"]: r for r in db.query_all(
            STATE["con"], "SELECT * FROM economics WHERE plant_id=?", (plant_id,)
        )
    }
    mult = {"pessimistic": 0.5, "base": 1.0, "optimistic": 1.35}.get(scenario, 1.0)

    recall = _metric(plant_id, "recall_at_budget") or 0.0
    base_rate = _metric(plant_id, "base_rate_test") or 0.0
    escape = _metric(plant_id, "escape_rate") or 0.0
    alerts = _metric(plant_id, "alerts_per_shift") or 0.0

    meta = STATE["plants"][plant_id]
    units_year = meta["achieved_uph"] * 8 * 3 * 250
    caught = units_year * base_rate * recall * mult

    def money(key: str) -> float:
        return econ[key]["value"] if key in econ else 0.0

    scrap = caught * money("scrap_cost_per_unit") * 0.55
    rework = caught * money("rework_cost_per_unit")
    warranty = caught * escape * money("warranty_escape_cost_per_unit")
    alarm_cost = -alerts * 3 * 250 * money("supervisor_response_cost_per_alarm")
    system = money("system_implementation_cost")
    net = scrap + rework + warranty + alarm_cost

    return {
        "plant_id": plant_id,
        "scenario": scenario,
        "basis": {
            "units_per_year": units_year,
            "achieved_uph": meta["achieved_uph"],
            "recall_at_budget": recall,
            "defect_rate": base_rate,
            "escape_rate": escape,
            "note": "Derived from measured test-set recall, not an assumed catch rate.",
        },
        "components": [
            {"label": "Scrap avoided", "value": scrap, "source": econ.get("scrap_cost_per_unit", {}).get("source")},
            {"label": "Rework avoided", "value": rework, "source": econ.get("rework_cost_per_unit", {}).get("source")},
            {"label": "Warranty escapes prevented", "value": warranty, "source": econ.get("warranty_escape_cost_per_unit", {}).get("source")},
            {"label": "Alarm response cost", "value": alarm_cost, "source": econ.get("supervisor_response_cost_per_alarm", {}).get("source")},
        ],
        "net_value": net,
        "system_cost": system,
        "roi_pct": 100.0 * (net - system) / system if system else None,
        "payback_months": 12.0 * system / net if net > 0 else None,
        "marginal_value_of_doubling_budget": _metric(plant_id, "marginal_value_of_doubling"),
        "marginal_cost_of_doubling_budget": _metric(plant_id, "marginal_cost_of_doubling"),
    }


@app.post("/api/feedback")
def feedback(req: FeedbackRequest) -> dict:
    db.execute(
        STATE["feedback"],
        "INSERT INTO feedback (plant_id, unit_id, verdict, created_at) VALUES (?,?,?,?)",
        (req.plant_id, req.unit_id, req.verdict, datetime.utcnow().isoformat()),
    )
    n = db.query_one(STATE["feedback"], "SELECT COUNT(*) c FROM feedback")
    return {"stored": True, "total": n["c"] if n else 0, "persistence": "ephemeral"}
