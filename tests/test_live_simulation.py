"""Live-synthetic run contract and temporal-correctness regressions."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
pytest.importorskip("fastapi")
if not (ROOT / "artifacts" / "pulsetwin.db").exists():
    pytest.skip("artifacts not built", allow_module_level=True)

from serve.main import app  # noqa: E402
from serve import db  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app, headers={"X-PulseTwin-Role": "supervisor"}) as value:
        yield value


def _start(client: TestClient, seed: int = 42, scenario: str = "normal", max_events: int = 12):
    response = client.post("/api/simulations", json={
        "plant_id": "plant_a",
        "seed": seed,
        "scenario": scenario,
        "speed": 500,
        "severity": 0.8,
        "max_events": max_events,
    })
    assert response.status_code == 200, response.text
    return response.json()


def _wait_terminal(client: TestClient, run_id: str, timeout_s: float = 5) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        run = client.get(f"/api/simulations/{run_id}").json()
        if run["status"] in ("completed", "stopped", "failed"):
            return run
        time.sleep(0.03)
    raise AssertionError(f"run {run_id} did not finish")


def _events(run_id: str) -> list[dict]:
    return app.state.live_simulation.events_after(run_id, 0, 1000)


def test_fixed_seed_is_reproducible_and_source_is_explicit(client):
    first = _start(client, seed=42)
    assert _wait_terminal(client, first["run_id"])["status"] == "completed"
    second = _start(client, seed=42)
    assert _wait_terminal(client, second["run_id"])["status"] == "completed"

    def comparable(events):
        return [
            (e["unit_id"], e["station_id"], e["cycle_time_s"], e["station_state"])
            for e in events if e["event_type"] in ("station_update", "alert")
        ]

    assert comparable(_events(first["run_id"])) == comparable(_events(second["run_id"]))
    assert all(e["source"] == "live_synthetic" for e in _events(first["run_id"]))


def test_different_seed_changes_synthetic_values(client):
    first = _start(client, seed=101)
    _wait_terminal(client, first["run_id"])
    second = _start(client, seed=202)
    _wait_terminal(client, second["run_id"])
    first_cycles = [e["cycle_time_s"] for e in _events(first["run_id"])
                    if "cycle_time_s" in e]
    second_cycles = [e["cycle_time_s"] for e in _events(second["run_id"])
                     if "cycle_time_s" in e]
    assert first_cycles != second_cycles


def test_pause_resume_stop_and_shared_active_state(client):
    run = _start(client, seed=303, max_events=1000)
    run_id = run["run_id"]
    paused = client.post(f"/api/simulations/{run_id}/pause").json()
    assert paused["status"] == "paused"
    active = client.get("/api/simulations/active?plant_id=plant_a").json()
    assert active["run_id"] == run_id
    resumed = client.post(f"/api/simulations/{run_id}/resume").json()
    assert resumed["status"] == "running"
    stopped = client.post(f"/api/simulations/{run_id}/stop").json()
    assert stopped["status"] == "stopped"


def test_temporal_unit_detail_hides_future_journey_and_outcome(client):
    run = _start(client, seed=404, max_events=15)
    _wait_terminal(client, run["run_id"])
    event = next(e for e in _events(run["run_id"])
                 if e["event_type"] in ("station_update", "alert"))
    response = client.get(
        f"/api/units/{event['unit_id']}?plant_id=plant_a"
        f"&run_id={run['run_id']}&at_ts={event['ts']}"
    )
    assert response.status_code == 200, response.text
    unit = response.json()
    assert all(step["entry_ts"] <= event["ts"] for step in unit["journey"])
    assert all(step["exit_ts"] is None or step["exit_ts"] <= event["ts"]
               for step in unit["journey"])
    assert unit["actual_defect"] is None
    assert unit["current_station"] is not None


def test_unknown_scenario_and_second_active_run_are_rejected(client):
    bad = client.post("/api/simulations", json={
        "plant_id": "plant_a", "seed": 1, "scenario": "magic", "speed": 10,
    })
    assert bad.status_code == 422

    run = _start(client, seed=505, max_events=1000)
    conflict = client.post("/api/simulations", json={
        "plant_id": "plant_a", "seed": 506, "scenario": "normal", "speed": 10,
    })
    assert conflict.status_code == 409
    client.post(f"/api/simulations/{run['run_id']}/stop")


def test_incident_start_time_delays_scenario_effect(client):
    response = client.post("/api/simulations", json={
        "plant_id": "plant_a", "seed": 606, "scenario": "station_breakdown",
        "speed": 500, "severity": 1.0, "incident_start_s": 86400,
        "max_events": 10,
    })
    assert response.status_code == 200, response.text
    run = response.json()
    assert run["incident_start_s"] == 86400
    assert _wait_terminal(client, run["run_id"])["status"] == "completed"
    events = [event for event in _events(run["run_id"])
              if event["event_type"] in ("station_update", "alert")]
    assert events
    assert all(event["scenario"] == "normal" for event in events)
    assert all(event["incident_active"] is False for event in events)


def test_alert_lifecycle_persists_through_closed_and_updates_metrics(client):
    run = _start(client, seed=707, max_events=10)
    _wait_terminal(client, run["run_id"])
    unit = db.query_one(
        app.state.main_con,
        "SELECT unit_id,calibrated_risk,prediction_ts FROM units "
        "WHERE plant_id='plant_a' AND alert_fired=1 LIMIT 1",
    )
    alert_id = f"{run['run_id']}:{unit['unit_id']}"
    db.execute(
        app.state.ops,
        """INSERT INTO active_alerts
               (alert_id,run_id,plant_id,unit_id,station_id,risk,status,opened_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (alert_id, run["run_id"], "plant_a", unit["unit_id"], 1,
         unit["calibrated_risk"], "OPEN", unit["prediction_ts"]),
    )

    acknowledged = client.post(
        f"/api/alerts/{unit['unit_id']}/acknowledge",
        json={"plant_id": "plant_a", "run_id": run["run_id"]},
    )
    assert acknowledged.status_code == 200, acknowledged.text
    restored = client.get(
        f"/api/alerts/{unit['unit_id']}/status?plant_id=plant_a&run_id={run['run_id']}"
    ).json()
    assert restored["status"] == "ACKNOWLEDGED"
    assert restored["acknowledged_at"]

    db.execute(
        app.state.ops,
        """UPDATE active_alerts SET status='VALIDATED',outcome=1,
                  outcome_at=?,validated_at=? WHERE alert_id=?""",
        (unit["prediction_ts"], unit["prediction_ts"], alert_id),
    )
    closed = client.post(
        f"/api/alerts/{unit['unit_id']}/close",
        json={"plant_id": "plant_a", "run_id": run["run_id"]},
    )
    assert closed.status_code == 200, closed.text
    final = client.get(
        f"/api/alerts/{unit['unit_id']}/status?plant_id=plant_a&run_id={run['run_id']}"
    ).json()
    assert final["status"] == "CLOSED"
    assert final["closed_at"]

    metrics = client.get(f"/api/simulations/{run['run_id']}/metrics").json()
    assert metrics["active_alerts"] == 0
    assert metrics["correct_alerts"] == 1
    assert metrics["false_alerts"] == 0
    assert metrics["alert_correctness_rate"] == 1.0


def test_an_orphaned_mqtt_run_can_be_stopped_and_stops_blocking(client):
    """An MQTT run whose publisher died never gets a terminal lifecycle message, so it sat
    at `running` forever and blocked every new run on the plant -- `stop` answered 409 'not
    owned by this backend process'. Found on the prod stack; the backend may now close it."""
    from serve import db
    from serve.main import app

    run_id = "MQTT-plant_a-orphan-test"
    db.execute(app.state.ops,
               """INSERT INTO simulation_runs (run_id, plant_id, seed, scenario, status, speed,
                      severity, max_events, source, producer, started_at)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
               (run_id, "plant_a", 0, "normal", "running", 1.0, 0.0, 1000000,
                "live_synthetic", "mqtt", "2026-01-01T00:00:00+00:00"))
    try:
        blocked = client.post("/api/simulations", json={
            "plant_id": "plant_a", "seed": 707, "scenario": "normal", "speed": 10})
        assert blocked.status_code == 409
        stopped = client.post(f"/api/simulations/{run_id}/stop")
        assert stopped.status_code == 200 and stopped.json()["status"] == "stopped"
        run = _start(client, seed=708, max_events=12)
        assert run["status"] == "running"
        _wait_terminal(client, run["run_id"])
    finally:
        db.execute(app.state.ops, "DELETE FROM simulation_runs WHERE run_id=?", (run_id,))
