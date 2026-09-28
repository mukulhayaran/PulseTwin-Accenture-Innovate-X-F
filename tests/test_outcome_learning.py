"""ADR-064: deterministic outcome evaluation and structured operational memory.

Synthetic live-twin events with a known answer, so every verdict and projection error
below can be checked by hand. The classification thresholds are read from
configs/alert_policies.yaml through serve.policy -- never hardcoded in the assertions.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from serve import db, outcomes as O
from serve.main import app

MANAGER = {"X-PulseTwin-Role": "manager"}
T0 = datetime(2026, 1, 28, 8, 0, 0)
RUN = "SIM-OUTCOME-TEST"


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("PULSETWIN_OPS_DB", str(tmp_path / "ops.db"))
    with TestClient(app) as c:
        yield c


def _ops():
    return app.state.ops


def _events(n: int, station: int, stalled_until: int, start_seq: int = 1) -> None:
    """One event a simulated minute; the first `stalled_until` are starved."""
    for k in range(n):
        seq = start_seq + k
        ts = (T0 + timedelta(minutes=seq)).isoformat()
        state = "starved" if seq <= stalled_until else "working"
        db.execute(_ops(), "INSERT INTO simulation_events (run_id, seq, ts, event_type, "
                           "unit_id, station_id, payload) VALUES (?,?,?,?,?,?,?)",
                   (RUN, seq, ts, "station_update", f"U{seq}", station,
                    json.dumps({"ts": ts, "station_id": station, "station_state": state})))


def _proposal(pid: str = "P-T1", station: int = 12, detector: str = "starvation") -> dict:
    db.execute(_ops(), "INSERT INTO action_proposals (proposal_id, sim_run_id, plant_id, "
                       "detector, station_id, fired_at, trigger_evidence, triage_run_id, "
                       "status) VALUES (?,?,?,?,?,?,?,?,'OPEN')",
               (pid, RUN, "plant_a", detector, station, T0.isoformat(),
                json.dumps({"stalled_share": 0.9}), f"run-{pid}"))
    return db.query_one(_ops(), "SELECT * FROM action_proposals WHERE proposal_id=?", (pid,))


def _experiment_for(pid: str, stalled_base: float, stalled_fix: float) -> None:
    """A finished experiment whose feed-buffer scenario projects a known stalled change."""
    eid = f"EXP-{pid}"
    db.execute(_ops(), "INSERT INTO simulation_experiments (experiment_id, created_at, "
                       "plant_id, station_id, objective, origin_proposal_id, requested_by, "
                       "status, horizon_s, seeds, interventions, simulator_version) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
               (eid, T0.isoformat(), "plant_a", 12, "starvation", pid, "manager", "done",
                3600.0, "[1,2,3]", "[]", "live_twin@test"))
    for sid, ordinal, kind, params, base in (
            (f"{eid}-0", 0, None, None, 1),
            (f"{eid}-1", 1, "mitigation", json.dumps({"action_type": "increase_feed_buffer"}), 0)):
        db.execute(_ops(), "INSERT INTO simulation_scenarios (scenario_id, experiment_id, "
                           "ordinal, label, intervention_type, params, is_baseline, supported) "
                           "VALUES (?,?,?,?,?,?,?,1)", (sid, eid, ordinal, sid, kind, params, base))
        value = stalled_base if base else stalled_fix
        for seed in (1, 2, 3):
            db.execute(_ops(), "INSERT INTO simulation_scenario_results (created_at, "
                               "experiment_id, scenario_id, seed, status, starved_pct, "
                               "blocked_pct, down_pct) VALUES (?,?,?,?,'ok',?,0,0)",
                       (T0.isoformat(), eid, sid, seed, value * 100))


# ------------------------------------------------------------------ classification
def test_the_classification_follows_the_configured_thresholds():
    t = O.POLICY["metrics"]["stalled_share"]
    n = O.POLICY["min_events"]
    base = 0.6
    ok = base * (1 - t["success_rel"]) - 0.01
    part = base * (1 - (t["success_rel"] + t["partial_rel"]) / 2)
    flat = base * (1 - t["partial_rel"] / 2)
    bad = base * (1 + t["harm_rel"]) + 0.01
    assert O.classify("stalled_share", base, ok, n, n)[0] == "successful"
    assert O.classify("stalled_share", base, part, n, n)[0] == "partially_successful"
    assert O.classify("stalled_share", base, flat, n, n)[0] == "no_material_effect"
    assert O.classify("stalled_share", base, bad, n, n)[0] == "harmful"
    assert O.classify("stalled_share", base, ok, n - 1, n)[0] == "inconclusive"
    assert O.classify("stalled_share", t["min_baseline"] / 2, 0.0, n, n)[0] == "inconclusive"


def test_each_metric_is_measured_from_station_events():
    ev = [{"station_state": "starved"}, {"station_state": "working"},
          {"station_state": "down"}, {"station_state": "blocked"}]
    assert O.measure(ev, "stalled_share") == (0.75, 4)
    hum = [{"humidity_pct": 70.0, "ambient_humidity_pct": 60.0},
           {"humidity_pct": 64.0, "ambient_humidity_pct": 60.0}, {"humidity_pct": 50.0}]
    assert O.measure(hum, "humidity_excess_pct") == (7.0, 2)
    sig = [{"station_id": 3, "variant": "ev", "torque_nm": 36.0},
           {"station_id": 3, "variant": "ev", "torque_nm": 44.0}]
    value, n = O.measure(sig, "signal_deviation_pct", {"3|torque_nm|ev": 40.0})
    assert value == pytest.approx(10.0) and n == 2


# --------------------------------------------------------- monitoring + learning flow
def test_an_applied_action_is_monitored_evaluated_and_learned(client):
    _events(60, 12, stalled_until=24)         # 24/30 stalled before, 0/30 after
    prop = _proposal()
    _experiment_for(prop["proposal_id"], stalled_base=0.8, stalled_fix=0.5)
    O.record_decision(_ops(), prop, decision="approved", role="manager", note=None,
                      action={"action_type": "increase_feed_buffer"}, model_version="m1")
    applied_ts = (T0 + timedelta(minutes=30)).isoformat()
    oid = O.open_outcome(_ops(), prop, "increase_feed_buffer", run_id=RUN,
                         applied_ts=applied_ts, applied_seq=30)
    row = db.query_one(_ops(), "SELECT * FROM action_outcomes WHERE outcome_id=?", (oid,))
    assert row["status"] == "observing"
    assert row["baseline_value"] == pytest.approx(24 / 30)
    assert row["expected_change"] == pytest.approx(-0.3)          # projected by the experiment
    assert row["experiment_id"] == "EXP-P-T1"

    O.evaluate(_ops(), row, current_seq=60)
    done = db.query_one(_ops(), "SELECT * FROM action_outcomes WHERE outcome_id=?", (oid,))
    assert done["status"] == "successful"
    assert done["actual_change"] == pytest.approx(-0.8)
    assert done["prediction_error"] == pytest.approx(-0.5)      # did 0.5 MORE good than projected
    rec = db.query_one(_ops(), "SELECT * FROM incident_learning_records WHERE proposal_id=?",
                       ("P-T1",))
    assert rec["success_status"] == "successful" and rec["outcome_id"] == oid
    assert rec["experiment_id"] == "EXP-P-T1" and rec["model_version"] == "m1"
    assert json.loads(rec["actual"])["events"] == 30

    body = client.get("/api/learning/outcomes?plant_id=plant_a", headers=MANAGER).json()
    assert body["accuracy"]["evaluated"] == 1 and body["accuracy"]["success_rate"] == 1.0
    assert body["accuracy"]["mean_abs_projection_error"] == pytest.approx(0.5)


def test_a_run_that_ends_early_cancels_its_open_window(client):
    _events(10, 12, stalled_until=10)
    prop = _proposal("P-T2")
    O.record_decision(_ops(), prop, decision="approved", role="manager", note=None,
                      action={"action_type": "increase_feed_buffer"}, model_version=None)
    O.open_outcome(_ops(), prop, "increase_feed_buffer", run_id=RUN,
                   applied_ts=(T0 + timedelta(minutes=10)).isoformat(), applied_seq=10)
    assert O.cancel_open(_ops(), RUN, "run stopped") == 1
    rec = db.query_one(_ops(), "SELECT success_status FROM incident_learning_records "
                               "WHERE proposal_id='P-T2'")
    assert rec["success_status"] == "cancelled"


def test_similar_incidents_match_on_structure_and_rejections_are_not_failures(client):
    for pid, station, det, action, decision, status in (
            ("A", 12, "starvation", "increase_feed_buffer", "approved", "harmful"),
            ("B", 12, "starvation", "increase_feed_buffer", "approved", "no_material_effect"),
            ("C", 14, "starvation", "maintenance_ticket", "approved", "successful"),
            ("D", 12, "starvation", "increase_feed_buffer", "rejected", None),
            ("E", 18, "paint_env", "hvac_setpoint", "approved", "successful")):
        prop = _proposal(pid, station, det)
        O.record_decision(_ops(), prop, decision=decision, role="manager", note="n",
                          action={"action_type": action} if decision == "approved" else None,
                          model_version=None)
        if status:
            O._update_record(_ops(), pid, success_status=status)
    body = client.get("/api/learning/similar?plant_id=plant_a&incident_type=starvation"
                      "&station_id=12", headers=MANAGER).json()
    ids = [r["proposal_id"] for r in body["records"]]
    assert "E" not in ids                                   # nothing in common: excluded
    assert set(ids[:3]) == {"A", "B", "D"}                   # exact station+type first
    assert body["records"][0]["matched_on"] == ["incident_type", "station"]
    assert body["repeatedly_failed"] == ["increase_feed_buffer"]
    assert body["intervention_tally"]["maintenance_ticket"] == {"successful": 1}
    assert body["rejected_recommendations"] == 1
    d = next(r for r in body["records"] if r["proposal_id"] == "D")
    assert d["success_status"] == "not_applied"
