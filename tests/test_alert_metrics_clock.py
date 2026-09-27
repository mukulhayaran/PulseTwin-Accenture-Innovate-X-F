"""ADR-063: human response times are wall-clock durations.

`opened_at` is the simulation clock; acknowledgement is wall clock. The run metrics
subtracted one from the other and reported a mean time-to-acknowledge of 241 days.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if not (ROOT / "artifacts" / "pulsetwin.db").exists():
    pytest.skip("artifacts not built", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from serve import db  # noqa: E402
from serve.main import app  # noqa: E402


def test_time_to_acknowledge_is_measured_on_the_wall_clock():
    with TestClient(app) as c:
        ops = app.state.ops
        rid = f"SIM-clock-{secrets.token_hex(3)}"
        now = datetime.now(timezone.utc)
        db.execute(ops, """INSERT INTO simulation_runs (run_id, plant_id, seed, scenario, status,
                               speed, severity, event_count, alerts_count, units_in_progress,
                               max_events, source, producer, started_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (rid, "plant_a", 42, "normal", "stopped", 60, 0, 1, 1, 0, 100,
                    "live_synthetic", "backend", now.isoformat()))
        sim_ts = "2026-01-28T08:32:52+00:00"                  # the simulated alert time
        db.execute(ops, """INSERT INTO active_alerts (alert_id, run_id, plant_id, unit_id,
                               station_id, risk, status, opened_at, opened_wall_at,
                               acknowledged_at, actioned_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                   (f"{rid}:U1", rid, "plant_a", "U1", 21, 0.41, "ACTIONED", sim_ts,
                    (now - timedelta(seconds=10)).isoformat(), now.isoformat(),
                    (now + timedelta(seconds=5)).isoformat()))
        try:
            m = c.get(f"/api/simulations/{rid}/metrics",
                      headers={"X-PulseTwin-Role": "manager"}).json()
        finally:
            db.execute(ops, "DELETE FROM active_alerts WHERE run_id=?", (rid,))
            db.execute(ops, "DELETE FROM simulation_runs WHERE run_id=?", (rid,))
    assert m["mean_time_to_acknowledge_s"] == pytest.approx(10.0, abs=1.0)
    assert m["mean_time_to_contain_s"] == pytest.approx(15.0, abs=1.0)
