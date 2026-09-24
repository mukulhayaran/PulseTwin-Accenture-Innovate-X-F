"""Session tokens in front of the demo role header.

serve/routers/auth_demo.py mints an opaque token on a correct login; serve/session_store.py
holds it; serve/middleware/auth.py resolves it and lets it OUTRANK the self-asserted
`X-PulseTwin-Role` header. The point of these tests is the seam between those three:

  * a token decides the role, a header alone cannot raise it,
  * a token may still be voluntarily dropped to a LOWER role (/ops "Viewing as"),
  * an expired or revoked token fails closed rather than falling back to the header, and
  * presenting no token behaves exactly as it did before sessions existed -- which is
    what tests/test_auth.py pins, and what keeps docs/SECURITY.md's honest description of
    the header seam true.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]

pytest.importorskip("fastapi")

if not (ROOT / "artifacts" / "pulsetwin.db").exists():
    pytest.skip("artifacts not built", allow_module_level=True)

from serve import session_store  # noqa: E402
from serve.main import app  # noqa: E402

# Endpoints at three different levels of the hierarchy, used to probe what a session buys.
MANAGER_ENDPOINT = "/api/system/health"
LEADERSHIP_ENDPOINT = "/api/economics/roi?plant_id=plant_a"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_sessions():
    session_store.clear()
    yield
    session_store.clear()


def sign_in(client: TestClient, username: str, password: str) -> str:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


# ------------------------------------------------------------------------------- login


def test_login_returns_a_token_and_the_role():
    with TestClient(app) as c:
        body = c.post(
            "/api/auth/login", json={"username": "manager", "password": "mgr2026"}
        ).json()
    assert body["ok"] is True
    assert body["role"] == "manager"
    assert len(body["token"]) >= 32
    assert body["expires_at"] > time.time()


def test_wrong_password_mints_nothing(client):
    r = client.post("/api/auth/login", json={"username": "manager", "password": "nope"})
    assert r.status_code == 401
    assert session_store.live_count() == 0


def test_two_logins_are_two_distinct_sessions(client):
    a = sign_in(client, "manager", "mgr2026")
    b = sign_in(client, "manager", "mgr2026")
    assert a != b
    assert session_store.live_count() == 2


# ---------------------------------------------------------------- the token wins


def test_token_grants_its_own_role_without_any_role_header(client):
    """The frontend relies on this: the session alone is enough to be a manager."""
    token = sign_in(client, "manager", "mgr2026")
    r = client.get(MANAGER_ENDPOINT, headers={"X-PulseTwin-Token": token})
    assert r.status_code == 200


def test_a_declared_role_cannot_climb_above_the_session(client):
    """A supervisor session declaring `leadership` stays a supervisor.

    This is the property the whole feature rests on -- without it the token would be
    decoration and the header would still be the real credential.
    """
    token = sign_in(client, "supervisor", "sup2026")
    r = client.get(
        LEADERSHIP_ENDPOINT,
        headers={"X-PulseTwin-Token": token, "X-PulseTwin-Role": "leadership"},
    )
    assert r.status_code == 403


def test_a_declared_role_may_drop_below_the_session(client):
    """/ops's "Viewing as" selector: a system session acting as supervisor gets a 403 on a
    manager endpoint, which is exactly the demo -- privilege dropped, gate visibly closes."""
    token = sign_in(client, "system", "sys2026")
    assert client.get(
        MANAGER_ENDPOINT, headers={"X-PulseTwin-Token": token}
    ).status_code == 200
    assert client.get(
        MANAGER_ENDPOINT,
        headers={"X-PulseTwin-Token": token, "X-PulseTwin-Role": "supervisor"},
    ).status_code == 403


def test_session_names_the_user_and_the_client_cannot_relabel_it(client):
    """Audit rows must say who acted, not who the caller claimed to be."""
    token = sign_in(client, "leadership", "lead2026")
    r = client.get(
        "/api/audit/log?limit=1",
        headers={"X-PulseTwin-Token": token, "X-PulseTwin-User": "somebody-else"},
    )
    assert r.status_code == 200


# ------------------------------------------------------------------- failing closed


def test_an_unknown_token_is_401_not_a_silent_header_fallback(client):
    r = client.get(
        MANAGER_ENDPOINT,
        headers={"X-PulseTwin-Token": "not-a-real-token", "X-PulseTwin-Role": "manager"},
    )
    assert r.status_code == 401


def test_an_expired_token_is_401(client, monkeypatch):
    token = sign_in(client, "manager", "mgr2026")
    # Reach into the store rather than sleeping eight hours.
    session_store._SESSIONS[token]["expires_at"] = time.time() - 1
    assert client.get(
        MANAGER_ENDPOINT, headers={"X-PulseTwin-Token": token}
    ).status_code == 401


def test_logout_revokes_the_token_for_real(client):
    token = sign_in(client, "manager", "mgr2026")
    assert client.get(MANAGER_ENDPOINT, headers={"X-PulseTwin-Token": token}).status_code == 200

    out = client.post("/api/auth/logout", headers={"X-PulseTwin-Token": token})
    assert out.status_code == 200 and out.json()["revoked"] is True

    assert client.get(MANAGER_ENDPOINT, headers={"X-PulseTwin-Token": token}).status_code == 401


def test_logout_is_idempotent(client):
    """Signing out twice must not 401 -- the user's intent is already satisfied."""
    token = sign_in(client, "manager", "mgr2026")
    client.post("/api/auth/logout", headers={"X-PulseTwin-Token": token})
    again = client.post("/api/auth/logout", headers={"X-PulseTwin-Token": token})
    assert again.status_code == 200 and again.json()["revoked"] is False


# --------------------------------------------------------------------- /api/auth/session


def test_session_endpoint_confirms_a_live_token(client):
    token = sign_in(client, "leadership", "lead2026")
    r = client.get("/api/auth/session", headers={"X-PulseTwin-Token": token})
    assert r.status_code == 200
    assert r.json()["role"] == "leadership"


def test_session_endpoint_rejects_a_missing_or_dead_token(client):
    assert client.get("/api/auth/session").status_code == 401
    assert client.get(
        "/api/auth/session", headers={"X-PulseTwin-Token": "nope"}
    ).status_code == 401


# ------------------------------------------------------------- backwards compatibility


def test_no_token_behaves_exactly_as_before(client):
    """The header seam docs/SECURITY.md documents is unchanged, deliberately."""
    assert client.get(MANAGER_ENDPOINT).status_code == 403
    assert client.get(
        MANAGER_ENDPOINT, headers={"X-PulseTwin-Role": "manager"}
    ).status_code == 200
    assert client.get(
        MANAGER_ENDPOINT, headers={"X-PulseTwin-Role": "intern"}
    ).status_code == 400


# ------------------------------------------------------------------------- the store


def test_store_evicts_rather_than_refusing_when_full():
    session_store.clear()
    first = session_store.issue("manager", "demo-manager")["token"]
    for _ in range(session_store.MAX_SESSIONS):
        session_store.issue("manager", "demo-manager")
    assert session_store.live_count() <= session_store.MAX_SESSIONS
    # The oldest went, not the newest -- a login storm must not lock out the person who
    # just signed in.
    assert session_store.resolve(first) is None
    session_store.clear()
