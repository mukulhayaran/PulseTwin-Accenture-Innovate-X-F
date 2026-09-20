"""The triage router: role gates, privilege capping, audit, degradation.

`subprocess.Popen` is monkeypatched throughout, so nothing spawns and no credential is
needed. What is under test is the serve/ half: gating, capping, persistence and what is
and is not returned to a browser.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytest.importorskip("fastapi")

if not (ROOT / "artifacts" / "pulsetwin.db").exists():
    pytest.skip("artifacts not built", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from serve import db  # noqa: E402
from serve.main import app  # noqa: E402
from serve.routers import triage as R  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def no_spawn(monkeypatch):
    spawned: list[list[str]] = []
    monkeypatch.setattr(R.subprocess, "Popen",
                        lambda argv, **kw: spawned.append(argv) or object())
    return spawned


def _post(client, role="manager", **body):
    payload = {"plant_id": "plant_a", "question": "Why did the constraint move?"}
    payload.update(body)
    return client.post("/api/triage/runs", json=payload,
                       headers={"X-PulseTwin-Role": role})


# ------------------------------------------------------------------------ role gates
def test_supervisor_cannot_start_a_run(client) -> None:
    assert _post(client, role="supervisor").status_code == 403


def test_manager_can_start_a_run(client) -> None:
    r = _post(client)
    assert r.status_code == 200
    assert r.json()["status"] == "queued"


def test_reads_require_manager(client) -> None:
    assert client.get("/api/triage/runs",
                      headers={"X-PulseTwin-Role": "supervisor"}).status_code == 403


def test_availability_is_ungated(client) -> None:
    """Like /api/health: the one call every client can always make."""
    r = client.get("/api/triage/availability")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["mode"] in ("agent", "deterministic")


def test_availability_never_leaks_the_credential(client, monkeypatch) -> None:
    monkeypatch.setenv(R.API_KEY_ENV, "xai-supersecret-abcdef123456")
    body = client.get("/api/triage/availability").json()
    blob = json.dumps(body)
    assert "supersecret" not in blob and "abcdef" not in blob
    assert body["mode"] == "agent"


# ------------------------------------------------------------- privilege escalation
def test_a_manager_cannot_act_as_leadership(client, no_spawn) -> None:
    """Without capping, an agent is a read-around for the role gate."""
    r = _post(client, role="manager", acting_role="leadership")
    assert r.json()["acting_role"] == "manager"


def test_leadership_may_act_downwards(client) -> None:
    r = _post(client, role="leadership", acting_role="manager")
    assert r.json()["acting_role"] == "manager"


def test_cap_role_unit() -> None:
    assert R._cap_role("leadership", "manager") == "manager"
    assert R._cap_role("supervisor", "leadership") == "supervisor"
    assert R._cap_role(None, "manager") == "manager"
    assert R._cap_role("nonsense", "manager") == "manager"


# ------------------------------------------------------------------------ validation
def test_unknown_plant_is_404(client) -> None:
    assert _post(client, plant_id="plant_zz").status_code == 404


def test_overlong_question_is_rejected(client) -> None:
    assert _post(client, question="x" * 5000).status_code == 422


def test_control_characters_are_rejected(client) -> None:
    assert _post(client, question="drop\x00tables please").status_code == 422


def test_agent_mode_without_a_credential_is_503(client, monkeypatch) -> None:
    monkeypatch.delenv(R.API_KEY_ENV, raising=False)
    r = _post(client, mode="agent")
    assert r.status_code == 503
    assert R.API_KEY_ENV in r.json()["detail"]


def test_auto_mode_falls_back_to_deterministic(client, monkeypatch) -> None:
    """Graceful degradation, same posture as the SPC fallback: do the honest lesser
    thing and label it, never 500 and never pretend."""
    monkeypatch.delenv(R.API_KEY_ENV, raising=False)
    assert _post(client, mode="auto").json()["mode"] == "deterministic"


# ----------------------------------------------------------------------- persistence
def test_run_row_exists_before_the_worker_starts(client) -> None:
    """Insert-before-spawn: a run is never invisible, even if the worker dies."""
    run_id = _post(client).json()["run_id"]
    row = db.query_one(app.state.ops,
                       "SELECT status, mode FROM triage_runs WHERE run_id = ?", (run_id,))
    assert row is not None and row["status"] == "queued"


def test_unknown_run_id_returns_unknown_not_404(client) -> None:
    body = client.get("/api/triage/runs/deadbeef",
                      headers={"X-PulseTwin-Role": "manager"}).json()
    assert body == {"run_id": "deadbeef", "status": "unknown"}


def test_raw_finding_is_never_served(client) -> None:
    """A redacted claim that reaches a screen has not been redacted."""
    run_id = _post(client).json()["run_id"]
    db.execute(app.state.ops,
               "UPDATE triage_runs SET finding_raw=?, finding=? WHERE run_id=?",
               (json.dumps({"secret": "unredacted"}),
                json.dumps({"summary": "served"}), run_id))
    body = client.get(f"/api/triage/runs/{run_id}",
                      headers={"X-PulseTwin-Role": "manager"}).json()
    assert "finding_raw" not in body
    assert "unredacted" not in json.dumps(body)
    assert body["finding"]["summary"] == "served"


def test_listing_is_scoped_and_clamped(client) -> None:
    _post(client)
    body = client.get("/api/triage/runs?plant_id=plant_a&limit=9999",
                      headers={"X-PulseTwin-Role": "manager"}).json()
    assert body["count"] <= 50
    assert all(r["plant_id"] == "plant_a" for r in body["runs"])


# ----------------------------------------------------------------------------- audit
def test_starting_a_run_writes_an_audit_row(client) -> None:
    run_id = _post(client).json()["run_id"]
    row = db.query_one(
        app.state.ops,
        "SELECT action, user_role FROM audit_log WHERE resource_id = ? ORDER BY id DESC",
        (run_id,),
    )
    assert row is not None
    assert row["action"] == "triage_run_started"


def test_spawn_argv_is_a_list_not_a_shell_string(client, no_spawn) -> None:
    """A list argv never reaches a shell, so the question carries no injection surface."""
    _post(client, question="'; rm -rf / #")
    argv = no_spawn[-1]
    assert isinstance(argv, list)
    assert "-m" in argv and "genai.triage" in argv
    assert "'; rm -rf / #" in argv
