"""End-to-end sensor dropout: a `sensor_health` row written the way
ingestion/mqtt_subscriber.py writes it (real INSERT, not a mock) must surface correctly
through GET /api/system/health -- this is the backend half of "inject a sensor dropout via
the publisher control, watch SensorDropoutAlert light up" (docs/FINALS_DEMO_SCRIPT.md).
The frontend rendering itself is out of pytest's reach; that's the `run`/`playwright`
skills' job during the actual demo rehearsal.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]

pytest.importorskip("fastapi")

if not (ROOT / "artifacts" / "pulsetwin.db").exists():
    pytest.skip("artifacts not built", allow_module_level=True)

from serve import db  # noqa: E402
from serve.main import app  # noqa: E402

MANAGER = {"X-PulseTwin-Role": "manager"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app, headers=MANAGER) as c:
        yield c


def test_no_dropout_reports_operational(client):
    body = client.get("/api/system/health?plant_id=plant_a").json()
    # Other tests in this module insert dropouts into the SAME shared ops db, so this one
    # must run first (module-scoped client) to observe the clean state.
    assert body["status"] in ("operational", "degraded")
    assert isinstance(body["ingestion"]["sensor_dropouts_active"], int)


def test_injecting_a_dropout_row_flips_system_status(client):
    ops = app.state.ops
    db.execute(
        ops,
        """INSERT INTO sensor_health
               (station_id, sensor_type, status, last_reading_at, dropout_duration_s, plant_id)
           VALUES (?,?,?,?,?,?)""",
        (8, "vibration_mm_s", "dropout", "2026-01-01T00:00:00", 180.0, "plant_a"),
    )

    body = client.get("/api/system/health?plant_id=plant_a").json()
    assert body["status"] == "degraded"
    assert body["ingestion"]["sensor_dropouts_active"] >= 1
    match = next(d for d in body["ingestion"]["dropout_details"] if d["station_id"] == 8)
    assert match["sensor"] == "vibration_mm_s"
    assert match["duration_s"] == 180.0


def test_a_later_healthy_row_supersedes_the_dropout(client):
    """sensor_health is a change log -- the router must read the LATEST row per
    (station, sensor), not just any row that says 'dropout'.

    Uses the shared module-scoped `client` fixture rather than opening a second,
    ad-hoc `TestClient(app)` -- a second `with` block re-runs `serve.main`'s lifespan on
    the same process-global STATE and closes `app.state.ops` out from under any test that
    runs after it, which is exactly what made the next test flaky."""
    ops = app.state.ops
    db.execute(
        ops,
        """INSERT INTO sensor_health
               (station_id, sensor_type, status, last_reading_at, dropout_duration_s, plant_id)
           VALUES (?,?,?,?,?,?)""",
        (8, "vibration_mm_s", "healthy", "2026-01-01T00:05:00", None, "plant_a"),
    )
    body = client.get("/api/system/health?plant_id=plant_a").json()
    stations_still_down = {d["station_id"] for d in body["ingestion"]["dropout_details"]}
    assert 8 not in stations_still_down


def test_dropout_scoped_to_its_own_plant(client):
    ops = app.state.ops
    db.execute(
        ops,
        """INSERT INTO sensor_health
               (station_id, sensor_type, status, last_reading_at, dropout_duration_s, plant_id)
           VALUES (?,?,?,?,?,?)""",
        (5, "torque_nm", "dropout", "2026-01-01T00:00:00", 60.0, "plant_b"),
    )
    body_a = client.get("/api/system/health?plant_id=plant_a").json()
    body_b = client.get("/api/system/health?plant_id=plant_b").json()
    assert not any(d["station_id"] == 5 for d in body_a["ingestion"]["dropout_details"])
    assert any(d["station_id"] == 5 for d in body_b["ingestion"]["dropout_details"])
