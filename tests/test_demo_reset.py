"""LOOP criteria A / T / U: a demo reset gives a clean session and deletes nothing, a
production backend (no PULSETWIN_DEMO_MODE) refuses it, and demo runs start at a quiet
point of the recorded shift that rotates between sessions."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
if not (ARTIFACTS / "pulsetwin.db").exists():
    pytest.skip("artifacts not built", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from serve import commander, db, demo_session  # noqa: E402
from serve.main import app  # noqa: E402

PLANT = "plant_a"
SUP = {"X-PulseTwin-Role": "supervisor"}
M = {"X-PulseTwin-Role": "manager"}
ENV = ("PULSETWIN_OPS_DB", "PULSETWIN_JOBS_DIR", "PULSETWIN_COMMANDER_TICK_S",
       "PULSETWIN_JOBS_ENABLED", demo_session.DEMO_ENV)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("demo_reset")
    old = {k: os.environ.get(k) for k in ENV}
    os.environ.update(PULSETWIN_OPS_DB=str(tmp / "ops.db"), PULSETWIN_JOBS_DIR=str(tmp / "jobs"),
                      PULSETWIN_COMMANDER_TICK_S="0", PULSETWIN_JOBS_ENABLED="0")
    os.environ.pop(demo_session.DEMO_ENV, None)
    with TestClient(app) as c:
        yield c
        live = app.state.live_simulation
        run = live.active(PLANT)
        if run:
            live.stop(run["run_id"])
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _count(table: str) -> int:
    return db.query_one(app.state.ops, f"SELECT COUNT(*) AS n FROM {table}")["n"]


def test_quiet_starts_are_really_quiet():
    """Each start point is followed by QUIET_S of stream without one watch firing, and
    line 0 (where the S3 Mode C episode fires 8 minutes in) is not one of them."""
    starts = demo_session.quiet_starts(str(ARTIFACTS), PLANT)
    assert len(starts) >= 2 and 0 not in starts
    assert list(starts) == sorted(set(starts))


def test_reset_refused_without_demo_mode(client):
    before = _count("simulation_runs")
    r = client.post("/api/demo/reset", json={"plant_id": PLANT}, headers=SUP)
    assert r.status_code == 403 and "PULSETWIN_DEMO_MODE" in r.json()["detail"]
    assert _count("simulation_runs") == before
    assert client.get("/api/demo/scenarios", headers=SUP).json()["demo_mode"] is False


def test_reset_closes_the_session_and_deletes_nothing(client, monkeypatch):
    monkeypatch.setenv(demo_session.DEMO_ENV, "1")
    live, ops = app.state.live_simulation, app.state.ops
    old = live.start(PLANT, 42, "normal", 60.0, 0.0, None, None, 40000, commander=True)
    # An incident needs telemetry to investigate: wait (bounded) for the run's first event.
    # Opening it straight after start() failed 2 of ~23 runs (independent verifier).
    deadline = time.time() + 30
    while time.time() < deadline and not (db.query_one(
            ops, "SELECT event_count FROM simulation_runs WHERE run_id=?", (old["run_id"],))
            or {}).get("event_count"):
        time.sleep(0.1)
    inc = commander.open_incident(commander.Deps.from_state(app.state, ARTIFACTS), plant_id=PLANT,
                                  kind="throughput", station_id=None, title="left over",
                                  source="manual", opened_by="supervisor",
                                  sim_run_id=old["run_id"], notes="stale")
    rows_before = _count("incidents")

    r = client.post("/api/demo/reset", json={"plant_id": PLANT}, headers=SUP)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stopped_run"] in (old["run_id"], None) and body["run_id"] != old["run_id"]
    assert inc["incident_id"] in body["closed_incidents"]

    # Nothing deleted: the old incident is closed, still there, and audited.
    assert _count("incidents") == rows_before
    assert commander.get_incident(ops, inc["incident_id"])["status"] == "closed"
    assert db.query_one(ops, "SELECT 1 AS x FROM audit_log WHERE action='demo_reset' AND "
                             "resource_id=?", (body["run_id"],))

    # The new session is clean and starts at a quiet point, not line 0.
    cur = client.get(f"/api/incidents?plant_id={PLANT}", headers=SUP).json()
    assert cur["run"]["run_id"] == body["run_id"] and cur["incidents"] == []
    assert client.get(f"/api/decisions/pending?plant_id={PLANT}", headers=M).json()["decisions"] == []
    first = live._controls[body["run_id"]]["start_line"]
    assert first in demo_session.quiet_starts(str(ARTIFACTS), PLANT)

    # Once that line has been SEEN, a second reset moves to a different point of the shift.
    _seen(body["run_id"])
    r2 = client.post("/api/demo/reset", json={"plant_id": PLANT}, headers=SUP).json()
    assert live._controls[r2["run_id"]]["start_line"] != first


def test_reset_needs_a_role(client, monkeypatch):
    monkeypatch.setenv(demo_session.DEMO_ENV, "1")
    r = client.post("/api/demo/reset", json={"plant_id": "no_such_plant"}, headers=SUP)
    assert r.status_code in (403, 404, 409)


LEAD = {"X-PulseTwin-Role": "leadership"}


def test_only_decision_authority_can_reject_or_reinvestigate(client):
    """Found by the loop's independent verifier: the role hierarchy let leadership (autonomy
    ceiling L2) reject and re-open recommendations it cannot approve (L3). The authority
    check runs before the lookup: leadership is refused outright, a manager reaches the
    (here unknown) recommendation."""
    for path, body in (("reject", {"reason": "not now"}),
                       ("request-investigation", {"note": "look again"})):
        r = client.post(f"/api/recommendations/REC-NOPE/{path}", json=body, headers=LEAD)
        assert r.status_code == 403 and "L3" in r.json()["detail"], (path, r.text)
        assert client.post(f"/api/recommendations/REC-NOPE/{path}", json=body,
                           headers=M).status_code == 404


def test_only_one_unattended_triage_at_a_time(client, monkeypatch):
    """Unbounded, every new alert started a multi-agent triage with its own twin experiments
    (300 % CPU on an idle run, twin slots never free for the commander). With one
    auto-monitor triage in flight, the next alert starts nothing."""
    monkeypatch.setenv("PULSETWIN_JOBS_ENABLED", "1")
    ops = app.state.ops
    db.execute(ops, "INSERT INTO triage_runs (run_id, plant_id, question, requested_by, acting_role, "
                    "mode, status, intent) VALUES (?,?,?,?,?,?,?,?)",
               ("TRG-busy", PLANT, "auto", "auto-monitor", "system", "auto", "running", "incident"))
    before = _count("triage_runs")
    app.state.live_simulation._trigger_auto_investigation(
        "RUN-X", PLANT, {"unit_id": "U000001", "station_id": 5})
    assert _count("triage_runs") == before


def _seen(run_id: str, events: int = demo_session.SEEN_EVENTS) -> None:
    """Stop a run and fix how much of it was shown (its producer thread would otherwise
    keep moving event_count under the test)."""
    live = app.state.live_simulation
    if live.active(PLANT) and live.active(PLANT)["run_id"] == run_id:
        live.stop(run_id)
    db.execute(app.state.ops, "UPDATE simulation_runs SET event_count=? WHERE run_id=?",
               (events, run_id))


def test_a_session_keeps_its_vehicles_and_the_next_session_changes_them(client, monkeypatch):
    """Independent-verifier attacks, each once a failure:
    - a preset continues its session's start point, even after the healthy run ended;
    - the next session never replays the last session anyone SAW -- including after a
      reset whose run showed nothing (reset -> reset -> preset repeated 47/47 when the
      rotation was a reset counter)."""
    monkeypatch.setenv(demo_session.DEMO_ENV, "1")
    ops, live = app.state.ops, app.state.live_simulation
    r1 = client.post("/api/demo/reset", json={"plant_id": PLANT}, headers=SUP).json()
    s1 = r1["start_line"]
    assert demo_session.next_start(ops, live, ARTIFACTS, PLANT) == s1   # preset: same session
    _seen(r1["run_id"])                                                  # ...even once it ended
    assert demo_session.next_start(ops, live, ARTIFACTS, PLANT) == s1

    r2 = client.post("/api/demo/reset", json={"plant_id": PLANT}, headers=SUP).json()
    assert r2["start_line"] != s1                                        # new session
    _seen(r2["run_id"], events=demo_session.SEEN_EVENTS - 1)             # ...a double-click nobody saw
    r3 = client.post("/api/demo/reset", json={"plant_id": PLANT}, headers=SUP).json()
    assert r3["start_line"] != s1                                        # still not the last SEEN one
    assert demo_session.next_start(ops, live, ARTIFACTS, PLANT) == r3["start_line"]


def test_healthy_line_outlasts_a_presentation():
    """At speed 60 the late start point ran out of stream in ~7.5 min: the line went dead."""
    times = demo_session._line_times(str(ARTIFACTS), PLANT)
    for start in demo_session.quiet_starts(str(ARTIFACTS), PLANT):
        speed = demo_session.healthy_speed(ARTIFACTS, PLANT, start)
        assert (times[-1] - times[start]) / speed >= demo_session.MIN_LINE_WALL_S - 1
