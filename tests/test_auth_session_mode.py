"""ADR-063: PULSETWIN_AUTH_MODE=session -- the demo posture for a shared deployment.

Before it, the running demo was in dev mode, where `-H "X-PulseTwin-Role: system"` passed
every role check. In session mode only a server-issued demo session grants a role; the
header can lower privilege, never raise it; no session is the anonymous floor view.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if not (ROOT / "artifacts" / "pulsetwin.db").exists():
    pytest.skip("artifacts not built", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from serve import session_store  # noqa: E402
from serve.main import app  # noqa: E402

MANAGER_ONLY = "/api/whatif/stations?plant_id=plant_a"          # open read, for contrast
LEADERSHIP_ONLY = "/api/economics/roi?plant_id=plant_a"
MANAGER_GATED = "/api/triage/runs"                              # require_role("manager")


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("PULSETWIN_AUTH_MODE", "session")
    with TestClient(app) as c:
        yield c


def _login(c, user, password):
    r = c.post("/api/auth/login", json={"username": user, "password": password})
    assert r.status_code == 200
    return r.json()["token"]


def test_a_bare_role_header_can_no_longer_claim_a_role(client):
    r = client.get(LEADERSHIP_ONLY, headers={"X-PulseTwin-Role": "system"})
    assert r.status_code == 401 and "sign" in r.json()["detail"]


def test_the_anonymous_floor_view_still_works(client):
    assert client.get("/api/config/plants").status_code == 200
    assert client.get("/api/units?plant_id=plant_a&limit=1",
                      headers={"X-PulseTwin-Role": "supervisor"}).status_code == 200
    assert client.get(MANAGER_GATED).status_code == 403          # anonymous = supervisor


def test_a_session_grants_exactly_its_role_and_a_header_only_lowers_it(client):
    tok = _login(client, "manager", "mgr2026")
    h = {"X-PulseTwin-Token": tok}
    assert client.get(MANAGER_GATED, headers=h).status_code == 200
    assert client.get(LEADERSHIP_ONLY, headers=h).status_code == 403
    # a stale or hostile header above the session is ignored, not honoured
    assert client.get(LEADERSHIP_ONLY,
                      headers={**h, "X-PulseTwin-Role": "system"}).status_code == 403
    # ...and a header below it is a deliberate drop (the /ops "Viewing as" selector)
    assert client.get(MANAGER_GATED,
                      headers={**h, "X-PulseTwin-Role": "supervisor"}).status_code == 403
    client.post("/api/auth/logout", headers=h)
    assert client.get(MANAGER_GATED, headers=h).status_code == 401  # logout ends access


def test_an_agent_run_gets_its_own_short_session_in_its_environment(monkeypatch, tmp_path):
    from serve.routers import triage
    seen = {}

    class FakePopen:
        def __init__(self, argv, **kw):
            seen["argv"], seen["env"] = argv, kw["env"]

    monkeypatch.setenv("PULSETWIN_JOBS_DIR", str(tmp_path))
    monkeypatch.setattr(triage.subprocess, "Popen", FakePopen)
    from serve import db
    ops = db.open_ops_db()
    triage.start_triage_run(ops, plant_id="plant_a", question="q", intent="shift_intel",
                            role="manager", acting_role="manager", mode="deterministic",
                            api_base="http://x")
    token = seen["env"]["PULSETWIN_API_TOKEN"]
    assert token not in " ".join(seen["argv"])                    # never on the command line
    sess = session_store.resolve(token)
    assert sess["role"] == "manager"
    assert sess["expires_at"] - session_store.time.time() <= triage.WORKER_STALE_S + 1
