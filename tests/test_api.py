"""API contract tests against the committed artifacts.

Every endpoint is exercised with the real database — no mocks — so a schema drift between
the artifact builder and the serving layer fails here rather than on stage.
"""

from __future__ import annotations

import re
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


@pytest.mark.parametrize("plant", PLANTS)
def test_station_stats_returns_one_row_per_station(client, plant):
    """/api/stations/stats had NO test calling it over HTTP until this one -- confirmed by
    grep before writing it. Its GROUP BY selected `provenance` without aggregating it or
    grouping by it, which SQLite's lenient extension tolerates (silently picking an
    unspecified row's value) and PostgreSQL rejects outright
    (`GroupingError: column "station_stats.provenance" must appear in the GROUP BY
    clause`) -- a crash on EVERY call, found by a security probe against the real
    docker-compose.prod.yml stack, not by this suite. This test cannot reproduce the
    Postgres-specific failure (nothing here runs against Postgres -- see
    reports/scalability-review.md's CI recommendation), but it closes the "never called at
    all" half of the gap and pins the shape so a future edit cannot silently reintroduce an
    invalid GROUP BY without at least breaking a passing assertion on SQLite.
    """
    body = client.get(f"/api/stations/stats?plant_id={plant}").json()
    assert body, "no station rows returned"
    for row in body:
        assert row["provenance"] in ("measured", "inferred")
        assert isinstance(row["station_id"], int)
        assert row["util"] is not None
    losses = [r["lost_uph"] for r in body]
    assert losses == sorted(losses, reverse=True)


@pytest.mark.parametrize("plant", PLANTS)
def test_every_inferred_station_stat_carries_its_interval(client, plant):
    """Contract #1 (ADR-060). A live shift_intel run was rejected `missing_interval`
    because this endpoint shipped inferred rows with no interval to cite."""
    body = client.get(f"/api/stations/stats?plant_id={plant}").json()
    inferred = [r for r in body if r["provenance"] == "inferred"]
    assert inferred, "the plant config has manual (unsensored) stations"
    for row in inferred:
        for field in ("util", "blocked", "starved", "down", "lost_uph"):
            assert row[f"{field}_ci_low"] <= row[field] <= row[f"{field}_ci_high"]
    for row in body:
        if row["provenance"] == "measured":
            assert "lost_uph_ci_low" not in row  # measured values carry no interval


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
    # Group-level overlap is intentionally exposed; individual simulated IDs such
    # as ``A_OP_004`` / ``B_OP_011`` must never cross the API boundary.
    assert not re.search(r"\b[ab]_op_\d+\b", blob), (
        "raw operator id leaked into the containment response"
    )
    assert "shift_a_op" not in blob
    for u in body["at_risk_units"]:
        assert u["action"] in ("HOLD", "INSPECT", "MONITOR", "INSPECTED")


def test_containment_ranks_on_evidence_and_reports_known_outcomes(client):
    """Alerting units share one score, so risk cannot order containment. Vehicles still
    on the line come first, by evidence; a vehicle already through final inspection
    carries its outcome and no action."""
    top = client.get("/api/units?plant_id=plant_a&alerts_only=true&limit=1").json()[0]
    at = top["prediction_ts"].replace("+00:00", "")
    units = client.get(f"/api/trace/containment/{top['unit_id']}?plant_id=plant_a"
                       f"&at_ts={at}").json()["at_risk_units"]
    assert units
    done = [u["action"] == "INSPECTED" for u in units]
    assert done == sorted(done), "an inspected vehicle ranked above one still on the line"
    for u in units:
        if u["action"] == "INSPECTED":
            assert u["inspection_outcome"] in (0, 1)
        else:
            assert u["inspection_outcome"] is None      # truth not known yet: causal
    pending = [len(u["shared_factors"]) for u in units if u["action"] != "INSPECTED"]
    assert pending == sorted(pending, reverse=True)
    # without a point in time there is no "already inspected": behaviour is unchanged
    timeless = client.get(f"/api/trace/containment/{top['unit_id']}?plant_id=plant_a").json()
    assert all(u["action"] != "INSPECTED" for u in timeless["at_risk_units"])
    # an hour on, the whole window has left the line: outcomes known, decisions refused
    from datetime import datetime, timedelta
    later = (datetime.fromisoformat(at) + timedelta(hours=1)).isoformat()
    after = client.get(f"/api/trace/containment/{top['unit_id']}?plant_id=plant_a"
                       f"&at_ts={later}").json()["at_risk_units"]
    assert after and all(u["action"] == "INSPECTED" for u in after)
    assert sum(u["inspection_outcome"] for u in after) >= 0


def test_containment_operator_group_overlap_uses_only_visible_journey(client):
    from serve import db

    flagged = client.get("/api/units?plant_id=plant_a&alerts_only=true&limit=1").json()[0]
    at_ts = flagged["prediction_ts"]
    body = client.get(
        f"/api/trace/containment/{flagged['unit_id']}?plant_id=plant_a&at_ts={at_ts}"
    ).json()

    def groups(unit_id: str) -> set[str]:
        return {
            row["operator_group"] for row in db.query_all(
                app.state.main_con,
                """SELECT DISTINCT operator_group FROM genealogy
                     WHERE plant_id=? AND unit_id=? AND operator_group IS NOT NULL
                       AND entry_ts<=?""",
                ("plant_a", unit_id, at_ts),
            )
        }

    flagged_groups = groups(flagged["unit_id"])
    for peer in body["at_risk_units"]:
        visible_overlap = bool(flagged_groups & groups(peer["unit_id"]))
        assert ("same_operator_group" in peer["shared_factors"]) is visible_overlap


def test_manual_containment_decision_is_saved_separately_from_recommendation(client):
    top = client.get("/api/units?plant_id=plant_a&alerts_only=true&limit=1").json()[0]
    path = f"/api/trace/containment/{top['unit_id']}?plant_id=plant_a"
    before = client.get(path).json()
    assert before["at_risk_units"]
    target = before["at_risk_units"][0]
    manual = "HOLD" if target["action"] != "HOLD" else "MONITOR"

    r = client.post("/api/trace/containment/decisions", json={
        "plant_id": "plant_a",
        "flagged_unit_id": top["unit_id"],
        "target_unit_id": target["unit_id"],
        "selected_action": manual,
        "message": "Inspect at the next quality gate before release.",
        "owner": "quality-gate-team",
        "due_station": 29,
        "due_at": "2026-09-22T12:00:00Z",
    }, headers={"X-PulseTwin-User": "test-supervisor"})
    assert r.status_code == 200
    saved = r.json()
    assert saved["stored"] is True
    assert saved["recommended_action"] == target["action"]
    assert saved["selected_action"] == manual
    assert saved["decided_by_user"] == "test-supervisor"

    history = client.get("/api/trace/containment-decisions?plant_id=plant_a&limit=1").json()[0]
    assert history["owner"] == "quality-gate-team"
    assert history["due_station"] == 29
    assert history["due_at"] == "2026-09-22T12:00:00Z"

    after_target = next(
        unit for unit in client.get(path).json()["at_risk_units"]
        if unit["unit_id"] == target["unit_id"]
    )
    assert after_target["action"] == target["action"]
    assert after_target["manual_action"] == manual
    assert after_target["decision_recorded_at"]


def test_manual_containment_decision_requires_a_useful_message(client):
    top = client.get("/api/units?plant_id=plant_a&alerts_only=true&limit=1").json()[0]
    containment_body = client.get(
        f"/api/trace/containment/{top['unit_id']}?plant_id=plant_a"
    ).json()
    target = containment_body["at_risk_units"][0]
    r = client.post("/api/trace/containment/decisions", json={
        "plant_id": "plant_a",
        "flagged_unit_id": top["unit_id"],
        "target_unit_id": target["unit_id"],
        "selected_action": "INSPECT",
        "message": "    ",
    })
    assert r.status_code == 422


def test_manual_containment_decision_rejects_operator_identifiers(client):
    top = client.get("/api/units?plant_id=plant_a&alerts_only=true&limit=1").json()[0]
    containment_body = client.get(
        f"/api/trace/containment/{top['unit_id']}?plant_id=plant_a"
    ).json()
    target = containment_body["at_risk_units"][0]
    r = client.post("/api/trace/containment/decisions", json={
        "plant_id": "plant_a",
        "flagged_unit_id": top["unit_id"],
        "target_unit_id": target["unit_id"],
        "selected_action": "INSPECT",
        "message": "Ask operator ID 123 to repeat the check.",
    })
    assert r.status_code == 422


def test_manual_containment_decision_rejects_a_unit_outside_the_group(client):
    top = client.get("/api/units?plant_id=plant_a&alerts_only=true&limit=1").json()[0]
    r = client.post("/api/trace/containment/decisions", json={
        "plant_id": "plant_a",
        "flagged_unit_id": top["unit_id"],
        "target_unit_id": "NOT-IN-THIS-GROUP",
        "selected_action": "HOLD",
        "message": "Hold for inspection.",
    })
    assert r.status_code == 404


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
    assert body["model_id"].startswith(f"{plant}_defect_risk_")
    assert body["model_version"] >= 1
    assert body["feature_set_version"]
    assert body["features_available"] + body["features_missing"] > 0
    assert 0 <= body["data_completeness_pct"] <= 100
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



def test_a_fresh_replay_can_start_just_before_the_first_alert(client):
    """The committed shift opens with ~55 quiet minutes: at 10x the alert feed sat empty
    for ~5.5 minutes after Start or restart, which read as broken. `start=first_alert`
    seeks to FIRST_ALERT_LEAD_S before the first alert. (Streamed end to end against a
    live server; the in-process TestClient blocks on a mid-file seek, so this pins the
    seek itself.)"""
    import json as _json
    from datetime import datetime, timedelta

    import serve.main as M
    from serve import db

    path = M.ARTIFACTS / "stream_plant_a.jsonl"
    first = M._replay_first_alert("plant_a", path)
    with path.open(encoding="utf-8") as fh:
        truth = next(_json.loads(l)["ts"] for l in fh if _json.loads(l).get("alert"))
    assert first == truth

    target = (datetime.fromisoformat(first)
              - timedelta(seconds=M.FIRST_ALERT_LEAD_S)).isoformat()
    row = db.query_one(M.STATE["con"], "SELECT ts FROM stream_index WHERE plant_id=? "
                       "AND ts>=? ORDER BY ts LIMIT 1", ("plant_a", target))
    lead = (datetime.fromisoformat(first) - datetime.fromisoformat(row["ts"])).total_seconds()
    assert 0 < lead <= M.FIRST_ALERT_LEAD_S  # starts before the alert, not at the shift top
    assert client.get("/api/stream?plant_id=plant_a&start=nonsense").status_code == 422

