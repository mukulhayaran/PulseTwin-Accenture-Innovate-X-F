"""ADR-064: the experiment API -- validation, resource limits, budgets, cancellation,
abandoned-worker reaping, and results that survive an API restart.

The detached spawn is replaced by a recorder, and the worker's own `execute` then runs
IN-PROCESS on the real twin: the same code path the subprocess runs, without a background
simulation racing the rest of the suite.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("OMP_NUM_THREADS", "1")

from fastapi.testclient import TestClient  # noqa: E402

from offline import sim_experiment as X  # noqa: E402
from serve import db  # noqa: E402
from serve.main import app  # noqa: E402
from serve.routers import experiments as E  # noqa: E402

MANAGER = {"X-PulseTwin-Role": "manager"}
BODY = {"plant_id": "plant_a", "station_id": 12, "objective": "throughput",
        "interventions": [{"type": "cycle_time_pct", "params": {"pct": -10}}],
        "seeds": [1, 2], "horizon_s": 1200}


class Spawned:
    argv: list[list[str]] = []

    def __init__(self, argv, **kw):
        Spawned.argv.append(argv)


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("PULSETWIN_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.setenv("PULSETWIN_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setattr(E.subprocess, "Popen", Spawned)
    Spawned.argv = []
    with TestClient(app) as c:
        yield c


def _ops():
    return app.state.ops


def test_create_validates_against_the_catalogue(client):
    bad = [
        {**BODY, "interventions": [{"type": "cycle_time_pct", "params": {"pct": -90}}]},
        {**BODY, "interventions": [{"type": "cycle_time_pct", "params": {}}]},
        {**BODY, "interventions": [{"type": "teleport", "params": {}}]},
        {**BODY, "station_id": 999},
        {**BODY, "objective": "happiness"},
        {**BODY, "horizon_s": 999999},
        {**BODY, "seeds": list(range(40))},
        {**BODY, "seeds": [1, 1]},
        {**BODY, "interventions": [{"type": "cycle_time_pct", "params": {"pct": 1}}] * 5},
        {**BODY, "incident": {"scenario": "alien_invasion"}},
    ]
    for body in bad:
        assert client.post("/api/simulation-experiments", json=body,
                           headers=MANAGER).status_code == 422, body
    assert Spawned.argv == []
    assert client.post("/api/simulation-experiments", json=BODY,
                       headers={"X-PulseTwin-Role": "supervisor"}).status_code == 403


def test_an_unsupported_intervention_is_recorded_as_refused_not_approximated(client):
    r = client.post("/api/simulation-experiments", headers=MANAGER, json={
        **BODY, "interventions": [{"type": "variant_sequencing", "params": {}},
                                  {"type": "feed_buffer_delta", "params": {"delta": 2}}]})
    assert r.status_code == 200
    scn = {s["label"]: s for s in r.json()["scenarios"]}
    assert scn["variant_sequencing"]["supported"] is False
    assert "random" in scn["variant_sequencing"]["reason"]
    assert scn["feed_buffer_delta(delta=2)"]["supported"] is True
    assert r.json()["method"] == "fresh_simulation"
    argv = Spawned.argv[-1]
    assert argv[argv.index("--experiment-id") + 1] == r.json()["experiment_id"]
    # the plant's published threshold travels with the job (no SQLite artifact in prod)
    assert float(argv[argv.index("--budget-threshold") + 1]) > 0


def test_a_full_experiment_runs_persists_and_survives_a_restart(client, monkeypatch, tmp_path):
    eid = client.post("/api/simulation-experiments", json=BODY,
                      headers=MANAGER).json()["experiment_id"]
    summary = X.execute(_ops(), eid)                       # the worker, in-process
    assert summary["verdict"] in ("improved", "too_uncertain", "no_improvement")
    view = client.get(f"/api/simulation-experiments/{eid}", headers=MANAGER).json()
    assert view["status"] == "done" and view["progress"]["results_done"] == 4
    assert view["simulator_version"].startswith("live_twin@")
    base, cyc = view["scenarios"]
    assert base["is_baseline"] and base["metrics"]["throughput_uph"]["n"] == 2
    assert cyc["vs_baseline"]["paired_seeds"] == 2
    assert cyc["vs_baseline"]["throughput_uph"]["provenance"] == "inferred"
    rows = client.get(f"/api/simulation-experiments/{eid}/comparison",
                      headers=MANAGER).json()["rows"]
    assert [r["is_baseline"] for r in rows] == [True, False]

    listed = client.get("/api/simulation-experiments?plant_id=plant_a",
                        headers=MANAGER).json()["experiments"]
    assert listed[0]["experiment_id"] == eid and listed[0]["verdict"] == summary["verdict"]
    # "Restart": a new app lifecycle on the same database file still serves the result.
    # Last, because it closes the handle the outer client shares.
    with TestClient(app) as again:
        later = again.get(f"/api/simulation-experiments/{eid}", headers=MANAGER).json()
    assert later["scenarios"][1]["vs_baseline"] == cyc["vs_baseline"]


def test_concurrency_and_per_run_budgets_are_enforced_server_side(client, monkeypatch):
    monkeypatch.setitem(E.LIMITS, "max_concurrent", 2)
    for _ in range(2):
        assert client.post("/api/simulation-experiments", json=BODY,
                           headers=MANAGER).status_code == 200
    r = client.post("/api/simulation-experiments", json=BODY, headers=MANAGER)
    assert r.status_code == 429 and "already running" in r.json()["detail"]

    db.execute(_ops(), "UPDATE simulation_experiments SET status='done'")
    run = {**BODY, "origin_run_id": "run-x"}
    for _ in range(3):
        assert client.post("/api/simulation-experiments", json=run,
                           headers=MANAGER).status_code == 200
        db.execute(_ops(), "UPDATE simulation_experiments SET status='done'")
    r = client.post("/api/simulation-experiments", json=run, headers=MANAGER)
    assert r.status_code == 429 and "3 experiments" in r.json()["detail"]

    monkeypatch.setitem(E.LIMITS, "max_seeds_per_run", 5)
    r = client.post("/api/simulation-experiments", headers=MANAGER,
                    json={**BODY, "origin_run_id": "run-y", "seeds": [1, 2, 3]})
    assert r.status_code == 429 and "scenario-seeds" in r.json()["detail"]


def test_cancellation_before_start_and_between_seeds(client):
    eid = client.post("/api/simulation-experiments", json=BODY,
                      headers=MANAGER).json()["experiment_id"]
    assert client.post(f"/api/simulation-experiments/{eid}/cancel",
                       headers=MANAGER).json()["status"] == "cancelled"
    assert X.execute(_ops(), eid)["verdict"] == "cancelled"      # worker exits untouched
    assert client.post(f"/api/simulation-experiments/{eid}/cancel",
                       headers=MANAGER).status_code == 409

    eid2 = client.post("/api/simulation-experiments", json=BODY,
                       headers=MANAGER).json()["experiment_id"]
    db.execute(_ops(), "UPDATE simulation_experiments SET cancel_requested=1 "
                       "WHERE experiment_id=?", (eid2,))      # cancel arrives mid-run
    X.execute(_ops(), eid2)
    view = client.get(f"/api/simulation-experiments/{eid2}", headers=MANAGER).json()
    assert view["status"] == "cancelled" and view["progress"]["results_done"] == 0


def test_an_abandoned_worker_is_reported_and_reaped(client):
    eid = client.post("/api/simulation-experiments", json=BODY,
                      headers=MANAGER).json()["experiment_id"]
    old = (datetime.now(timezone.utc) - timedelta(seconds=E.STALE_S + 60)).isoformat()
    db.execute(_ops(), "UPDATE simulation_experiments SET status='running', heartbeat_at=? "
                       "WHERE experiment_id=?", (old, eid))
    view = client.get(f"/api/simulation-experiments/{eid}", headers=MANAGER).json()
    assert view["status"] == "failed" and "worker_stopped" in view["error"]
    assert E.reap_abandoned(_ops()) == 1
    row = db.query_one(_ops(), "SELECT status FROM simulation_experiments WHERE "
                               "experiment_id=?", (eid,))
    assert row["status"] == "failed"
