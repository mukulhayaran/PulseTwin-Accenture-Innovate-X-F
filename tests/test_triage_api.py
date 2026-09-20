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


# ------------------------------------------------------------------ serve/genai mirrors
#
# serve/routers/triage.py re-declares intent keys and the phase trail by hand, because
# the import wall forbids importing genai/. Hand-maintained mirrors drift; these make a
# drift a test failure instead of a blank panel or a silent 422.


def test_intent_keys_mirror_the_genai_registry() -> None:
    from genai import intents

    assert set(R.INTENT_KEYS) == set(intents.INTENTS)
    for key, label in R.INTENT_LABELS.items():
        assert label == intents.INTENTS[key].label


def test_the_intent_pattern_accepts_exactly_those_keys() -> None:
    import re

    pattern = re.compile(R.INTENT_PATTERN)
    for key in R.INTENT_KEYS:
        assert pattern.match(key), f"{key} rejected by TriageRequest's pattern"
    assert not pattern.match("promote_model")


def test_phase_order_mirrors_the_tool_registry() -> None:
    from genai import tools as T

    assert tuple(R.PHASE_ORDER) == T.PHASE_ORDER


def test_whatif_requires_a_station(client) -> None:
    r = client.post("/api/triage/runs",
                    json={"plant_id": "plant_a", "intent": "whatif"},
                    headers={"X-PulseTwin-Role": "manager"})
    assert r.status_code == 422


def test_a_named_action_needs_no_question(client) -> None:
    r = client.post("/api/triage/runs",
                    json={"plant_id": "plant_a", "intent": "handover"},
                    headers={"X-PulseTwin-Role": "manager"})
    assert r.status_code == 200, r.text
    assert r.json()["intent"] == "handover"


def test_ask_still_requires_a_question(client) -> None:
    r = client.post("/api/triage/runs", json={"plant_id": "plant_a", "intent": "ask"},
                    headers={"X-PulseTwin-Role": "manager"})
    assert r.status_code == 422


def test_a_run_opens_a_thread(client) -> None:
    body = client.post("/api/triage/runs",
                       json={"plant_id": "plant_a", "intent": "handover"},
                       headers={"X-PulseTwin-Role": "manager"}).json()
    assert body["thread_id"] and body["turn_index"] == 0

    follow = client.post("/api/triage/runs",
                         json={"plant_id": "plant_a", "intent": "shift_intel",
                               "thread_id": body["thread_id"]},
                         headers={"X-PulseTwin-Role": "manager"})
    # The first turn is still `queued` (Popen is monkeypatched away), so the busy guard
    # must fire rather than letting two turns race for the same index.
    assert follow.status_code == 409


def test_a_thread_cannot_change_plants(client) -> None:
    body = client.post("/api/triage/runs",
                       json={"plant_id": "plant_a", "intent": "handover"},
                       headers={"X-PulseTwin-Role": "manager"}).json()
    r = client.post("/api/triage/runs",
                    json={"plant_id": "plant_b", "intent": "handover",
                          "thread_id": body["thread_id"]},
                    headers={"X-PulseTwin-Role": "manager"})
    assert r.status_code == 409


def test_an_unknown_thread_is_404(client) -> None:
    r = client.post("/api/triage/runs",
                    json={"plant_id": "plant_a", "intent": "handover",
                          "thread_id": "no_such_thread"},
                    headers={"X-PulseTwin-Role": "manager"})
    assert r.status_code == 404


def test_the_first_heartbeat_is_written_before_the_worker_starts(client, tmp_path,
                                                                  monkeypatch) -> None:
    """The longest wait in a run is interpreter startup plus the first model round trip,
    all of it before the worker can write its own heartbeat. Without this the panel shows
    a bare spinner at exactly the moment an audience decides whether it works."""
    monkeypatch.setenv("PULSETWIN_JOBS_DIR", str(tmp_path))
    body = client.post("/api/triage/runs",
                       json={"plant_id": "plant_a", "intent": "handover"},
                       headers={"X-PulseTwin-Role": "manager"}).json()

    hb = json.loads(
        (tmp_path / "pulsetwin_triage_runs" / f"{body['run_id']}.json")
        .read_text(encoding="utf-8"))
    assert hb["v"] == R.HEARTBEAT_VERSION
    assert hb["phase"] == R.PHASE_ORDER[0]
    assert hb["status"] == "queued"


def test_thread_listing_is_plant_scoped(client) -> None:
    client.post("/api/triage/runs",
                json={"plant_id": "plant_a", "intent": "handover"}, headers={"X-PulseTwin-Role": "manager"})
    body = client.get("/api/triage/threads?plant_id=plant_a", headers={"X-PulseTwin-Role": "manager"}).json()
    assert body["count"] >= 1
    assert all(t["plant_id"] == "plant_a" for t in body["threads"])


def test_threads_require_manager(client) -> None:
    assert client.get("/api/triage/threads?plant_id=plant_a",
                      headers={"X-PulseTwin-Role": "supervisor"}).status_code == 403
