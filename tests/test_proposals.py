"""Watch -> agent -> proposal -> human decision -> twin (ADR-051), end to end.

Firings here are REAL: produced by serve/watch.py from the live simulator's own
`_transform` over the committed stream. The plans then run against the real API, their
proposed actions go through the identical verifier, and a human decides them through the
real endpoints.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"

if not (ARTIFACTS / "pulsetwin.db").exists():
    pytest.skip("artifacts not built", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from genai import offline_triage, tools as T, verifier as V  # noqa: E402
from genai.api_client import PulseTwinAPI  # noqa: E402
from genai.api_client import testclient_transport as _tc_transport  # noqa: E402
from genai.finding import ACTION_TYPES, Citation, Claim, Finding, ProposedAction  # noqa: E402
from serve import db  # noqa: E402
from serve.live_simulation import MITIGATIONS, LiveSimulationManager, _mitigated  # noqa: E402
from serve.main import app  # noqa: E402
from serve.watch import LineWatch, load_rules  # noqa: E402

PLANT = "plant_a"
MANAGER = {"X-PulseTwin-Role": "manager"}
RUN = f"TEST-{secrets.token_hex(4)}"


def _transformed(scenario: str, station: int | None, limit: int, control_extra=None):
    fake = SimpleNamespace(plants={PLANT: {"budget_threshold": 1.0}},
                           _raise_risk=lambda event, sev: None)
    control = {"scenario": scenario, "severity": 0.7, "affected_station": station or 0,
               "affected_lot": None, "incident_start_s": 0.0, "first_event_ts": None,
               **(control_extra or {})}
    with open(ARTIFACTS / f"stream_{PLANT}.jsonl", encoding="utf-8") as f:
        for seq, line in enumerate(f, start=1):
            if seq > limit:
                return
            yield LiveSimulationManager._transform(
                fake, json.loads(line), RUN, PLANT, 42, seq, control)


def _first_firing(scenario: str, detector: str, station: int | None):
    watch = LineWatch(load_rules(ARTIFACTS, PLANT))
    for event in _transformed(scenario, station, 3000):
        for f in watch.observe(event):
            if f.detector == detector:
                return f
    raise AssertionError(f"{scenario} never fired {detector}")


def _torque_station() -> int:
    rules = load_rules(ARTIFACTS, PLANT)
    return min(int(k) for k, v in rules["stations"].items() if "torque_nm" in v["sensors"])


@pytest.fixture(scope="module")
def ctx():
    with TestClient(app) as c:
        ops = app.state.ops
        firings = {
            "starvation": _first_firing("station_breakdown", "starvation", _torque_station()),
            "paint_env": _first_firing("high_humidity", "paint_env", None),
            "tool_wear": _first_firing("torque_degradation", "tool_wear", _torque_station()),
        }
        for det, f in firings.items():
            db.execute(ops, """INSERT INTO action_proposals (proposal_id, sim_run_id,
                                   plant_id, detector, station_id, fired_at,
                                   trigger_evidence, status)
                               VALUES (?,?,?,?,?,?,?,'OPEN')""",
                       (f"{RUN}-{det}", RUN, PLANT, det, f.station_id, f.ts,
                        json.dumps(f.evidence)))
        registry = T.build_registry(["plant_a", "plant_b"])
        api = PulseTwinAPI("http://testserver", "manager", RUN, transport=_tc_transport(c))
        yield c, registry, api, firings
        db.execute(ops, "DELETE FROM action_proposals WHERE sim_run_id = ?", (RUN,))
        db.execute(ops, "DELETE FROM triage_runs WHERE run_id LIKE ?", (f"{RUN}%",))


def _station_ids(client) -> set[int]:
    return {s["station_id"] for s in
            client.get(f"/api/config/stations?plant_id={PLANT}").json()["stations"]}


# ------------------------------------------------------------------- the contract

def test_every_action_type_has_a_twin_mitigation() -> None:
    """genai/ and serve/ cannot import each other; this keeps the catalogue in step."""
    assert set(MITIGATIONS) == set(ACTION_TYPES)


@pytest.mark.parametrize("detector", ["starvation", "paint_env", "tool_wear"])
def test_each_watch_plan_proposes_verified_actions(ctx, detector) -> None:
    client, registry, api, firings = ctx
    f = firings[detector]
    loop, finding = offline_triage.run_plan(
        detector, registry, api, plant_id=PLANT, question=f"watch {detector}",
        station_id=None if detector == "paint_env" else f.station_id)
    ver, served = V.verify(finding, loop.transcript, station_ids=_station_ids(client))

    assert ver.status != V.REJECTED, ver.reject_reason
    assert served.proposed_actions, ver.action_issues
    assert ver.actions_served == ver.actions_total
    ok = {v.claim_id for v in ver.claim_verdicts if v.ok}
    for action in served.proposed_actions:
        assert action.action_type in ACTION_TYPES
        assert set(action.claim_ids) <= ok


def test_an_action_resting_on_an_unverified_claim_is_dropped() -> None:
    transcript = {"call_0001": {"ok": True, "data": {"x": 5.0, "y": 6.0, "z": 7.0}}}
    good = [Claim(f"c{i}", f"{k} holds", v, "count", "measured",
                  Citation("call_0001", f"/data/{k}"))
            for i, (k, v) in ((1, ("x", 5.0)), (3, ("y", 6.0)), (4, ("z", 7.0)))]
    # c2 cites x but states nine: it fails C2 and is redacted -- one in four stays
    # under the redaction ratio, so the RUN survives and only the action is at stake.
    bad = Claim("c2", "x is nine", 9.0, "count", "measured", Citation("call_0001", "/data/x"))
    finding = Finding(
        question="q", summary="s", claims=[*good, bad], recommendation="r",
        proposed_actions=[
            ProposedAction("a1", "maintenance_ticket", {"station_id": 3}, "fix", ["c1"]),
            ProposedAction("a2", "maintenance_ticket", {"station_id": 3}, "fix", ["c2"]),
            ProposedAction("a3", "stop_the_line", {}, "why not", ["c1"]),
            ProposedAction("a4", "hvac_setpoint", {}, "missing param", ["c1"]),
            ProposedAction("a5", "resequence_paint_queue", {}, "no evidence", []),
        ])
    ver, served = V.verify(finding, transcript)
    assert [a.id for a in served.proposed_actions] == ["a1"]
    assert ver.actions_total == 5 and ver.actions_served == 1
    assert len(ver.action_issues) == 4


# ---------------------------------------------------------------- human decision

def _seed_agent_run(ops, proposal_id: str, actions: list[dict]) -> None:
    run_id = f"{proposal_id}-run"
    db.execute(ops, """INSERT INTO triage_runs (run_id, plant_id, question, requested_by,
                           acting_role, mode, status, finding)
                       VALUES (?,?,?,?,?,?,?,?)""",
               (run_id, PLANT, "q", "auto-monitor", "system", "deterministic", "verified",
                json.dumps({"summary": "s", "proposed_actions": actions})))
    db.execute(ops, "UPDATE action_proposals SET triage_run_id=? WHERE proposal_id=?",
               (run_id, proposal_id))


def test_a_human_approves_one_verified_action_exactly_once(ctx) -> None:
    client, *_ = ctx
    pid = f"{RUN}-paint_env"
    _seed_agent_run(app.state.ops, pid, [
        {"id": "a1", "action_type": "hvac_setpoint",
         "params": {"target_humidity_pct": 55.0}, "rationale": "r", "claim_ids": ["c1"]}])

    listed = {p["proposal_id"]: p for p in client.get(
        f"/api/proposals?plant_id={PLANT}&limit=50", headers=MANAGER).json()["proposals"]}
    assert listed[pid]["status"] == "PROPOSED"

    assert client.post(f"/api/proposals/{pid}/approve", json={"action_id": "a1"},
                       headers={"X-PulseTwin-Role": "supervisor"}).status_code == 403
    assert client.post(f"/api/proposals/{pid}/approve", json={"action_id": "a9"},
                       headers=MANAGER).status_code == 404

    r = client.post(f"/api/proposals/{pid}/approve", json={"action_id": "a1"},
                    headers=MANAGER).json()
    # No live run owns this proposal, so the decision is recorded but not simulated.
    assert r["status"] == "APPROVED" and r["applied_to_twin"] is False
    assert client.post(f"/api/proposals/{pid}/approve", json={"action_id": "a1"},
                       headers=MANAGER).status_code == 409

    audit = db.query_one(app.state.ops, "SELECT * FROM audit_log WHERE action=? AND "
                         "resource_id=? ORDER BY id DESC LIMIT 1",
                         ("proposal_approved", pid))
    assert audit and audit["user_role"] == "manager"


def test_a_proposal_still_being_investigated_cannot_be_approved(ctx) -> None:
    client, *_ = ctx
    pid = f"{RUN}-tool_wear"
    listed = {p["proposal_id"]: p for p in client.get(
        f"/api/proposals?plant_id={PLANT}&limit=50", headers=MANAGER).json()["proposals"]}
    assert listed[pid]["status"] == "NO_AGENT"
    assert client.post(f"/api/proposals/{pid}/approve", json={"action_id": "a1"},
                       headers=MANAGER).status_code == 409
    r = client.post(f"/api/proposals/{pid}/reject", json={"note": "not now"},
                    headers=MANAGER)
    assert r.json()["status"] == "REJECTED"
    assert client.post(f"/api/proposals/{pid}/reject", json={"note": "again"},
                       headers=MANAGER).status_code == 409


def test_the_watch_firings_endpoint_serves_the_evidence(ctx) -> None:
    client, *_ = ctx
    body = client.get(f"/api/watch/firings?plant_id={PLANT}&detector=starvation&limit=20",
                      headers=MANAGER).json()
    mine = [f for f in body["firings"] if f["sim_run_id"] == RUN]
    assert mine and mine[0]["evidence"]["stalled_share"] >= 0.5


# ------------------------------------------------------------------ the twin applies

def test_an_approved_hvac_action_removes_the_humidity_excess_in_the_twin() -> None:
    hvac = [{**MITIGATIONS["hvac_setpoint"], "station": 0}]
    humid = {e["unit_id"] + str(e["station_id"]): e.get("humidity_pct")
             for e in _transformed("high_humidity", None, 300)}
    fixed = {e["unit_id"] + str(e["station_id"]): e.get("humidity_pct")
             for e in _transformed("high_humidity", None, 300, {"mitigations": hvac})}
    normal = {e["unit_id"] + str(e["station_id"]): e.get("humidity_pct")
              for e in _transformed("normal", None, 300)}
    assert fixed == normal and humid != normal


def test_a_station_mitigation_applies_only_at_its_own_station() -> None:
    control = {"mitigations": [{**MITIGATIONS["maintenance_ticket"], "station": 8}]}
    assert _mitigated(control, "torque_degradation", 8, "signal") == 1.0
    assert _mitigated(control, "torque_degradation", 9, "signal") == 0.0
    assert _mitigated(control, "high_humidity", 8, "signal") == 0.0
    risk_only = {"mitigations": [{**MITIGATIONS["resequence_paint_queue"], "station": 0}]}
    assert _mitigated(risk_only, "high_humidity", 16, "signal") == 0.0
    assert _mitigated(risk_only, "high_humidity", 16, "risk") == 1.0


# ---------------------------------------------------------- counterfactual (ADR-055)

def test_a_proposal_waits_for_its_counterfactual(ctx, tmp_path, monkeypatch) -> None:
    """Nothing is decidable before the twin has simulated it -- and a stalled job is
    reported stopped, never left looking like it is still running."""
    client, *_ = ctx
    monkeypatch.setenv("PULSETWIN_JOBS_DIR", str(tmp_path))
    pid = f"{RUN}-starvation"
    _seed_agent_run(app.state.ops, pid, [
        {"id": "a1", "action_type": "increase_feed_buffer",
         "params": {"station_id": 3, "buffer_delta": 2}, "rationale": "r",
         "claim_ids": ["c1"]}])
    jobs = tmp_path / "pulsetwin_triage_runs"
    jobs.mkdir()
    path = jobs / f"{pid}.forecast.json"

    def listed() -> dict:
        return {p["proposal_id"]: p for p in client.get(
            f"/api/proposals?plant_id={PLANT}&limit=50",
            headers=MANAGER).json()["proposals"]}[pid]

    path.write_text(json.dumps({"version": 1, "status": "running"}))
    assert listed()["status"] == "SIMULATING"
    assert client.post(f"/api/proposals/{pid}/approve", json={"action_id": "a1"},
                       headers=MANAGER).status_code == 409

    arm = {"action_type": "increase_feed_buffer", "modelled": True,
           "throughput_uph": {"mean": 53.9, "ci_low": 53.7, "ci_high": 54.1},
           "vs_do_nothing": {"throughput_uph": {"mean": 0.46, "ci_low": 0.13,
                                                "ci_high": 0.83}, "distinguishable": True}}
    nothing = {"action_type": "do_nothing", "modelled": True,
               "throughput_uph": {"mean": 53.5, "ci_low": 53.2, "ci_high": 53.7}}
    path.write_text(json.dumps({"version": 1, "status": "done", "provenance": "inferred",
                                "arms": [nothing, arm]}))
    view = listed()
    assert view["status"] == "PROPOSED"
    assert view["proposed_actions"][0]["simulated_impact"] == arm
    assert view["do_nothing_forecast"] == nothing

    path.write_text(json.dumps({"version": 1, "status": "running"}))
    old = path.stat().st_mtime - 3600
    import os
    os.utime(path, (old, old))
    view = listed()
    assert view["counterfactual"]["status"] == "failed"
    assert view["status"] == "PROPOSED"  # a dead job must not hold the decision forever
