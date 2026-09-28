"""The watch on an EDGE-produced (MQTT) run (ADR-059): events projected by ingestion/ are
followed by sequence number, silence ticks only on the edge's heartbeat, and a firing
opens an Incident Commander incident exactly as on a backend run (ADR-069). Events are written the way
ingestion/live_projection.py writes them."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if not (ROOT / "artifacts" / "pulsetwin.db").exists():
    pytest.skip("artifacts not built", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from serve import db  # noqa: E402
from serve.main import app  # noqa: E402

T0 = datetime(2026, 1, 5, 8, 0)


@pytest.fixture()
def mqtt_run(monkeypatch):
    monkeypatch.setenv("PULSETWIN_JOBS_ENABLED", "0")  # record incidents, spawn nothing
    monkeypatch.setenv("PULSETWIN_COMMANDER_TICK_S", "0")  # no background investigation
    with TestClient(app):  # the lifespan builds app.state
        manager, ops = app.state.live_simulation, app.state.ops
        run_id = f"MQTT-test-{secrets.token_hex(4)}"
        db.execute(ops, """INSERT INTO simulation_runs
                               (run_id, plant_id, seed, scenario, status, speed, severity,
                                affected_station, affected_lot, incident_start_s, event_count,
                                alerts_count, units_in_progress, max_events, source, producer,
                                started_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (run_id, "plant_a", 0, "normal", "running", 1, 0, None, None, 0, 0, 0, 0,
                    1000000, "real", "mqtt", T0.isoformat()))
        seq = [0]

        def emit(event: dict) -> None:
            seq[0] += 1
            event = {"run_id": run_id, "plant_id": "plant_a", **event}
            db.execute(ops, """INSERT INTO simulation_events
                                   (run_id, seq, ts, event_type, unit_id, station_id, payload)
                               VALUES (?,?,?,?,?,?,?)""",
                       (run_id, seq[0], event["ts"], event["event_type"],
                        event.get("unit_id"), event.get("station_id"), json.dumps(event)))
            db.execute(ops, "UPDATE simulation_runs SET event_count=? WHERE run_id=?",
                       (seq[0], run_id))

        manager.follow_mqtt_once()  # first sight: starts at the current end, fires nothing
        yield manager, ops, run_id, emit
        db.execute(ops, "DELETE FROM incidents WHERE sim_run_id=?", (run_id,))
        db.execute(ops, "DELETE FROM simulation_events WHERE run_id=?", (run_id,))
        db.execute(ops, "DELETE FROM simulation_runs WHERE run_id=?", (run_id,))


def _at(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat()


def _incidents(ops, run_id):
    rows = db.query_all(ops, "SELECT source_ref, station_id, symptom FROM incidents "
                             "WHERE sim_run_id=? AND source='watch'", (run_id,))
    return [{"detector": r["source_ref"], "station_id": r["station_id"],
             "evidence": json.loads(r["symptom"]) if isinstance(r["symptom"], str)
             else r["symptom"]} for r in rows]


def test_a_down_station_on_the_edge_feed_opens_a_breakdown_incident(mqtt_run):
    manager, ops, run_id, emit = mqtt_run
    for sid in range(1, 8):
        emit({"event_type": "station_update", "ts": _at(0), "station_id": sid,
              "unit_id": f"U{sid}", "station_state": "working", "cycle_time_s": 60.0})
    # The PLC state tag, no unit: ingestion now projects it instead of dropping it.
    emit({"event_type": "station_state", "ts": _at(1), "station_id": 4,
          "station_state": "down"})
    assert manager.follow_mqtt_once() == 0
    # The line is quiet; only the edge's heartbeat moves the clock.
    emit({"event_type": "heartbeat", "ts": _at(20)})
    assert manager.follow_mqtt_once() == 1
    [row] = _incidents(ops, run_id)
    evidence = row["evidence"]
    assert (row["detector"], row["station_id"]) == ("starvation", 4)
    assert evidence["signal"] == "silence" and evidence["down_stations"] == [4]


def test_no_heartbeat_means_no_silence(mqtt_run):
    """A network outage stops events AND heartbeats: that must not read as a stoppage."""
    manager, ops, run_id, emit = mqtt_run
    for sid in range(1, 8):
        emit({"event_type": "station_update", "ts": _at(0), "station_id": sid,
              "unit_id": f"U{sid}", "station_state": "working", "cycle_time_s": 60.0})
    assert manager.follow_mqtt_once() == 0
    assert manager.follow_mqtt_once() == 0
    assert _incidents(ops, run_id) == []


def test_merged_telemetry_reaches_the_tool_wear_detector(mqtt_run):
    """The subscriber folds a visit's telemetry into its state message, so a projected
    station event carries torque -- without that the watch never saw a signal."""
    manager, ops, run_id, emit = mqtt_run
    rules = manager._watch_rules["plant_a"]
    key, base = next((k, v) for k, v in rules["tool_wear"]["baselines"].items()
                     if k.endswith("|torque_nm|suv"))
    sid = int(key.split("|")[0])
    for i in range(rules["window_events"]):
        emit({"event_type": "station_update", "ts": _at(i), "station_id": sid,
              "unit_id": f"W{i}", "variant": "suv", "station_state": "working",
              "cycle_time_s": 60.0, "torque_nm": base * 0.85})
    assert manager.follow_mqtt_once() == 1
    [row] = _incidents(ops, run_id)
    assert (row["detector"], row["station_id"]) == ("tool_wear", sid)
