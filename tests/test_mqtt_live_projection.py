"""The MQTT projection is tested without a broker or PostgreSQL server."""

from __future__ import annotations

import json
import sqlite3

from ingestion.live_projection import LiveProjection


class PgLikeSqlite:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")

    def execute(self, sql, params=()):
        return self.db.execute(sql.replace("%s", "?"), params)


def _database() -> PgLikeSqlite:
    con = PgLikeSqlite()
    con.db.executescript("""
        CREATE TABLE station_config (plant_id TEXT, station_id INTEGER, is_inspection INTEGER);
        INSERT INTO station_config VALUES ('plant_a', 30, 1);
        CREATE TABLE plant_meta (plant_id TEXT, alert_budget INTEGER);
        INSERT INTO plant_meta VALUES ('plant_a', 6);
        CREATE TABLE units (plant_id TEXT, unit_id TEXT, actual_defect INTEGER);
        INSERT INTO units VALUES ('plant_a', 'U001', 1);
        CREATE TABLE simulation_runs (
            run_id TEXT PRIMARY KEY, plant_id TEXT, seed INTEGER, scenario TEXT, status TEXT,
            speed REAL, severity REAL, event_count INTEGER, alerts_count INTEGER,
            units_in_progress INTEGER, max_events INTEGER, source TEXT, producer TEXT,
            started_at TEXT, ended_at TEXT, simulation_time TEXT);
        CREATE TABLE simulation_events (
            run_id TEXT, seq INTEGER, ts TEXT, event_type TEXT, unit_id TEXT,
            station_id INTEGER, payload TEXT, PRIMARY KEY(run_id,seq));
        CREATE TABLE unit_current_state (
            run_id TEXT, plant_id TEXT, unit_id TEXT, current_station INTEGER, status TEXT,
            risk REAL, variant TEXT, lot_id TEXT, updated_at TEXT, inspected INTEGER DEFAULT 0,
            actual_defect INTEGER, PRIMARY KEY(run_id,unit_id));
        CREATE TABLE station_current_state (
            run_id TEXT, plant_id TEXT, station_id INTEGER, state TEXT, unit_id TEXT,
            cycle_time_s REAL, risk REAL, updated_at TEXT, PRIMARY KEY(run_id,station_id));
        CREATE TABLE active_alerts (
            alert_id TEXT PRIMARY KEY, run_id TEXT, plant_id TEXT, unit_id TEXT,
            station_id INTEGER, risk REAL, status TEXT, opened_at TEXT,
            acknowledged_at TEXT, actioned_at TEXT, validated_at TEXT, closed_at TEXT,
            outcome INTEGER, outcome_at TEXT);
        CREATE TABLE containment_decisions (
            id INTEGER PRIMARY KEY, run_id TEXT, target_unit_id TEXT, selected_action TEXT,
            status TEXT, actual_outcome INTEGER, outcome_at TEXT, validation_result TEXT);
    """)
    return con


def test_mqtt_state_is_visible_as_shared_live_event_and_alert():
    con = _database()
    projection = LiveProjection(con)
    projection.lifecycle("plant_a", {
        "run_id": "MQTT-DEMO", "status": "running", "source": "live_synthetic",
        "seed": 42, "speed": 10, "started_at": 1_700_000_000,
    })
    projection.station_state("plant_a", 8, {
        "run_id": "MQTT-DEMO", "source": "live_synthetic",
        "ts": "2026-01-01T00:00:00+00:00", "unit_id": "U001",
        "station_state": "working", "cycle_time_s": 61.2,
        "calibrated_risk": 0.81, "alert": 1, "variant": "ev", "lot_id": "L1",
    })

    run = con.execute("SELECT producer,event_count,alerts_count FROM simulation_runs").fetchone()
    assert tuple(run) == ("mqtt", 1, 1)
    raw = con.execute("SELECT event_type,payload FROM simulation_events").fetchone()
    assert raw[0] == "alert"
    assert json.loads(raw[1])["run_id"] == "MQTT-DEMO"
    assert json.loads(raw[1])["alerts_used"] == 1
    assert con.execute("SELECT status FROM active_alerts").fetchone()[0] == "OPEN"


def test_inspection_closes_alert_and_emits_outcome():
    con = _database()
    projection = LiveProjection(con)
    base = {"run_id": "MQTT-DEMO", "source": "live_synthetic", "unit_id": "U001"}
    projection.station_state("plant_a", 8, {
        **base, "ts": "2026-01-01T00:00:00+00:00", "station_state": "working",
        "calibrated_risk": 0.8, "alert": 1,
    })
    projection.station_state("plant_a", 30, {
        **base, "ts": "2026-01-01T00:10:00+00:00", "station_state": "working",
        "calibrated_risk": 0.8, "alert": 0,
    })

    assert con.execute("SELECT status,outcome FROM active_alerts").fetchone() == ("VALIDATED", 1)
    event_types = [r[0] for r in con.execute("SELECT event_type FROM simulation_events ORDER BY seq")]
    assert event_types == ["alert", "station_update", "inspection_outcome"]
