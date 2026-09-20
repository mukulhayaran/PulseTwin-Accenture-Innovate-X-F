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

from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def test_registry_timestamps_are_utc_not_local():
    """A local-naive stamp and a UTC one differ by the host offset; on UTC+5:30 that was
    enough to make a just-trained model look a day old in the future."""
    from mlops.model_registry import _utc_now

    stamped = datetime.fromisoformat(_utc_now().replace("Z", "+00:00"))
    assert abs((datetime.now(timezone.utc) - stamped).total_seconds()) < 120


# ---------------------------------------------------------------------- RBAC / supervisor


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


# --------------------------------------------------------------------- MLOps lifecycle


def test_a_failed_retrain_is_recorded_in_the_registry(tmp_path, monkeypatch):
    """retrainer.run() calls mark_failed() from its except block, but training usually
    fails BEFORE register() inserts the row -- and mark_failed was UPDATE-only, so it
    matched zero rows and the failure left no trace. `failed` is one of the four states
    migrations/003 defines and it was unreachable on the path that produces it."""
    monkeypatch.setenv("PULSETWIN_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.setenv("PULSETWIN_JOBS_DIR", str(tmp_path))

    from mlops import model_registry

    model_registry.mark_failed("plant_a_defect_v999999", notes="simulated training failure")
    row = model_registry.get("plant_a_defect_v999999")
    assert row is not None, "a failed challenger left no row in the registry"
    assert row["status"] == "failed"
    assert row["plant_id"] == "plant_a"


def test_promotion_actually_changes_what_is_served(client):
    """docs/MLOPS_LIFECYCLE.md stage 7 says POST /api/admin/promote-model "is the only
    code path that changes what /api/predict/defect actually serves". It changed a
    registry row and nothing else: /api/predict/defect returned a byte-identical
    probability before and after promoting a different model. Promotion was cosmetic.
    """
    ops = app.state.ops
    system = {"X-PulseTwin-Role": "system"}
    # Register the plant's OWN committed artifact as a challenger, then promote it. The
    # swap is observable through the response contract without assuming two models differ.
    artifact = ROOT / "artifacts" / "model_plant_a.npz"
    if not artifact.exists():
        pytest.skip("plant_a model artifact not built")
    _drop_registry_row(ops, "qa_regress_swap")
    db.execute(
        ops,
        """INSERT INTO model_registry
               (model_id, model_type, version, status, plant_id, trained_at,
                training_data_range, metrics, artifact_path, notes)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        ("qa_regress_swap", "defect_risk", 2, "challenger", "plant_a",
         datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "", "{}",
         str(artifact), "qa regression"),
    )
    try:
        r = client.post("/api/admin/promote-model", json={"model_id": "qa_regress_swap"},
                        headers=system)
        assert r.status_code == 200, r.text
        serving = r.json().get("serving")
        assert serving is not None, (
            "promote-model does not report whether it swapped the scorer")
        assert serving["swapped"] is True, f"scorer was not hot-swapped: {serving}"
    finally:
        # Promotion RETIRES the previous champion, so leaving this row behind also leaves
        # plant_a with a champion pointing at a test fixture for every later test in the
        # session and every later run on this machine.
        _drop_registry_row(ops, "qa_regress_swap")


def test_challenger_artifacts_are_pruned(tmp_path, monkeypatch):
    """One .npz per challenger is what makes `artifact_path` trustworthy, but one file per
    run with nothing pruning them grows without bound -- a suite that retrains a few times
    per run fills artifacts/ by itself. Cap kept well above any plausible rollback depth so
    a rollback target is never the file this deletes."""
    from mlops import retrainer

    monkeypatch.setattr(retrainer, "ARTIFACTS", tmp_path)
    for i in range(retrainer.KEEP_CHALLENGER_ARTIFACTS + 5):
        (tmp_path / f"model_plant_a_defect_v{1000 + i}.npz").write_bytes(b"x")
    (tmp_path / "model_plant_a.npz").write_bytes(b"keep-me")
    (tmp_path / "model_plant_b_defect_v1.npz").write_bytes(b"other-plant")

    retrainer._prune_challenger_artifacts("plant_a")

    assert len(list(tmp_path.glob("model_plant_a_defect_v*.npz"))) == \
        retrainer.KEEP_CHALLENGER_ARTIFACTS
    assert (tmp_path / "model_plant_a.npz").exists(), "pruned the committed champion artifact"
    assert (tmp_path / "model_plant_b_defect_v1.npz").exists(), "pruned another plant's artifact"


def test_each_challenger_gets_its_own_artifact_file():
    """Every registry row pointed at one reused `model_<plant>_challenger.npz`, so the
    next retrain overwrote the weights behind a row already marked `champion` -- and a
    promotion reading `artifact_path` would load whatever the latest run happened to
    leave there."""
    src = (ROOT / "mlops" / "retrainer.py").read_text(encoding="utf-8")
    assert 'f"model_{challenger_id}.npz"' in src, (
        "challengers still share a single fixed .npz path"
    )


def test_rollback_records_an_approver_in_both_implementations():
    """serve/routers/admin.py and mlops/model_registry.py hold duplicate promote/rollback
    SQL and their own docstrings say to keep them in sync. The registry copy set only
    promoted_at on rollback, so the same action recorded an approver through the API and
    none through the module."""
    reg = (ROOT / "mlops" / "model_registry.py").read_text(encoding="utf-8")
    rollback_body = reg[reg.index("def rollback("):reg.index("def mark_failed(")]
    assert "promoted_by=?" in rollback_body


# ------------------------------------------------------------------- ingestion seams


def test_reorder_window_actually_buffers_and_reorders():
    """The window cut `event_ts <= wall_clock_now - 60`. Replayed events carry SIMULATED
    timestamps months in the past, so that was true on arrival for every record: the heap
    drained immediately and out-of-order arrivals were written in arrival order. The F2
    acceptance criterion "late-arriving messages are correctly reordered" was not met.
    """
    pytest.importorskip("paho.mqtt.client")
    import itertools

    from ingestion.mqtt_subscriber import REORDER_WINDOW_S, Ingestor

    ing = Ingestor.__new__(Ingestor)
    ing.reorder_heap, ing._seq = [], itertools.count()
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
