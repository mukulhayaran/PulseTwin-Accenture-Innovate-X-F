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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse, StreamingResponse
from prometheus_fastapi_instrumentator import Instrumentator

from serve import db
from serve import metrics as prom_metrics  # "metrics" collides with this module's own
# GET /api/metrics endpoint function further below -- both would bind the name `metrics`
# at module scope, and the LAST definition wins, silently shadowing the earlier import.
from serve.schemas import (
    BottleneckResponse,
    ContainmentResponse,
    FeedbackRequest,
    HealthResponse,
    MetricsResponse,
    PredictRequest,
    PredictResponse,
    StationConfigResponse,
    WhatIfRequest,
    WhatIfResponse,
)
from serve.fallback_scorer import FallbackScorer
from serve.middleware.audit import log_action
from serve.middleware.auth import get_role, require_role
from serve.middleware.rate_limit import RateLimitMiddleware
from serve.routers import admin, alerts_mgmt, audit, model_health, system, triage
from serve.scorer import Scorer

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = Path(os.environ.get("PULSETWIN_ARTIFACTS", ROOT / "artifacts"))

STATE: dict = {
    "con": None, "ops": None, "feedback": None, "scorers": {}, "fallback_scorers": {},
    "plants": {}, "started": 0.0, "backend": "sqlite",
}


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
    STATE["con"], STATE["ops"], STATE["backend"] = db.open_database(ARTIFACTS / "pulsetwin.db")
    STATE["feedback"] = db.open_feedback_db()
    for row in db.query_all(STATE["con"], "SELECT * FROM plant_meta"):
        pid = row["plant_id"]
        STATE["plants"][pid] = row
        npz = ARTIFACTS / f"model_{pid}.npz"
        if npz.exists():
            STATE["scorers"][pid] = Scorer(npz)
        fb_npz = ARTIFACTS / f"fallback_{pid}.npz"
        if fb_npz.exists():
            STATE["fallback_scorers"][pid] = FallbackScorer(fb_npz)
    mb, src = rss_mb()
    print(f"[pulsetwin] backend={STATE['backend']} {len(STATE['plants'])} plants, "
          f"{len(STATE['scorers'])} scorers, RSS={mb if mb else 'n/a'} ({src})")

    # Exposed via app.state (not the STATE global) so serve/routers/* modules can reach
    # them without importing serve.main -- which would import routers, which import
    # main: a cycle. FastAPI's own idiom for sharing app-lifetime objects with routers.
    app.state.main_con = STATE["con"]
    app.state.ops = STATE["ops"]
    app.state.backend = STATE["backend"]
    app.state.started = STATE["started"]
    app.state.plants = STATE["plants"]
    # The SAME dict object, not a copy: serve/routers/admin.py swaps an entry in here when
    # a challenger is promoted, and _predict() below reads STATE["scorers"], so both must
    # see the one mapping. Without this, promotion updated a registry row and nothing
    # else -- /api/predict/defect kept serving the old model forever, while
    # docs/MLOPS_LIFECYCLE.md claimed promotion "is the only code path that changes what
    # /api/predict/defect actually serves".
    app.state.scorers = STATE["scorers"]

    yield
    seen = set()
    for key in ("con", "ops", "feedback"):
        handle = STATE[key]
        if handle is not None and id(handle) not in seen:
            seen.add(id(handle))
            handle.close()


app = FastAPI(title="PulseTwin API", default_response_class=ORJSONResponse, lifespan=lifespan)
# ORDER MATTERS, and it is the reverse of how it reads. `add_middleware` pushes onto the
# TOP of the stack, so the LAST one added is the OUTERMOST. RateLimitMiddleware was added
# last and therefore wrapped CORS -- its 429 short-circuits and returns a JSONResponse
# that never passes through CORSMiddleware, so the response carried no
# Access-Control-Allow-Origin. A browser hitting the per-role limit saw an opaque CORS
# failure, indistinguishable from the backend being down, instead of a 429 the UI could
# explain. CORS is added last here so it stays outermost and decorates every response,
# including the rejections.
app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],      # demo posture -- see docs/ARCHITECTURE.md
    allow_methods=["*"],
    allow_headers=["*"],      # covers X-PulseTwin-Role (serve/middleware/auth.py)
)

# Generic HTTP request-count/latency metrics, exposed at GET /metrics. The
# finals-brief-specific gauges/counters in serve/metrics.py are updated inline at the
# call sites below (predict, stream, system health, model health) rather than here --
# the instrumentator only knows about the request/response cycle, not what's inside it.
Instrumentator().instrument(app).expose(app)

# New in the finals upgrade (Phase F3/F4). Each module owns its own role gating
# (serve/middleware/auth.py) -- see docs/SECURITY.md for the full RBAC table.
app.include_router(model_health.router)
app.include_router(system.router)
app.include_router(audit.router)
app.include_router(alerts_mgmt.router)
app.include_router(admin.router)
app.include_router(triage.router)


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
        database=STATE["backend"],
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
        prom_metrics.active_connections.inc()
        try:
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
                        prom_metrics.alerts_fired_total.labels(plant_id=plant_id, shift=key).inc()
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
        finally:
            prom_metrics.active_connections.dec()

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


def _fallback_predict(req: PredictRequest) -> PredictResponse:
    """SPC fallback (mlops/fallback.py / serve/fallback_scorer.py). Surfaced when the
    primary model is missing or fails to score -- see docs/MLOPS_LIFECYCLE.md. Labelled
    plainly so the UI never presents a fallback number as though the real model spoke.
    """
    fb = STATE["fallback_scorers"].get(req.plant_id)
    if fb is None:
        raise HTTPException(404, f"no model (or fallback) for plant '{req.plant_id}'")
    result = fb.score(req.features)
    p = result["probability"]
    return PredictResponse(
        probability=p,
        calibrated=False,
        confidence_interval=[max(0.0, p - 0.15), min(1.0, p + 0.15)],
        explanation_mode="spc_fallback",
        provenance={"measured": sorted(req.features), "inferred": []},
        top_factors=[
            {
                "feature": f"s{v['station_id']}_{v['signal']}",
                "direction": "high" if v["z_score"] > 0 else "low",
                "contribution": v["z_score"],
                "value": v["value"],
                "plain_language": (
                    f"FALLBACK MODE: station {v['station_id']} {v['signal']} is "
                    f"{abs(v['z_score']):.1f} sigma from its rolling baseline "
                    f"({v['baseline_mean']:.2f})"
                ),
            }
            for v in result["violations"][:5]
        ],
        recommended_action=(
            "FALLBACK MODE - simplified statistical rules active while the primary model "
            "is unavailable or being validated. " +
            ("Hold for inspection" if p >= 0.5 else "Monitor")
        ),
    )


@app.post("/api/predict/defect", response_model=PredictResponse)
def predict(req: PredictRequest) -> PredictResponse:
    # Validate the plant BEFORE touching a labelled metric. Incrementing first meant any
    # caller could mint an unbounded number of Prometheus label values by POSTing made-up
    # plant_ids -- a cardinality explosion in the monitoring backend, from unauthenticated
    # input. `_plant` raises 404 for anything not in the artifact.
    _plant(req.plant_id)
    prom_metrics.predictions_total.labels(plant_id=req.plant_id).inc()
    with prom_metrics.prediction_latency_seconds.labels(plant_id=req.plant_id).time():
        return _predict(req)


def _predict(req: PredictRequest) -> PredictResponse:
    scorer = STATE["scorers"].get(req.plant_id)
    if scorer is None:
        return _fallback_predict(req)

    try:
        x = scorer.vector_from(req.features)
        p = float(scorer.predict(x.reshape(1, -1))[0])
        factors = scorer.explain(x, top=5)
    except (ValueError, IndexError, KeyError) as e:
        # A corrupt/incompatible model file must degrade to the fallback, not 500 the
        # whole endpoint -- "what happens when the model is wrong" includes "when it is
        # broken" (finals brief SS1).
        print(f"[pulsetwin] primary scorer failed for {req.plant_id}: {e}; using fallback")
        return _fallback_predict(req)

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


# A NEGATIVE limit means "no limit" in SQLite, so `?limit=-1` quietly turned every capped
# list endpoint into a full table scan of the artifact. serve/routers/audit.py already
# clamped; these did not. Clamp at the same place rather than trusting the caller.
def _clamp_limit(limit: int, cap: int) -> int:
    return max(1, min(limit, cap))


@app.get("/api/units")
def units(plant_id: str = "plant_a", alerts_only: bool = False, limit: int = 100) -> list[dict]:
    _plant(plant_id)
    sql = "SELECT * FROM units WHERE plant_id = ?"
    if alerts_only:
        # TRUE/FALSE, not the literal 1/0 -- alert_fired is a native BOOLEAN on Postgres
        # (migrations/001) and SQLite has treated TRUE/FALSE as 1/0 aliases since 3.23.
        sql += " AND alert_fired = TRUE"
    sql += " ORDER BY calibrated_risk DESC LIMIT ?"
    return db.query_all(STATE["con"], sql, (plant_id, _clamp_limit(limit, 2000)))


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
    # A +/- window in wall-clock time. Computed in Python rather than with a DB-specific
    # date function (SQLite's julianday() has no Postgres equivalent) -- both backends can
    # then compare ISO-8601 bounds as plain text/timestamptz range predicates.
    flagged_dt = datetime.fromisoformat(flagged["prediction_ts"])
    window_start = (flagged_dt - timedelta(minutes=window_min)).isoformat()
    window_end = (flagged_dt + timedelta(minutes=window_min)).isoformat()
    peers = db.query_all(
        STATE["con"],
        """SELECT u.unit_id, u.calibrated_risk, u.lot_id, u.variant, u.prediction_ts,
                  (SELECT MAX(station_id) FROM genealogy g
                    WHERE g.plant_id=u.plant_id AND g.unit_id=u.unit_id) AS current_station
             FROM units u
            WHERE u.plant_id = ? AND u.unit_id != ?
              AND u.prediction_ts >= ? AND u.prediction_ts <= ?
              AND (u.lot_id = ? OR u.calibrated_risk >= ?)
            ORDER BY u.calibrated_risk DESC LIMIT 12""",
        (plant_id, unit_id, window_start, window_end, flagged["lot_id"],
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
        (plant_id, _clamp_limit(limit, 5000)),
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
def roi(plant_id: str = "plant_a", scenario: str = "base",
        role: str = Depends(require_role("leadership"))) -> dict:
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


@app.post("/api/whatif", response_model=WhatIfResponse)
def whatif(req: WhatIfRequest, request: Request,
           role: str = Depends(require_role("manager"))) -> WhatIfResponse:
    """Interpolate the precomputed scenario grid (ADR-007).

    Throughput and WIP interpolate. `new_bottleneck_station` does NOT -- it is a
    categorical station ID, and the mean of S12 and S19 is not S15.5. It is resolved by
    nearest neighbour and the response says so, rather than quietly returning a number
    that is not a station.
    """
    _plant(req.plant_id)
    base = db.query_one(
        STATE["con"], "SELECT * FROM whatif_baseline WHERE plant_id=?", (req.plant_id,)
    )
    if not base:
        raise HTTPException(404, "no what-if grid for this plant")

    pts = db.query_all(
        STATE["con"],
        "SELECT * FROM whatif_grid WHERE plant_id=? AND station_id=?",
        (req.plant_id, req.station_id),
    )
    if not pts:
        available = db.query_all(
            STATE["con"],
            "SELECT DISTINCT station_id FROM whatif_grid WHERE plant_id=? ORDER BY station_id",
            (req.plant_id,),
        )
        raise HTTPException(
            404,
            f"station {req.station_id} is not in the grid; available: "
            f"{[a['station_id'] for a in available]}",
        )

    # Inverse-distance weighting over the grid points for this station.
    weights, uph, wip = [], [], []
    for p in pts:
        db_ = abs(p["buffer_delta"] - req.buffer_delta) / 8.0
        dc = abs(p["cycle_delta_pct"] - req.cycle_delta_pct) / 20.0
        weights.append(1.0 / (1e-6 + db_ * db_ + dc * dc))
        uph.append(p["throughput_uph"])
        wip.append(p["wip_units"])

    w = np.asarray(weights)
    w = w / w.sum()
    proj_uph = float(np.dot(w, np.asarray(uph)))
    proj_wip = float(np.dot(w, np.asarray(wip)))
    nearest = pts[int(np.argmax(w))]

    delta_uph = proj_uph - base["throughput_uph"]
    econ = {
        r["key"]: r["value"]
        for r in db.query_all(
            STATE["con"], "SELECT key, value FROM economics WHERE plant_id=?", (req.plant_id,)
        )
    }
    # Marginal contribution per unit, derived from the sourced downtime cost rather than
    # invented: downtime $/hr divided by achieved units/hr.
    achieved = STATE["plants"][req.plant_id]["achieved_uph"] or 1.0
    per_unit = econ.get("downtime_cost_per_hour", 5000.0) / achieved
    annual_value = delta_uph * 8 * 3 * 250 * per_unit

    log_action(request, STATE["ops"], role, "whatif_simulation_run", "station",
               str(req.station_id), {"plant_id": req.plant_id, "buffer_delta": req.buffer_delta,
                                      "cycle_delta_pct": req.cycle_delta_pct})
    return WhatIfResponse(
        mode="interpolated",
        bottleneck_mode="nearest_neighbour",
        grid_resolution={"buffer_deltas": [-2, 0, 2, 4, 8], "cycle_deltas": [-10.0, 0.0, 10.0]},
        baseline={
            "throughput_uph": base["throughput_uph"],
            "wip_units": base["wip_units"],
            "bottleneck_station": base["bottleneck_station"],
        },
        projected={
            "throughput_uph": proj_uph,
            "wip_units": proj_wip,
            "bottleneck_station": nearest["new_bottleneck_station"],
        },
        delta={"throughput_uph": delta_uph, "wip_units": proj_wip - base["wip_units"]},
        annual_value=annual_value,
        assumptions=(
            f"Interpolated from a {len(pts)}-point grid, each a full "
            f"{base['window_hours']:.0f}-hour shift simulated under the PEAK variant mix "
            f"-- the EV-heavy condition this tuning is for. Buffer delta applies to the "
            f"FEED buffer upstream of the station; downstream capacity cannot relieve a "
            f"constraint. Marginal contribution "
            f"${per_unit:,.0f}/unit derived from the sourced downtime cost at "
            f"{achieved:.1f} UPH achieved. Annualised over 250 days x 3 shifts."
        ),
    )


@app.get("/api/whatif/stations")
def whatif_stations(plant_id: str = "plant_a") -> list[dict]:
    """Which stations the grid covers -- the constraint and its neighbours."""
    return db.query_all(
        STATE["con"],
        """SELECT DISTINCT g.station_id, c.station_name, c.sensor_tier
             FROM whatif_grid g JOIN station_config c
               ON c.plant_id = g.plant_id AND c.station_id = g.station_id
            WHERE g.plant_id = ? ORDER BY g.station_id""",
        (plant_id,),
    )


@app.get("/api/economics/instrumentation-plan")
def instrumentation_plan(plant_id: str = "plant_a",
                          role: str = Depends(require_role("leadership"))) -> dict:
    """Retrofit ranking by measured marginal model value per dollar.

    `delta_pr_auc` for a manual station is an ESTIMATE BY ANALOGY -- there is no true
    sensor reading at an unsensored station to ablate, so it is inferred from what
    comparable instrumented neighbours' data is worth. Labelled as such, because a
    capital decision should know which numbers were measured and which were reasoned.
    """
    _plant(plant_id)
    rows = db.query_all(
        STATE["con"],
        # `(payback_months IS NULL)` first, THEN the value. A plain `ORDER BY
        # payback_months` sorted NULLs to the TOP on SQLite, so this table -- a capital
        # recommendation, rendered top-to-bottom in the Leadership view -- led with every
        # DEFER candidate (annual value $0, payback undefined) and buried the only PROCEED
        # row at the bottom: plant A showed its single funded retrofit 4th of 4, plant B
        # showed its two 7th and 8th of 8. The endpoint's own docstring calls this a
        # "ranking by measured marginal model value per dollar"; it was inverted at the top.
        #
        # It was also backend-dependent: SQLite sorts NULLs first, Postgres sorts them
        # last, so the same request returned a different order depending on which database
        # answered -- exactly what serve/db.py exists to prevent. The boolean-expression
        # form is portable across both; `NULLS LAST` is not (SQLite lacks it).
        "SELECT * FROM instrumentation_plan WHERE plant_id=? "
        "ORDER BY (payback_months IS NULL), payback_months",
        (plant_id,),
    )
    return {
        "plant_id": plant_id,
        "plan": rows,
        "total_investment": sum(r["cost"] for r in rows),
        "total_annual_value": sum(r["annual_value"] for r in rows),
        "maintenance_windows": sorted({r["window"] for r in rows}),
        "method": (
            "delta_pr_auc is measured by group-permutation importance for instrumented "
            "stations, and estimated by analogy to instrumented neighbours for manual "
            "stations (no true readings exist there to ablate)."
        ),
    }


@app.post("/api/feedback")
def feedback(req: FeedbackRequest, request: Request, role: str = Depends(get_role)) -> dict:
    db.execute(
        STATE["feedback"],
        "INSERT INTO feedback (plant_id, unit_id, verdict, created_at) VALUES (?,?,?,?)",
        (req.plant_id, req.unit_id, req.verdict, datetime.now(timezone.utc).isoformat()),
    )
    n = db.query_one(STATE["feedback"], "SELECT COUNT(*) c FROM feedback")
    log_action(request, STATE["ops"], role, "feedback_submitted", "unit", req.unit_id,
               {"plant_id": req.plant_id, "verdict": req.verdict})
    return {"stored": True, "total": n["c"] if n else 0, "persistence": "ephemeral"}
