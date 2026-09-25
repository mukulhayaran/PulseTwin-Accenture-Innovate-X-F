"""Regression tests for the defects found during the end-to-end QA pass (2026-09-16).

Every test here failed before its fix and passes after. They are grouped in one module
because they share a cause rather than a subsystem: each one covers a seam BETWEEN two
components -- detector to API, API to page, publisher to subscriber, image to route table
-- which is precisely where the existing 226 tests had no coverage. Each component was
individually correct and individually tested; the wiring between them was not.

Two of these assert against non-Python files (a Dockerfile CMD, a TypeScript query-string
builder). There is no JS test runner in this repo, and both defects made a headline
feature silently return nothing, so a cheap text assertion is worth more than no
assertion at all.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytest.importorskip("fastapi")

if not (ROOT / "artifacts" / "pulsetwin.db").exists():
    pytest.skip("artifacts not built", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from serve import db  # noqa: E402
from serve.main import app  # noqa: E402

SUPERVISOR = {"X-PulseTwin-Role": "supervisor"}
LEADERSHIP = {"X-PulseTwin-Role": "leadership"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# ------------------------------------------------------------------ drift -> model health


def test_model_health_reports_every_feature_of_a_drift_run(client):
    """mlops/drift_detector.py writes one row per feature, each with its own DEFAULT
    timestamp. model_health._latest_run used to keep only rows matching the single newest
    timestamp, so a 20-feature run surfaced as `features_total: 1`, `features_shifted: 0`,
    `worst_feature: null` and an overall status of "healthy" -- from a run whose own
    output was "10/20 shifted, worst PSI 4.69, WARNING".
    """
    ops = app.state.ops
    features = [f"qa_regress_feat_{i}" for i in range(12)]
    for i, feat in enumerate(features):
        # 0.30 is above PSI_ALERT (0.2) for the first four, healthy for the rest.
        value = 0.30 if i < 4 else 0.01
        db.execute(
            ops,
            """INSERT INTO drift_metrics
                   (model_id, metric_type, feature_name, value, threshold, alert_triggered,
                    window_size, plant_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            ("qa_regress_model", "psi", feat, value, 0.2, value > 0.2, 500, "plant_a"),
        )

    body = client.get("/api/model/health?plant_id=plant_a", headers=LEADERSHIP).json()
    psi = body["drift"]["psi_summary"]

    assert psi["features_total"] >= len(features), (
        f"only {psi['features_total']} feature(s) surfaced from a {len(features)}-feature "
        "run -- the per-run grouping has regressed to exact-timestamp matching"
    )
    assert psi["features_shifted"] >= 4
    assert psi["worst_feature"] is not None
    assert psi["worst_feature"]["psi"] >= 0.30


def _drop_registry_row(ops, model_id: str) -> None:
    """Remove a fixture row from model_registry, before AND after the test that owns it.

    The ops database is NOT a fixture. `serve/db.py::open_ops_db` puts it at
    `%TEMP%/pulsetwin_ops.db` and it survives the process, so a test that INSERTs a fixed
    `model_id` and never deletes it passes exactly once per machine and then fails
    forever with `UNIQUE constraint failed: model_registry.model_id`. Both tests below
    did that; both failed on the second run of the suite on this machine.

    Cleaning up front as well as after matters: the rows from the run that discovered
    this are already on disk, and a test that only cleans up afterwards would still fail
    on its first run after the fix.
    """
    db.execute(ops, "DELETE FROM model_registry WHERE model_id = ?", (model_id,))


def test_model_health_age_days_is_never_negative(client):
    """`trained_at` written with time.strftime (LOCAL naive) but read back as UTC made a
    model trained seconds ago report `age_days: -1` on any host east of Greenwich."""
    ops = app.state.ops
    _drop_registry_row(ops, "qa_regress_champion")
    trained = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.execute(
        ops,
        """INSERT INTO model_registry
               (model_id, model_type, version, status, plant_id, trained_at,
                training_data_range, metrics, artifact_path, notes)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        ("qa_regress_champion", "defect_risk", 1, "champion", "plant_qa_age", trained,
         "", "{}", "", "qa regression"),
    )
    try:
        body = client.get("/api/model/health?plant_id=plant_qa_age",
                          headers=LEADERSHIP).json()
        age = body["current_model"]["age_days"]
        assert age is not None and age >= 0, (
            f"freshly trained model reported age_days={age}")
    finally:
        _drop_registry_row(ops, "qa_regress_champion")


def test_supervisor_can_read_sensor_dropouts(client):
    """/supervisor pins itself to the supervisor role and renders SensorDropoutAlert from
    this data. It used to poll /api/system/health, which requires `manager`, and took a
    silent 403 on every poll -- so the dropout banner could never appear."""
    r = client.get("/api/system/sensor-dropouts?plant_id=plant_a", headers=SUPERVISOR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "dropout_details" in body and isinstance(body["dropout_details"], list)
    assert isinstance(body["sensor_dropouts_active"], int)


def test_sensor_dropouts_endpoint_did_not_widen_the_system_health_gate(client):
    """The fix must be a narrower endpoint, not a weaker gate: connection-pool internals
    and uptime stay manager-and-above (docs/SECURITY.md's RBAC table)."""
    assert client.get("/api/system/health?plant_id=plant_a",
                      headers=SUPERVISOR).status_code == 403


def test_supervisor_page_does_not_call_the_manager_gated_health_endpoint():
    src = (ROOT / "frontend" / "app" / "supervisor" / "page.tsx").read_text(encoding="utf-8")
    assert "getSensorDropouts(" in src
    assert "getSystemHealth(" not in src, (
        "/supervisor calls a manager-gated endpoint while pinned to the supervisor role"
    )


# ------------------------------------------------------------------------- audit trail


def test_audit_log_ignores_absent_filters(client):
    """The Ops AuditLogViewer sent `?role=undefined&action=undefined` (URLSearchParams
    stringifies undefined), which the backend matched as `user_role = 'undefined'`, so the
    panel rendered "No matching entries" against a populated table."""
    client.post("/api/feedback", json={"plant_id": "plant_a", "unit_id": "U_QA_REGRESS",
                                        "verdict": "useful"}, headers=SUPERVISOR)
    unfiltered = client.get("/api/audit/log?limit=50", headers=LEADERSHIP).json()
    assert unfiltered["count"] > 0

    bogus = client.get("/api/audit/log?role=undefined&action=undefined&limit=50",
                        headers=LEADERSHIP).json()
    assert bogus["count"] == 0, "a literal 'undefined' filter must not match real rows"


def test_audit_log_query_builder_drops_empty_params():
    src = (ROOT / "frontend" / "lib" / "api.ts").read_text(encoding="utf-8")
    getter = src[src.index("export const getAuditLog"):]
    getter = getter[:getter.index("export const getAuditActions")]
    assert "filter(" in getter and "undefined" in getter, (
        "getAuditLog must strip undefined/empty params before building the query string"
    )


def test_rejections_still_carry_cors_headers(client):
    """`add_middleware` pushes onto the TOP of the stack, so the last one added is the
    OUTERMOST. RateLimitMiddleware was added after CORSMiddleware and therefore wrapped
    it: its 429 short-circuited and never passed through CORS, so a browser hitting the
    per-role limit saw an opaque CORS failure indistinguishable from the backend being
    down. The same applied to every 403 the RBAC layer raises."""
    origin = {"Origin": "http://localhost:3000", "X-PulseTwin-Role": "supervisor"}
    denied = client.get("/api/system/health", headers=origin)
    assert denied.status_code == 403
    assert denied.headers.get("access-control-allow-origin") == "*", (
        "a 403 reaches the browser as a CORS error rather than a readable rejection"
    )


def test_postgres_boolean_columns_are_not_compared_to_integer_literals():
    """SQLite accepts ``boolean_column = 1`` but PostgreSQL rejects it. The mismatch
    previously made Alert Triage and Shift Summary return HTTP 500 in the Docker stack
    even though their SQLite API tests passed.
    """
    source = (ROOT / "serve" / "routers" / "intelligence.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    executable_strings = "\n".join(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )
    invalid = re.findall(
        r"\b(?:alert_fired|above_cost_threshold|actual_defect)\s*=\s*[01]\b",
        executable_strings,
        flags=re.IGNORECASE,
    )
    assert not invalid, (
        "Boolean SQL must use IS TRUE/IS FALSE so queries work on both SQLite and "
        f"PostgreSQL; found {invalid}"
    )


@pytest.mark.parametrize("plant", ["plant_a", "plant_b"])
def test_instrumentation_plan_leads_with_the_fundable_retrofits(client, plant):
    """`ORDER BY payback_months` sorts NULLs FIRST on SQLite, so this table -- a capital
    recommendation rendered top-to-bottom in the Leadership view -- led with every DEFER
    candidate (value $0, payback undefined) and buried the PROCEED rows at the bottom.
    Plant A showed its one funded retrofit 4th of 4. The endpoint's own docstring calls it
    a "ranking by measured marginal model value per dollar".

    Also backend-dependent: Postgres sorts NULLs last, so the same request returned a
    different order depending on which database answered.
    """
    rows = client.get(f"/api/economics/instrumentation-plan?plant_id={plant}",
                       headers=LEADERSHIP).json()["plan"]
    paybacks = [r["payback_months"] for r in rows]
    real = [p for p in paybacks if p is not None]
    if not real or len(real) == len(paybacks):
        pytest.skip(f"{plant} has no mix of funded and deferred candidates to order")

    assert paybacks[0] is not None, (
        f"{plant}'s retrofit plan leads with a deferred candidate: {paybacks}"
    )
    assert real == sorted(real), f"{plant}'s funded retrofits are not payback-ordered: {real}"
    first_null = paybacks.index(None)
    assert all(p is None for p in paybacks[first_null:]), (
        f"{plant} interleaves deferred candidates among funded ones: {paybacks}"
    )


# ------------------------------------------------------------------- input robustness


@pytest.mark.parametrize("path", ["/api/units?limit=-1", "/api/bottleneck/history?limit=-1"])
def test_negative_limit_does_not_disable_the_cap(client, path):
    """SQLite treats a negative LIMIT as "no limit", so `?limit=-1` turned a capped list
    endpoint into a full scan of the artifact table."""
    capped = client.get(path).json()
    assert len(capped) <= 1, f"{path} returned {len(capped)} rows for limit=-1"


def test_predict_rejects_an_unknown_plant_before_labelling_a_metric(client):
    """predictions_total was incremented with the caller's raw plant_id before validation,
    letting unauthenticated input mint unbounded Prometheus label values."""
    assert client.post("/api/predict/defect",
                        json={"plant_id": "plant_not_real_qa", "features": {}}).status_code == 404
    series = client.get("/metrics").text
    assert 'plant_id="plant_not_real_qa"' not in series


def test_ingestion_lag_survives_a_naive_last_reading_timestamp(client):
    """`sensor_health.timestamp` is UTC-aware (column DEFAULT, '...Z'); `last_reading_at`
    is written naive by ingestion/ from a replayed event. Subtracting them raised
    TypeError, which the handler did not catch -- so one dropout row turned
    GET /api/system/ingestion-lag into a bare 500 and killed the Ops Ingestion Lag panel.
    The endpoint looked healthy right up until a sensor actually dropped out.
    """
    db.execute(
        app.state.ops,
        """INSERT INTO sensor_health
               (station_id, sensor_type, status, last_reading_at, dropout_duration_s, plant_id)
           VALUES (?,?,?,?,?,?)""",
        (8, "vibration_mm_s", "dropout", "2026-01-28T06:02:07", 180.0, "plant_a"),
    )
    r = client.get("/api/system/ingestion-lag?plant_id=plant_a",
                    headers={"X-PulseTwin-Role": "manager"})
    assert r.status_code == 200, r.text
    assert r.json()["samples"] >= 1


# ------------------------------------------------------------------- MQTT ingestion


def test_reorder_window_actually_buffers_and_reorders():
    """The window cut `event_ts <= wall_clock_now - 60`. Replayed events carry SIMULATED
    timestamps months in the past, so that was true on arrival for every record: the heap
    drained immediately and out-of-order arrivals were written in arrival order. The F2
    acceptance criterion "late-arriving messages are correctly reordered" was not met.
    """
    pytest.importorskip("paho.mqtt.client")
    import itertools

    from ingestion.mqtt_subscriber import REORDER_WINDOW_S, Ingestor

    from collections import OrderedDict

    ing = Ingestor.__new__(Ingestor)
    ing.reorder_heap, ing._seq = [], itertools.count()
    ing._pending_signals = OrderedDict()  # set by __init__ (ADR-059), bypassed here
    ing.watermark = ing.last_message_at = 0.0
    written: list[float] = []
    ing._write_reading = lambda rec: written.append(rec["event_ts"])

    class Parsed:
        plant_id, station_id, kind = "plant_a", 1, "telemetry"

    base = datetime(2026, 1, 28, 6, 0, 0)
    # Deliver newest-first; the oldest event arrives LAST, i.e. late.
    for offset in (90, 60, 30, 0):
        body = {"ts": (base + timedelta(seconds=offset)).isoformat(), "unit_id": "U1",
                "emitted_at": 1_000_000.0, "torque_nm": 50.0}
        ing._queue_telemetry(Parsed(), body, 1_000_000.0)

    ing.flush_ready(1_000_000.5)
    assert ing.reorder_heap, "the reorder window buffered nothing"
    assert written == sorted(written), "records were not written in event order"

    ing.flush_ready(1_000_000.0 + REORDER_WINDOW_S + 5)   # idle drain
    assert not ing.reorder_heap, "idle drain left records stranded in the buffer"
    assert len(written) == 4


def test_timeliness_measures_transport_lag_not_replay_age():
    """Timeliness compared arrival against the SIMULATED event timestamp, making every
    reading ~231 days late. It scored 0.0 for every station forever, capping every
    data-quality score at 0.70 no matter how healthy the feed."""
    from ingestion.data_quality import DataQualityTracker

    fresh = DataQualityTracker()
    fresh.record("plant_a", 1, "torque_nm", 48.0,
                 event_ts=1_000_000.0, arrival_ts=1_000_000.05, nominal_cycle_s=60.0)
    assert fresh.score("plant_a", 1)["timeliness"] == 1.0

    slow = DataQualityTracker()
    slow.record("plant_a", 1, "torque_nm", 48.0,
                event_ts=1_000_000.0, arrival_ts=1_000_400.0, nominal_cycle_s=60.0)
    assert slow.score("plant_a", 1)["timeliness"] == 0.0


def test_publisher_stamps_a_wall_clock_alongside_the_event_timestamp():
    """The subscriber cannot distinguish a fresh replayed reading from a stale one unless
    the publisher carries both clocks, the way OPC-UA carries Source and Server
    timestamps."""
    src = (ROOT / "ingestion" / "mqtt_publisher.py").read_text(encoding="utf-8")
    assert '"emitted_at": wall_now' in src


# ---------------------------------------------------------------------- packaging


def test_frontend_image_does_not_serve_every_route_as_the_landing_page():
    """`serve -s` is single-page-app mode: it rewrites every non-file path to index.html.
    Next's static export is MULTI-page, so /supervisor, /manager, /leadership, /ops and
    /login all returned the landing page byte-for-byte in the container. Never seen in
    dev, because `next dev` does its own routing."""
    cmd = [ln for ln in (ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")
           .splitlines() if ln.startswith("CMD")]
    assert cmd, "frontend/Dockerfile has no CMD"
    assert '"-s"' not in cmd[0] and "--single" not in cmd[0], (
        f"{cmd[0]} serves the landing page for every persona route"
    )
