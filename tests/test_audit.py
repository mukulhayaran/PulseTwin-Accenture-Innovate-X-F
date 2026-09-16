"""Audit trail tests (finals brief Phase F4). serve/middleware/audit.py writes to
`audit_log`; serve/routers/audit.py reads it back, leadership+ only.
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

LEADERSHIP = {"X-PulseTwin-Role": "leadership"}
SUPERVISOR = {"X-PulseTwin-Role": "supervisor"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app, headers=LEADERSHIP) as c:
        yield c


def test_audit_log_requires_leadership(client):
    assert client.get("/api/audit/log", headers=SUPERVISOR).status_code == 403


def test_feedback_creates_an_audit_entry(client):
    top = client.get("/api/units?plant_id=plant_a&alerts_only=true&limit=1").json()[0]
    r = client.post("/api/feedback",
                     json={"plant_id": "plant_a", "unit_id": top["unit_id"], "verdict": "useful"},
                     headers=SUPERVISOR)
    assert r.status_code == 200

    body = client.get("/api/audit/log?action=feedback_submitted&limit=20").json()
    assert body["count"] > 0
    entry = next(e for e in body["entries"] if e["resource_id"] == top["unit_id"])
    assert entry["user_role"] == "supervisor"
    assert entry["payload"]["verdict"] == "useful"
    # Operator privacy / sourced-economics contracts extend to the audit trail: no raw
    # sensor reading or operator id belongs in a feedback payload.
    assert "operator" not in str(entry["payload"]).lower()


def test_whatif_creates_an_audit_entry(client):
    sid = client.get("/api/whatif/stations?plant_id=plant_a").json()[0]["station_id"]
    r = client.post("/api/whatif", json={"plant_id": "plant_a", "station_id": sid},
                     headers={"X-PulseTwin-Role": "manager"})
    assert r.status_code == 200

    body = client.get("/api/audit/log?action=whatif_simulation_run&limit=20").json()
    assert body["count"] > 0
    assert body["entries"][0]["user_role"] == "manager"


def test_audit_log_is_filterable_by_role(client):
    body = client.get("/api/audit/log?role=manager&limit=50").json()
    assert all(e["user_role"] == "manager" for e in body["entries"])


def test_audit_log_ordered_most_recent_first(client):
    body = client.get("/api/audit/log?limit=50").json()
    timestamps = [e["timestamp"] for e in body["entries"]]
    assert timestamps == sorted(timestamps, reverse=True)


def test_supervisor_action_never_leaks_into_a_higher_role_filter(client):
    body = client.get("/api/audit/log?role=leadership&limit=50").json()
    assert all(e["user_role"] == "leadership" for e in body["entries"])
