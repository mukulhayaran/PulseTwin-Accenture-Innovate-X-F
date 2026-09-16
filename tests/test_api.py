"""API contract tests against the committed artifacts.

Every endpoint is exercised with the real database — no mocks — so a schema drift between
the artifact builder and the serving layer fails here rather than on stage.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]

pytest.importorskip("fastapi")

if not (ROOT / "artifacts" / "pulsetwin.db").exists():
    pytest.skip("artifacts not built", allow_module_level=True)

from serve.main import app  # noqa: E402

PLANTS = ["plant_a", "plant_b"]


@pytest.fixture(scope="module")
def client():
    # Finals brief Phase F4: several endpoints now require a role (serve/middleware/
    # auth.py). "leadership" satisfies every non-`system` gate in this suite -- role
    # ENFORCEMENT itself is covered by tests/test_auth.py, not re-tested per endpoint here.
    with TestClient(app, headers={"X-PulseTwin-Role": "leadership"}) as c:
        yield c


# --------------------------------------------------------------------------- health


def test_health_reports_both_plants(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert set(body["plants"]) == set(PLANTS)


def test_health_does_not_throw_without_proc(client):
    """ADR-018. /proc/self/statm does not exist on Windows and psutil is banned, so the
    health endpoint must degrade rather than 500 during local development."""
    body = client.get("/api/health").json()
    assert body["rss_source"] in ("proc", "unavailable")
    if body["rss_source"] == "unavailable":
        assert body["rss_mb"] is None


# ---------------------------------------------------------------------------- config


@pytest.mark.parametrize("plant", PLANTS)
def test_stations_match_the_configured_line(client, plant):
    from offline.config_schema import load_plant_config

    cfg = load_plant_config(f"{plant}.yaml")
    body = client.get(f"/api/config/stations?plant_id={plant}").json()
    assert len(body["stations"]) == len(cfg.stations)
    assert {z["id"] for z in body["zones"]} == {z.id for z in cfg.zones}

    manual = [s for s in body["stations"] if s["sensor_tier"] == "manual"]
    assert len(manual) == len(cfg.manual_stations())
    for s in manual:
        assert s["sensors"] == [], "a manual station must expose no continuous sensors"


def test_unknown_plant_is_404(client):
    assert client.get("/api/config/stations?plant_id=nope").status_code == 404


# ---------------------------------------------------------------------------- units


@pytest.mark.parametrize("plant", PLANTS)
def test_alerts_are_ranked_and_above_threshold(client, plant):
    rows = client.get(f"/api/units?plant_id={plant}&alerts_only=true&limit=20").json()
    assert rows
    risks = [r["calibrated_risk"] for r in rows]
    assert risks == sorted(risks, reverse=True)
    assert all(r["alert_fired"] == 1 for r in rows)


@pytest.mark.parametrize("plant", PLANTS)
def test_unit_detail_carries_factors_and_journey(client, plant):
    top = client.get(f"/api/units?plant_id={plant}&alerts_only=true&limit=1").json()[0]
    body = client.get(f"/api/units/{top['unit_id']}?plant_id={plant}").json()
    assert body["factors"], "an alerting unit must carry explanations"
    assert all(f["plain_language"] for f in body["factors"])
    assert body["journey"], "a unit must have a station journey"


def test_unknown_unit_is_404(client):
    assert client.get("/api/units/NOPE?plant_id=plant_a").status_code == 404


# ----------------------------------------------------------------------- containment


@pytest.mark.parametrize("plant", PLANTS)
def test_containment_never_exposes_operator_identity(client, plant):
    """CLAUDE.md §3 / ADR-017. Individual operator attribution is a hard rule, and this
    endpoint is the one place the join genuinely needs the linkage."""
    top = client.get(f"/api/units?plant_id={plant}&alerts_only=true&limit=1").json()[0]
    body = client.get(f"/api/trace/containment/{top['unit_id']}?plant_id={plant}").json()

    blob = str(body).lower()
    assert "_op" not in blob, "raw operator id leaked into the containment response"
    assert "shift_a_op" not in blob
    for u in body["at_risk_units"]:
        assert u["action"] in ("HOLD", "INSPECT", "MONITOR")


# ------------------------------------------------------------------------ bottleneck


@pytest.mark.parametrize("plant", PLANTS)
def test_bottleneck_accuracy_is_not_circular(client, plant):
    """A detector scored against its own definition returns 100%. If this ever reads 1.0
    again, the ground truth has collapsed back onto the detector."""
    body = client.get(f"/api/bottleneck/current?plant_id={plant}").json()
    acc = body["detection_accuracy"]
    assert acc is not None
    assert 0.4 < acc < 0.99, f"suspicious detection accuracy {acc}"


@pytest.mark.parametrize("plant", PLANTS)
def test_bottleneck_history_shows_the_constraint_moving(client, plant):
    rows = client.get(f"/api/bottleneck/history?plant_id={plant}&limit=500").json()
    assert rows
    assert len({r["bottleneck_station"] for r in rows}) > 1, "constraint never moved"


# --------------------------------------------------------------------------- metrics


@pytest.mark.parametrize("plant", PLANTS)
def test_metrics_carry_intervals_and_the_oracle(client, plant):
    body = client.get(f"/api/metrics?plant_id={plant}").json()
    v = body["values"]
    assert v["pr_auc_ci_low"] < v["pr_auc"] < v["pr_auc_ci_high"]
    assert v["oracle_pr_auc"] >= v["pr_auc"], "model cannot beat the oracle ceiling"
    assert 0 < v["oracle_capture"] <= 1.0
    assert body["reliability"] and body["alarm_budget_curve"]


@pytest.mark.parametrize("plant", PLANTS)
def test_the_two_thresholds_still_disagree(client, plant):
    """ADR-004. If these ever converge, the dual-threshold story is false and the
    Leadership view needs rewriting."""
    v = client.get(f"/api/metrics?plant_id={plant}").json()["values"]
    assert v["budget_threshold"] > 10 * v["cost_optimal_threshold"]


# --------------------------------------------------------------------------- predict


@pytest.mark.parametrize("plant", PLANTS)
def test_predict_returns_honest_explanation_mode(client, plant):
    """Never call something SHAP that is not SHAP (AUDIT M15)."""
    r = client.post("/api/predict/defect", json={
        "plant_id": plant,
        "features": {"paint_buffer_dwell_s": 620.0},
    })
    assert r.status_code == 200
    body = r.json()
    assert 0.0 <= body["probability"] <= 1.0
    assert body["explanation_mode"] == "decision_path"
    assert "shap" not in str(body).lower()
    lo, hi = body["confidence_interval"]
    assert lo <= body["probability"] <= hi


# ---------------------------------------------------------------------------- whatif


@pytest.mark.parametrize("plant", PLANTS)
def test_whatif_grid_covers_both_constraints(client, plant):
    """A grid seeded from only the most frequent constraint omitted the variant-heavy-day
    bottleneck, which is the one a manager actually wants to explore."""
    rows = client.get(f"/api/whatif/stations?plant_id={plant}").json()
    covered = {r["station_id"] for r in rows}
    assert len(covered) >= 4

    hist = client.get(f"/api/bottleneck/history?plant_id={plant}&limit=700").json()
    seen: dict[int, int] = {}
    for h in hist:
        seen[h["bottleneck_station"]] = seen.get(h["bottleneck_station"], 0) + 1
    top_two = sorted(seen, key=seen.get, reverse=True)[:2]
    assert set(top_two) <= covered, f"grid {sorted(covered)} misses constraint(s) {top_two}"


@pytest.mark.parametrize("plant", PLANTS)
def test_whatif_never_interpolates_a_station_id(client, plant):
    """ADR-007. The mean of S12 and S19 is not S15.5."""
    sid = client.get(f"/api/whatif/stations?plant_id={plant}").json()[0]["station_id"]
    body = client.post("/api/whatif", json={
        "plant_id": plant, "station_id": sid, "buffer_delta": 3, "cycle_delta_pct": -5.0,
    }).json()
    assert body["bottleneck_mode"] == "nearest_neighbour"
    assert float(body["projected"]["bottleneck_station"]).is_integer()
    assert body["assumptions"]


def test_whatif_rejects_a_station_outside_the_grid(client):
    r = client.post("/api/whatif", json={
        "plant_id": "plant_a", "station_id": 999, "buffer_delta": 0, "cycle_delta_pct": 0,
    })
    assert r.status_code == 404
    assert "available" in r.json()["detail"]


# ------------------------------------------------------------------------- economics


@pytest.mark.parametrize("plant", PLANTS)
def test_achieved_throughput_is_plausible(client, plant):
    """Guards a silent factor-of-25 error in the entire business case.

    `achieved_uph` was computed from the replay-window unit count divided by the full
    28-day horizon, reporting 2.1 UPH for a line running at 53.8. Nothing failed -- the
    number just flowed into units_per_year and understated every ROI figure.
    """
    from offline.config_schema import load_plant_config

    cfg = load_plant_config(f"{plant}.yaml")
    body = client.get(f"/api/economics/roi?plant_id={plant}").json()
    achieved = body["basis"]["achieved_uph"]
    target = cfg.units_per_hour_target
    assert 0.8 * target < achieved < 1.05 * target, (
        f"{plant}: achieved {achieved:.1f} UPH against a {target} UPH target"
    )
    # and it must actually reach the annual unit count
    assert body["basis"]["units_per_year"] > 100_000


@pytest.mark.parametrize("plant", PLANTS)
def test_roi_is_derived_from_measured_recall(client, plant):
    body = client.get(f"/api/economics/roi?plant_id={plant}").json()
    assert body["basis"]["recall_at_budget"] > 0
    metrics = client.get(f"/api/metrics?plant_id={plant}").json()["values"]
    assert abs(body["basis"]["recall_at_budget"] - metrics["recall_at_budget"]) < 1e-9


@pytest.mark.parametrize("plant", PLANTS)
def test_every_roi_component_is_sourced(client, plant):
    """No dollar figure without provenance (CLAUDE.md §3)."""
    body = client.get(f"/api/economics/roi?plant_id={plant}").json()
    for c in body["components"]:
        assert c["source"], f"{c['label']} has no source"


def test_scenarios_move_the_value(client):
    lo = client.get("/api/economics/roi?plant_id=plant_a&scenario=pessimistic").json()
    base = client.get("/api/economics/roi?plant_id=plant_a&scenario=base").json()
    hi = client.get("/api/economics/roi?plant_id=plant_a&scenario=optimistic").json()
    assert lo["net_value"] < base["net_value"] < hi["net_value"]


@pytest.mark.parametrize("plant", PLANTS)
def test_instrumentation_plan_is_ranked_and_labelled(client, plant):
    body = client.get(f"/api/economics/instrumentation-plan?plant_id={plant}").json()
    assert body["plan"]
    assert body["method"], "the estimate method must be stated, not implied"
    for r in body["plan"]:
        assert r["cost_source"], "a capital figure without a source is not usable"
        assert r["recommendation"]


# ---------------------------------------------------------------------------- stream


def test_stream_sets_proxy_hardening_headers(client):
    """ADR-009. Without X-Accel-Buffering the stream works on localhost and delivers
    nothing through Hugging Face Spaces' proxy."""
    with client.stream("GET", "/api/stream?plant_id=plant_a&speed=400") as r:
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-cache"
        assert r.headers["x-accel-buffering"] == "no"
        assert "text/event-stream" in r.headers["content-type"]


def test_stream_emits_events_and_a_server_side_budget(client):
    """ADR-012. The alert budget is computed on the server, so it survives a refresh."""
    seen = []
    with client.stream("GET", "/api/stream?plant_id=plant_a&speed=400") as r:
        for line in r.iter_lines():
            if line.startswith("data: "):
                seen.append(line)
            if len(seen) >= 25:
                break
    assert len(seen) >= 25
    import json as _json

    payloads = [_json.loads(s[6:]) for s in seen]
    assert any("alerts_budget" in p for p in payloads)


# -------------------------------------------------------------------------- feedback


def test_feedback_does_not_write_to_the_read_only_artifact(client):
    """ADR-011. A write into the committed DB would break reproducibility."""
    top = client.get("/api/units?plant_id=plant_a&alerts_only=true&limit=1").json()[0]
    r = client.post("/api/feedback", json={
        "plant_id": "plant_a", "unit_id": top["unit_id"], "verdict": "useful",
    })
    assert r.status_code == 200
    assert r.json()["stored"] is True
    assert r.json()["persistence"] == "ephemeral"


def test_feedback_rejects_an_invalid_verdict(client):
    r = client.post("/api/feedback", json={
        "plant_id": "plant_a", "unit_id": "U000001", "verdict": "maybe",
    })
    assert r.status_code == 422
