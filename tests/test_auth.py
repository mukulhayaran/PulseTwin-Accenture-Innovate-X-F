"""RBAC tests (finals brief Phase F4). serve/middleware/auth.py's role hierarchy:
supervisor < manager < leadership < system, header `X-PulseTwin-Role`, default supervisor.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]

pytest.importorskip("fastapi")

if not (ROOT / "artifacts" / "pulsetwin.db").exists():
    pytest.skip("artifacts not built", allow_module_level=True)

from serve.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    # Deliberately no default role header here (unlike test_api.py's fixture) -- this
    # module tests the gating itself, so each test states its own role explicitly.
    with TestClient(app) as c:
        yield c


def h(role: str | None) -> dict:
    return {} if role is None else {"X-PulseTwin-Role": role}


# --------------------------------------------------------------------------- defaults


def test_missing_role_header_defaults_to_supervisor(client):
    """No header must grant the LEAST access, not the most."""
    r = client.get("/api/system/health")  # manager-gated
    assert r.status_code == 403


def test_unknown_role_is_rejected(client):
    r = client.get("/api/system/health", headers=h("intern"))
    assert r.status_code == 400


# ----------------------------------------------------------------------- hierarchy


@pytest.mark.parametrize("role,expected", [
    ("supervisor", 403), ("manager", 200), ("leadership", 200), ("system", 200),
])
def test_manager_gated_endpoint(client, role, expected):
    assert client.get("/api/system/health", headers=h(role)).status_code == expected


@pytest.mark.parametrize("role,expected", [
    ("supervisor", 403), ("manager", 403), ("leadership", 200), ("system", 200),
])
def test_leadership_gated_endpoint(client, role, expected):
    assert client.get("/api/economics/roi?plant_id=plant_a", headers=h(role)).status_code == expected


@pytest.mark.parametrize("role,expected", [
    ("supervisor", 403), ("manager", 403), ("leadership", 403),
])
def test_system_gated_endpoint_rejects_everything_below_system(client, role, expected):
    r = client.post("/api/admin/trigger-retrain", json={"plant_id": "plant_a"}, headers=h(role))
    assert r.status_code == expected


def test_system_role_can_trigger_retrain(client):
    """Mocks the subprocess -- this test is about the ROLE GATE, not the training run
    itself (mlops/retrainer.py is exercised directly by tests/test_retraining.py)."""
    with patch("serve.routers.admin.subprocess.Popen") as mock_popen:
        r = client.post("/api/admin/trigger-retrain", json={"plant_id": "plant_a"},
                         headers=h("system"))
        assert r.status_code == 200
        assert r.json()["status"] == "queued"
        assert mock_popen.called


def test_supervisor_cannot_promote_a_model(client):
    r = client.post("/api/admin/promote-model", json={"model_id": "does-not-exist"},
                     headers=h("supervisor"))
    assert r.status_code == 403


def test_system_role_promote_reaches_business_logic(client):
    """system role passes the gate; a bogus model_id then 404s from the handler itself,
    proving the request got past auth and into real logic, not stopped by the gate."""
    r = client.post("/api/admin/promote-model", json={"model_id": "does-not-exist"},
                     headers=h("system"))
    assert r.status_code == 404


# -------------------------------------------------------------------- supervisor access


def test_supervisor_keeps_access_to_its_own_endpoints(client):
    """The hierarchy must not accidentally lock supervisors out of their own view."""
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/units?plant_id=plant_a").status_code == 200


def test_manager_can_run_whatif(client):
    sid = client.get("/api/whatif/stations?plant_id=plant_a",
                      headers=h("leadership")).json()[0]["station_id"]
    r = client.post("/api/whatif", json={"plant_id": "plant_a", "station_id": sid},
                     headers=h("manager"))
    assert r.status_code == 200


def test_supervisor_cannot_run_whatif(client):
    r = client.post("/api/whatif", json={"plant_id": "plant_a", "station_id": 1},
                     headers=h("supervisor"))
    assert r.status_code == 403
