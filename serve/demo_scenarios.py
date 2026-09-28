"""Grand-finale scenarios (ADR-067): real runs of the live twin, never canned answers.

A scenario is only a way to START the real system in a known physical condition: it picks
stations from the plant's own topology (no station literals -- CLAUDE.md §1), starts a
live-twin run with the Incident Commander routing on, and -- for the two scenarios whose
trigger is a person rather than a detector -- files the operator report once the run has
produced enough telemetry to investigate. Everything after that (detection, the
investigation, the hypotheses, the twin tests, the governor, the recommendation) is the
production code path. The API returns what was STARTED, never what will be concluded.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from serve import commander, db, demo_session
from serve import incident_evidence as E

# Simulated events before an operator report is filed (~35 simulated minutes of line):
# enough window for every probe's minimum sample.
REPORT_AFTER_EVENTS = 1200


def _throughput_station(topo: list[dict[str, Any]]) -> int:
    """A sensored mid-to-late-line station with a downstream neighbour."""
    n = len(topo)
    for t in topo[int(0.6 * n):]:
        if (t["sensor_tier"] != "manual" and not t["is_inspection"] and len(t["sensors"]) >= 2
                and t["index"] + 1 < n):
            return t["station_id"]
    return topo[n // 2]["station_id"]


def _torque_station(topo: list[dict[str, Any]], base: dict[str, Any]) -> int:
    """The torque station whose recorded torque is steadiest (lowest coefficient of
    variation) -- so an injected fault is the only thing moving it. A station with its own
    designed deficit (tool wear through the day) would make a correct repair read as
    'harmful' to the outcome evaluator; measured on the first scenario-B run."""
    def cv(sid: int) -> float:
        ref = base["signals"].get((sid, "torque_nm", "*"))
        return ref["sd"] / ref["mean"] if ref and ref["mean"] else 9.9
    return min((t["station_id"] for t in topo[2:] if "torque_nm" in t["sensors"]
                and not t["is_inspection"]), key=cv)


def _manual_station(topo: list[dict[str, Any]]) -> int:
    return next(t["station_id"] for t in topo if t["sensor_tier"] == "manual")


SCENARIOS: dict[str, dict[str, Any]] = {
    "bottleneck": {
        "title": "A - Bottleneck: a station held by downstream congestion",
        "twin_scenario": "buffer_congestion", "severity": 0.7, "station": _throughput_station,
        "incident_start_s": 900, "trigger": "watch (starvation detector)",
        "shows": "dynamic specialist selection, competing hypotheses, a twin test per "
                 "hypothesis, governor L3, approval, action, outcome, memory"},
    "delayed_defect": {
        "title": "B - Delayed defect: a torque fault found at the final gate",
        "twin_scenario": "torque_degradation", "severity": 0.8, "station": _torque_station,
        # 15 min of healthy line first: the exposure-split test needs units that passed
        # the station BEFORE the fault to compare against.
        "incident_start_s": 900, "trigger": "defect-escape detector at the inspection gate",
        "shows": "genealogy trace backwards, affected window, in-flight units, quality hold, "
                 "maintenance-window planning"},
    "sensor_poor": {
        "title": "C - Sensor-poor station: a manual-only station under suspicion",
        "twin_scenario": "rushed_checklist", "severity": 0.8, "station": _manual_station,
        "incident_start_s": 600, "trigger": "operator report",
        "report": "Line lead: the checks at this station look rushed since the last break.",
        "kind": "equipment",
        "shows": "sensor-maturity cap, manual evidence, confidence lowered, recommendation "
                 "withheld, manual inspection requested"},
    "false_alarm": {
        "title": "D - False alarm: a report the evidence does not support",
        "twin_scenario": "normal", "severity": 0.0, "station": _throughput_station,
        "incident_start_s": 0, "trigger": "operator report",
        "report": "Operator: this station feels slow today.", "kind": "throughput",
        "shows": "every probe within baseline, 'no fault' leads, no work order raised"},
    "model_drift": {
        "title": "E - Model drift: paint-zone inputs outside the training distribution",
        "twin_scenario": "high_humidity", "severity": 1.0, "station": None,
        "incident_start_s": 600, "trigger": "watch (paint_env detector)",
        "shows": "PSI above the block threshold, MODEL_DEGRADED, governor investigation-only"},
}


def catalogue() -> list[dict[str, Any]]:
    return [{"key": k, **{f: v[f] for f in ("title", "twin_scenario", "trigger", "shows")}}
            for k, v in SCENARIOS.items()]


def start(deps: commander.Deps, key: str, plant_id: str, speed: float, role: str
          ) -> dict[str, Any]:
    spec = SCENARIOS.get(key)
    if spec is None:
        raise commander.CommanderError(f"unknown scenario '{key}'; one of {sorted(SCENARIOS)}")
    live = deps.live
    first_line = demo_session.next_start(deps.ops, live, deps.artifacts, plant_id)
    active = live.active(plant_id)
    if active:
        try:
            live.stop(active["run_id"])
        except RuntimeError:
            raise commander.CommanderError(f"run {active['run_id']} is owned by another process")
    topo = E.topology(deps.main_con, plant_id)
    base = E.baseline(str(deps.artifacts), plant_id)
    pick = spec["station"]
    station = (pick(topo, base) if pick is _torque_station else pick(topo)) if pick else None
    run = live.start(plant_id, 42, spec["twin_scenario"], speed, spec["severity"], station,
                     None, 40000, spec["incident_start_s"], commander=True,
                     start_line=first_line)
    db.execute(deps.ops, "INSERT INTO audit_log (user_role, action, resource_type, resource_id, "
                         "payload) VALUES (?,?,?,?,?)",
               (role, "demo_scenario_started", "simulation_run", run["run_id"],
                json.dumps({"scenario": key, "station_id": station, "start_line": first_line})))
    if spec.get("report"):
        threading.Thread(target=_file_report, args=(deps, run["run_id"], plant_id, key, station),
                         daemon=True, name=f"demo-report-{run['run_id']}").start()
    return {"scenario": key, "title": spec["title"], "run_id": run["run_id"],
            "plant_id": plant_id, "station_id": station, "twin_scenario": spec["twin_scenario"],
            "trigger": spec["trigger"], "shows": spec["shows"], "speed": speed}


def _file_report(deps: commander.Deps, run_id: str, plant_id: str, key: str,
                 station: int) -> None:
    spec = SCENARIOS[key]
    deadline = time.time() + 600
    while time.time() < deadline:
        run = db.query_one(deps.ops, "SELECT status, event_count FROM simulation_runs WHERE "
                                     "run_id=?", (run_id,))
        if run is None or run["status"] not in ("running", "paused"):
            return
        if int(run["event_count"] or 0) >= REPORT_AFTER_EVENTS:
            break
        threading.Event().wait(1.0)   # not time.sleep: tests/test_serve_imports.py (ADR-009)
    else:
        return
    try:
        inc = commander.open_incident(
            deps, plant_id=plant_id, kind=spec["kind"], station_id=station,
            title=f"Operator report at S{station}", source="manual", opened_by="supervisor",
            sim_run_id=run_id, notes=spec["report"], scenario_key=key)
        commander.launch(deps, inc["incident_id"], "supervisor")
    except Exception as exc:  # noqa: BLE001 - a demo helper must not crash the server
        print(f"[demo] report for {run_id} failed: {exc}")
