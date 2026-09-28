"""The Incident Commander: a stateful, bounded, auditable investigation (ADR-067).

    INCIDENT -> OBSERVE -> PLAN -> COLLECT -> UPDATE HYPOTHESES -> enough? --no--> PLAN
                                                                     |yes
                                              TEST IN TWIN (awaiting_simulation) -> DECIDE
                                              -> POLICY (governor) -> RECOMMEND | ABSTAIN

Division of labour:
  * a PLANNER chooses the next step. The default planner is deterministic (expected
    information gain over the hypothesis set). An LLM planner (genai/commander.py) may
    propose a plan instead -- as JSON, over HTTP -- and it goes through `validate_plan`
    exactly like the deterministic one: allowlisted agents and probes, station scope,
    budgets. An invalid plan is recorded and replaced (`fallback_used`).
  * PROBES (serve/incident_evidence.py) and the SIMULATOR (offline/sim_experiment.py)
    produce every number.
  * SCORING (serve/hypotheses.py) turns evidence into confidences with a published rule.
  * the GOVERNOR (serve/governor.py) decides how much authority any option carries.

All state lives in the database (`investigations.state` + `investigation_evidence` +
`investigation_steps`), so a restarted backend resumes where it stopped: the background
advancer (`start_advancer`) picks up every investigation that is still running.

Import wall: numpy/pydantic + stdlib, and serve/ modules only.
"""

from __future__ import annotations

import json
import math
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from serve import action_gateway as G
from serve import case_memory, db, governor
from serve import hypotheses as H
from serve import incident_evidence as E
from serve.agentic_config import C, CATALOGUE, CONF, CONFIG, PROHIBITED
from serve.privacy import contains_operator_identity
from serve.watch import load_rules

KINDS = ("throughput", "equipment", "quality", "environment")
# Which reproduced conditions each catalogue mitigation addresses (serve/live_simulation.py).
MITIGATIONS_FOR = {"increase_feed_buffer": ("buffer_congestion", "station_breakdown"),
                   "maintenance_ticket": ("torque_degradation", "station_breakdown")}
DETECTOR_KIND = {"starvation": "throughput", "tool_wear": "equipment",
                 "paint_env": "environment", "defect_escape": "quality"}
AGENTS = E.SPECIALISTS + ("simulation", "decide")
ACTIVE = ("running", "awaiting_telemetry", "awaiting_simulation", "awaiting_planner")
MANDATORY = ("model_health", "sensor_health")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _age_s(ts: str | None) -> float:
    if not ts:
        return 1e9
    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds()


@dataclass
class Deps:
    """What the commander needs from the running app -- `app.state` in production, a
    namespace in tests."""
    ops: Any
    main_con: Any
    artifacts: Path
    plants: dict[str, dict[str, Any]]
    scorers: dict[str, Any]
    live: Any = None

    @classmethod
    def from_state(cls, state: Any, artifacts: Path) -> "Deps":
        return cls(state.ops, state.main_con, artifacts, state.plants,
                   getattr(state, "scorers", {}) or {}, getattr(state, "live_simulation", None))


class CommanderError(ValueError):
    """A request the commander refuses; the router maps it to 409/422."""


def _audit(ops: Any, role: str, action: str, resource_type: str, resource_id: str,
           payload: dict[str, Any]) -> None:
    try:
        db.execute(ops, "INSERT INTO audit_log (user_role, action, resource_type, resource_id, "
                        "payload) VALUES (?,?,?,?,?)",
                   (role, action, resource_type, resource_id, json.dumps(payload, default=str)))
    except Exception as exc:  # noqa: BLE001 - auditing must not break the investigation
        print(f"[commander] audit failed for {action}: {exc}")


# =================================================================== incidents
def open_incident(deps: Deps, *, plant_id: str, kind: str, station_id: int | None, title: str,
                  source: str, opened_by: str, source_ref: str | None = None,
                  symptom: dict[str, Any] | None = None, sim_run_id: str | None = None,
                  trigger_seq: int | None = None, trigger_ts: str | None = None,
                  notes: str | None = None, scenario_key: str | None = None,
                  severity: str = "medium") -> dict[str, Any]:
    if plant_id not in deps.plants:
        raise CommanderError(f"unknown plant '{plant_id}'")
    if kind not in KINDS:
        raise CommanderError(f"kind must be one of {KINDS}")
    topo = E.topology(deps.main_con, plant_id)
    if kind == "quality" and station_id is None:
        station_id = next((t["station_id"] for t in topo if t["is_inspection"]), None)
    if station_id is not None and station_id not in {t["station_id"] for t in topo}:
        raise CommanderError(f"station {station_id} is not in {plant_id}")
    if notes and contains_operator_identity(notes):
        raise CommanderError("notes must not identify an individual operator "
                             "(use a shift, team or operator_group bucket)")
    if sim_run_id is None or trigger_seq is None:
        run = db.query_one(deps.ops, "SELECT run_id, event_count, simulation_time FROM "
                                     "simulation_runs WHERE plant_id=? ORDER BY started_at DESC "
                                     "LIMIT 1", (plant_id,))
        if run is None or not run.get("event_count"):
            raise CommanderError(f"no telemetry run on {plant_id} to investigate: start a "
                                 "simulation or connect the MQTT feed first")
        sim_run_id = sim_run_id or run["run_id"]
        if trigger_seq is None:
            last = db.query_one(deps.ops, "SELECT MAX(seq) AS s FROM simulation_events "
                                          "WHERE run_id=?", (sim_run_id,))
            trigger_seq = int((last or {}).get("s") or 0)
    if not trigger_ts:
        row = db.query_one(deps.ops, "SELECT ts, payload FROM simulation_events WHERE run_id=? "
                                     "AND seq<=? ORDER BY seq DESC LIMIT 1",
                           (sim_run_id, trigger_seq))
        trigger_ts = (_loads((row or {}).get("payload")) or {}).get("ts") if row else None
    if not trigger_ts:
        raise CommanderError("the run has no events yet")
    incident_id = "INC-" + secrets.token_hex(4).upper()
    db.execute(deps.ops, """INSERT INTO incidents
        (incident_id, created_at, updated_at, plant_id, sim_run_id, kind, station_id, title,
         severity, source, source_ref, symptom, status, trigger_ts, trigger_seq, opened_by,
         notes, scenario_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (incident_id, _now(), _now(), plant_id, sim_run_id, kind, station_id, title[:200],
         severity, source, source_ref, json.dumps(symptom or {}), "open", str(trigger_ts),
         int(trigger_seq), opened_by, (notes or None) and notes[:1000], scenario_key))
    _audit(deps.ops, opened_by, "incident_opened", "incident", incident_id,
           {"kind": kind, "station_id": station_id, "source": source, "source_ref": source_ref})
    return get_incident(deps.ops, incident_id)


def get_incident(ops: Any, incident_id: str) -> dict[str, Any]:
    row = db.query_one(ops, "SELECT * FROM incidents WHERE incident_id=?", (incident_id,))
    if row is None:
        raise KeyError(incident_id)
    row["symptom"] = _loads(row.get("symptom")) or {}
    return row


def _set_incident_status(ops: Any, incident_id: str, status: str) -> None:
    db.execute(ops, "UPDATE incidents SET status=?, updated_at=? WHERE incident_id=?",
               (status, _now(), incident_id))


# =================================================================== persistence
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(inv_id: str) -> threading.Lock:
    # ponytail: per-process locks; a Postgres advisory lock if serve ever runs >1 worker.
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(inv_id, threading.Lock())


def load(ops: Any, investigation_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    row = db.query_one(ops, "SELECT * FROM investigations WHERE investigation_id=?",
                       (investigation_id,))
    if row is None:
        raise KeyError(investigation_id)
    state = _loads(row["state"]) or {}
    state["evidence"] = [_evidence_row(r) for r in db.query_all(
        ops, "SELECT * FROM investigation_evidence WHERE investigation_id=? ORDER BY created_at, "
             "evidence_id", (investigation_id,))]
    return row, state


def _evidence_row(r: dict[str, Any]) -> dict[str, Any]:
    return {**r, "data": _loads(r.get("data")) or {}, "effects": _loads(r.get("effects")) or {}}


def latest_investigation(ops: Any, incident_id: str) -> dict[str, Any] | None:
    return db.query_one(ops, "SELECT investigation_id FROM investigations WHERE incident_id=? "
                             "ORDER BY created_at DESC LIMIT 1", (incident_id,))


def _save(ops: Any, inv_id: str, state: dict[str, Any], status: str) -> None:
    body = {k: v for k, v in state.items() if k != "evidence"}
    a = state.get("assessment") or {}
    b = state["budgets"]
    db.execute(ops, """UPDATE investigations SET state=?, version=version+1, status=?, phase=?,
                       planner_mode=?, step_count=?, probe_calls=?, simulations_run=?,
                       assessment=?, confidence=?, stop_reason=?, updated_at=?,
                       finished_at=? WHERE investigation_id=?""",
               (json.dumps(body, default=str), status, state["phase"], state["planner_mode"],
                b["steps"], b["probe_calls"], b["simulations"], a.get("assessment"),
                a.get("confidence"), state.get("stop_reason"), _now(),
                _now() if status in ("completed", "failed") else None, inv_id))


def _store_evidence(ops: Any, inv_id: str, step_id: str, items: list[E.Evidence]) -> list[dict]:
    out = []
    for ev in items:
        d = ev.to_dict()
        db.execute(ops, """INSERT INTO investigation_evidence
            (evidence_id, investigation_id, step_id, created_at, specialist, probe, station_id,
             provenance, summary, observed_at, data, effects, quality)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (d["evidence_id"], inv_id, step_id, _now(), d["specialist"], d["probe"],
             d["station_id"], d["provenance"], d["summary"][:500], d["observed_at"],
             json.dumps(d["data"], default=str), json.dumps(d["effects"]), d["quality"]))
        out.append({**d, "step_id": step_id, "investigation_id": inv_id})
    return out


def _record_step(ops: Any, inv_id: str, state: dict[str, Any], kind: str, agent: str | None,
                 *, plan: dict[str, Any] | None = None, plan_source: str | None = None,
                 plan_valid: bool | None = None, rejection: str | None = None,
                 evidence_ids: list[str] | None = None, summary: str = "",
                 started: float | None = None) -> str:
    seq = len(state["completed_steps"]) + 1
    step_id = f"{inv_id}-S{seq:02d}"
    duration = (time.time() - started) * 1000.0 if started else None
    db.execute(ops, """INSERT INTO investigation_steps
        (step_id, investigation_id, seq, created_at, kind, agent, plan, plan_source,
         plan_valid, rejection_reason, evidence_ids, summary, duration_ms)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (step_id, inv_id, seq, _now(), kind, agent, json.dumps(plan) if plan else None,
         plan_source, None if plan_valid is None else int(plan_valid), rejection,
         json.dumps(evidence_ids or []), summary[:500], duration))
    state["completed_steps"].append({"step_id": step_id, "seq": seq, "kind": kind,
                                     "agent": agent, "summary": summary[:300]})
    return step_id


def record_agent_run(ops: Any, *, inv: dict[str, Any], agent: str, started: float,
                     tools: list[str], evidence_read: int, provider: str | None = None,
                     model: str | None = None, prompt_tokens: int = 0,
                     completion_tokens: int = 0, cost_usd: float | None = None,
                     cost_source: str | None = None, schema_valid: bool | None = True,
                     policy_status: str | None = None, fallback: bool = False,
                     error: str | None = None) -> None:
    ended = time.time()
    db.execute(ops, """INSERT INTO agent_runs
        (run_id, incident_id, investigation_id, parent_run_id, agent_name, model_provider,
         model_name, started_at, ended_at, latency_ms, prompt_tokens, completion_tokens,
         estimated_cost_usd, cost_source, tools_called, evidence_read, output_schema_valid,
         verifier_status, policy_status, fallback_used, error)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("AR-" + secrets.token_hex(6), inv["incident_id"], inv["investigation_id"],
         inv["investigation_id"], agent, provider or "deterministic", model or "rules",
         datetime.fromtimestamp(started, timezone.utc).isoformat(),
         datetime.fromtimestamp(ended, timezone.utc).isoformat(), (ended - started) * 1000.0,
         prompt_tokens, completion_tokens, cost_usd if cost_usd is not None else 0.0,
         cost_source or "deterministic: no model call", json.dumps(tools), evidence_read,
         None if schema_valid is None else int(schema_valid),
         "not_applicable", policy_status, int(fallback), error))


# =================================================================== context
def evidence_window(ops: Any, incident: dict[str, Any]) -> dict[str, Any] | None:
    """The frozen evidence window, or None while a detector-fired incident is still
    gathering post-detection telemetry. A manual report looks back from the report; a
    detector fires AT onset, so its window reaches forward `post_trigger_s` (measured: a
    backward-only window put a +20-point humidity fault at +1.3 on average)."""
    if incident["source"] == "manual":
        return {"start_ts": _shift(incident["trigger_ts"], -float(C["evidence_window_s"])),
                "end_ts": incident["trigger_ts"], "end_seq": int(incident["trigger_seq"]),
                "basis": f"{int(C['evidence_window_s'])} simulated s before the report"}
    want = _shift(incident["trigger_ts"], float(C["post_trigger_s"]))
    # The FIRST event at or past trigger + post_trigger_s -- not the run's latest, which
    # would stretch the window over everything since (the window must not depend on how
    # late the investigation happened to start).
    end = db.query_one(ops, "SELECT seq, payload FROM simulation_events WHERE run_id=? AND seq>? "
                            "AND ts>=? ORDER BY seq LIMIT 1",
                       (incident["sim_run_id"], int(incident["trigger_seq"]), want))
    if end is None:
        run = db.query_one(ops, "SELECT status FROM simulation_runs WHERE run_id=?",
                           (incident["sim_run_id"],))
        if (run or {}).get("status") in ("running", "paused"):
            return None
        end = db.query_one(ops, "SELECT seq, payload FROM simulation_events WHERE run_id=? "
                                "ORDER BY seq DESC LIMIT 1", (incident["sim_run_id"],))
    end_ts = (_loads(end["payload"]) or {}).get("ts") if end else None
    return {"start_ts": _shift(incident["trigger_ts"], -float(C["pre_trigger_s"])),
            "end_ts": end_ts or incident["trigger_ts"],
            "end_seq": int(end["seq"]) if end else int(incident["trigger_seq"]),
            "basis": f"{int(C['pre_trigger_s'])} s before detection to "
                     f"{int(C['post_trigger_s'])} s after it (simulated)"}


def _shift(ts: str, seconds: float) -> str:
    return (E._ts(ts) + timedelta(seconds=seconds)).isoformat()


def context(deps: Deps, incident: dict[str, Any], state: dict[str, Any]) -> E.ProbeContext:
    plant = incident["plant_id"]
    w = state.get("window") or {
        "start_ts": _shift(incident["trigger_ts"], -float(C["evidence_window_s"])),
        "end_ts": incident["trigger_ts"], "end_seq": int(incident["trigger_seq"])}
    events = E.run_events(deps.ops, incident["sim_run_id"], int(w["end_seq"]))
    span = (E._ts(w["end_ts"]) - E._ts(w["start_ts"])).total_seconds()
    window = E.in_window(events, w["end_ts"], span)
    # Probes that look "back from the trigger" (the gate's inspection window, maintenance
    # clocks) read the window's END: that is "now" for this investigation.
    incident = {**incident, "trigger_ts": w["end_ts"]}
    return E.ProbeContext(
        plant_id=plant, incident=incident, topo=E.topology(deps.main_con, plant),
        base=E.baseline(str(deps.artifacts), plant), events=events, window=window,
        ops=deps.ops, main_con=deps.main_con, rules=load_rules(deps.artifacts, plant) or {},
        defect_rate=E.defect_base_rate(deps.main_con, plant),
        candidates=[c["station_id"] for c in state.get("candidates", [])],
        model_available=plant in (deps.scorers or {}) or not deps.scorers)


# =================================================================== start / observe
FOCUS_PROBES: dict[str, dict[str, list[str]]] = {
    # A manager's "investigate further" is BOUNDED: named focus areas, each a fixed list of
    # allowlisted probes run first. Free text is kept as data (and shown to an LLM planner
    # as the manager's request), never executed.
    "more_evidence": {"throughput": ["state_mix", "neighbour_flow", "signal_health"],
                      "equipment": ["signal_health", "state_mix", "manual_checklist"],
                      "quality": ["defect_rate", "defect_trace"],
                      "environment": ["zone_climate"]},
    "supplier_lot": {"quality": ["lot_concentration"], "throughput": ["variant_mix"],
                     "equipment": ["variant_mix"], "environment": []},
}


def start(deps: Deps, incident_id: str, started_by: str, planner_mode: str = "deterministic",
          manager_request: dict[str, Any] | None = None) -> str:
    incident = get_incident(deps.ops, incident_id)
    existing = db.query_one(deps.ops, "SELECT investigation_id, status FROM investigations "
                                      "WHERE incident_id=? ORDER BY created_at DESC LIMIT 1",
                            (incident_id,))
    if existing and existing["status"] in ACTIVE:
        return existing["investigation_id"]
    inv_id = "INV-" + secrets.token_hex(4).upper()
    kind = incident["kind"]
    objective = {
        "throughput": f"identify the source of the S{incident['station_id']} throughput loss",
        "equipment": f"establish whether S{incident['station_id']} equipment is degrading",
        "quality": "trace the escaping defects to the station and window that introduced them",
        "environment": "establish whether paint-zone climate control has failed",
    }[kind]
    state = {
        "incident_id": incident_id, "objective": objective, "status": "running",
        "phase": "observe", "planner_mode": planner_mode, "observations": [],
        "hypotheses": [], "investigation_plan": None, "evidence_requests": [],
        "completed_steps": [], "current_step": None, "confidence": None,
        "uncertainty": None, "assessment": None, "decision": None, "proposed_actions": [],
        "stop_reason": None, "candidates": [], "sensor_maturity": {}, "model_health": None,
        "experiments": [], "fallbacks": [], "trace": None, "scope": {},
        "manager_request": manager_request,
        "budgets": {"steps": 0, "probe_calls": 0, "simulations": 0, "llm_tokens": 0,
                    "llm_cost_usd": 0.0, "started_at": _now(),
                    "limits": {k: C[k] for k in ("max_steps", "max_probe_calls",
                                                 "max_simulations", "timeout_s",
                                                 "max_llm_tokens", "max_llm_cost_usd")}},
    }
    db.execute(deps.ops, """INSERT INTO investigations
        (investigation_id, incident_id, created_at, updated_at, status, phase, planner_mode,
         state, version, started_by, driver_heartbeat_at)
        VALUES (?,?,?,?,'running','observe',?,?,0,?,?)""",
        (inv_id, incident_id, _now(), _now(), planner_mode, json.dumps(state), started_by,
         _now()))
    _set_incident_status(deps.ops, incident_id, "investigating")
    _audit(deps.ops, started_by, "investigation_started", "investigation", inv_id,
           {"incident_id": incident_id, "planner_mode": planner_mode})
    return inv_id


def _observe(deps: Deps, inv: dict[str, Any], incident: dict[str, Any], state: dict[str, Any]
             ) -> None:
    t0 = time.time()
    ctx = context(deps, incident, state)
    sid = incident.get("station_id")
    items: list[E.Evidence] = []
    symptom = incident.get("symptom") or {}
    if incident["source"] in ("watch", "quality_detector"):
        items.append(E.Evidence("commander", "trigger", sid, "measured",
                                f"{incident['source'].replace('_', ' ')}: {incident['title']}",
                                symptom, {"no_fault": -1.0}, incident["trigger_ts"]))
    else:
        # An operator's words are DATA: quoted, stored, shown -- never interpreted as an
        # instruction and never weighted (docs/AUTONOMY_AND_SAFETY.md, prompt injection).
        items.append(E.Evidence("commander", "operator_report", sid, "manual",
                                "operator report received (text quoted as data)",
                                {"note": (incident.get("notes") or "")[:500]}, {},
                                incident["trigger_ts"]))
    candidates: list[dict[str, Any]] = []
    if incident["kind"] == "quality":
        candidates = E.discover_candidates(ctx)
        state["candidates"] = candidates
        ctx.candidates = [c["station_id"] for c in candidates]
        items.append(E.Evidence("quality", "trace_candidates", sid, "measured",
                                "stations where defective units' readings differ from good "
                                "units': " + (", ".join(f"S{c['station_id']} (t={c['t']})"
                                                        for c in candidates) or "none"),
                                {"candidates": candidates}, {}))
    up, down = E.neighbours(ctx.topo, sid) if sid is not None else (None, None)
    names = {t["station_id"]: t["station_name"] for t in ctx.topo}
    state["hypotheses"] = H.build(incident["kind"], sid, down, up, candidates,
                                  incident["source"] != "manual", names)
    for s in {sid, *ctx.candidates} - {None}:
        state["sensor_maturity"][str(s)] = E.maturity(ctx, s)
    state["scope"] = {"plant_id": incident["plant_id"],
                      "stations": E.scope_stations(ctx.topo, sid, int(C["station_scope_hops"]),
                                                   ctx.candidates)}
    step_id = _record_step(deps.ops, inv["investigation_id"], state, "observe", "commander",
                           summary=f"{len(state['hypotheses'])} competing hypotheses; "
                                   f"{len(ctx.window)} events in the evidence window",
                           started=t0)
    stored = _store_evidence(deps.ops, inv["investigation_id"], step_id, items)
    state["evidence"].extend(stored)
    state["observations"] = [e["evidence_id"] for e in stored]
    H.score(state["hypotheses"], state["evidence"])
    state["phase"] = "plan"
    record_agent_run(deps.ops, inv=inv, agent="commander", started=t0, tools=["observe"],
                     evidence_read=len(ctx.window))


# =================================================================== plans
class InvestigationPlan(BaseModel):
    """The ONLY shape a planner -- deterministic or LLM -- may hand the executor."""
    model_config = ConfigDict(extra="forbid")
    goal: str = Field(min_length=3, max_length=300)
    next_agent: Literal["reliability", "production", "quality", "data_quality", "memory",
                        "simulation", "decide"]
    reason: str = Field(min_length=3, max_length=600)
    required_evidence: list[str] = Field(default_factory=list, max_length=4)
    expected_information_gain: Literal["high", "medium", "low"] = "medium"
    stop_condition: str = Field(default="", max_length=300)
    hypotheses_to_test: list[str] = Field(default_factory=list, max_length=2)


def _targets(ctx: E.ProbeContext, probe: str, incident: dict[str, Any]) -> list[int | None]:
    spec = E.PROBES[probe]
    sid = incident.get("station_id")
    if spec.target == "plant":
        return [sid]
    if incident["kind"] == "quality":
        return list(ctx.candidates) if spec.target == "candidates" or probe in (
            "signal_health", "sensor_health") else [sid]
    return [sid]


def _done(state: dict[str, Any]) -> set[tuple[str, int | None]]:
    return {(r["probe"], r.get("station_id")) for r in state["evidence_requests"]
            if r["status"] in ("collected", "missing")}


def _eig(ctx: E.ProbeContext, state: dict[str, Any], probe: str, sid: int | None) -> float:
    spec = E.PROBES[probe]
    total = 0.0
    for h in state["hypotheses"]:
        key, p = h["key"], h["confidence"]
        m = spec.informs.get(key, 0.0)
        if "@" in spec.informs and key == f"process_deviation@{sid}":
            m = spec.informs["@"]
        if "*" in spec.informs and key != H.NO_FAULT:
            m = spec.informs["*"]
        total += p * (1 - p) * m
    return total * E.available(ctx, probe, sid)


def candidate_requests(ctx: E.ProbeContext, incident: dict[str, Any], state: dict[str, Any]
                       ) -> list[dict[str, Any]]:
    """Every (probe, station) still worth asking for, with its information gain."""
    done = _done(state)
    out = []
    for name, spec in E.PROBES.items():
        if incident["kind"] not in spec.kinds and not (
                incident["kind"] == "quality" and name in ("signal_health", "sensor_health")):
            continue
        for sid in _targets(ctx, name, incident):
            if (name, sid) in done:
                continue
            out.append({"probe": name, "station_id": sid, "specialist": spec.specialist,
                        "eig": round(_eig(ctx, state, name, sid), 4)})
    out.sort(key=lambda r: -r["eig"])
    return out


def _requested(ctx: E.ProbeContext, incident: dict[str, Any], state: dict[str, Any],
               reqs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The manager's requested probes not yet collected, from ONE specialist per step."""
    mr = state.get("manager_request") or {}
    wanted = [p for f in mr.get("focus") or [] for p in FOCUS_PROBES.get(f, {}).get(incident["kind"], [])]
    todo = [r for r in reqs if r["probe"] in wanted]
    if not todo:
        return []
    return [r for r in todo if r["specialist"] == todo[0]["specialist"]][:3]


def _mandatory_left(state: dict[str, Any], incident: dict[str, Any]) -> list[tuple[str, Any]]:
    done = _done(state)
    sid = incident.get("station_id")
    return [(p, sid) for p in MANDATORY if (p, sid) not in done]


def deterministic_plan(deps: Deps, incident: dict[str, Any], state: dict[str, Any]
                       ) -> dict[str, Any]:
    ctx = context(deps, incident, state)
    b, hyps = state["budgets"], state["hypotheses"]
    top, second = hyps[0], hyps[1]
    stop = f"top hypothesis >= {CONF['stop_confidence']:.0%} or information gain < {C['min_information_gain']}"
    goal = state["objective"]
    if b["steps"] >= C["max_steps"] or b["probe_calls"] >= C["max_probe_calls"]:
        return {"goal": goal, "next_agent": "decide", "reason": "investigation budget spent",
                "required_evidence": [], "expected_information_gain": "low",
                "stop_condition": stop}
    reqs = candidate_requests(ctx, incident, state)
    sid = incident.get("station_id")
    asked = _requested(ctx, incident, state, reqs)
    if asked:
        mr = state["manager_request"]
        why = mr.get("instruction") or ", ".join(f.replace("_", " ") for f in mr.get("focus") or [])
        return {"goal": goal, "next_agent": asked[0]["specialist"],
                "reason": f"manager asked for further investigation: {why}"[:600],
                "required_evidence": [f"{r['probe']}@{r['station_id']}" if r["station_id"] is not None
                                      else r["probe"] for r in asked],
                "expected_information_gain": "high", "stop_condition": stop}
    mat = state["sensor_maturity"].get(str(sid))
    mandatory = _mandatory_left(state, incident)
    # Poorly-instrumented station: establish what can be measured before trusting any of it.
    if b["steps"] == 0 and mat in ("LOW", "UNKNOWN", "MANUAL_ONLY") and mandatory:
        return {"goal": goal, "next_agent": "data_quality",
                "reason": f"sensing at S{sid} is {mat}: establish which readings can be trusted "
                          "before weighing any of them",
                "required_evidence": [f"{p}@{s}" if s is not None else p for p, s in mandatory],
                "expected_information_gain": "high", "stop_condition": stop}
    best_gain = reqs[0]["eig"] if reqs else 0.0
    if top["confidence"] < CONF["stop_confidence"] and best_gain >= C["min_information_gain"]:
        by_agent: dict[str, list[dict[str, Any]]] = {}
        for r in reqs:
            if r["eig"] >= C["min_information_gain"]:
                by_agent.setdefault(r["specialist"], []).append(r)
        agent = max(by_agent, key=lambda a: sum(r["eig"] for r in by_agent[a][:3]))
        picks = by_agent[agent][:3]
        gain = sum(r["eig"] for r in picks)
        return {"goal": goal, "next_agent": agent,
                "reason": (f"'{top['title']}' ({top['confidence']:.0%}) vs '{second['title']}' "
                           f"({second['confidence']:.0%}) is unresolved; {agent.replace('_', ' ')} "
                           f"evidence ({', '.join(r['probe'] for r in picks)}) has the highest "
                           f"expected information gain ({gain:.2f})"),
                "required_evidence": [f"{r['probe']}@{r['station_id']}" if r["station_id"] is not None
                                      else r["probe"] for r in picks],
                "expected_information_gain": "high" if gain >= 0.5 else
                                             "medium" if gain >= 0.15 else "low",
                "stop_condition": stop}
    if mandatory:
        return {"goal": goal, "next_agent": "data_quality",
                "reason": "before deciding: confirm the data and the model inputs can be trusted",
                "required_evidence": [f"{p}@{s}" if s is not None else p for p, s in mandatory],
                "expected_information_gain": "medium", "stop_condition": stop}
    tests = simulation_tests(ctx, incident, state)
    if tests:
        return {"goal": goal, "next_agent": "simulation",
                "reason": "test the leading explanations in the digital twin: "
                          + "; ".join(f"{t['hypothesis_title']} -> {t['label']}" for t in tests),
                "required_evidence": [], "expected_information_gain": "high",
                "stop_condition": stop,
                "hypotheses_to_test": [t["hypothesis"] for t in tests]}
    why = ("confidence reached the stop threshold" if top["confidence"] >= CONF["stop_confidence"]
           else "no remaining evidence would move the hypotheses")
    return {"goal": goal, "next_agent": "decide", "reason": why, "required_evidence": [],
            "expected_information_gain": "low", "stop_condition": stop}


def validate_plan(deps: Deps, incident: dict[str, Any], state: dict[str, Any],
                  raw: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Parse and check a plan against the allowlists, scope and budgets. Untrusted input:
    every name is looked up, nothing is executed on the model's say-so."""
    try:
        plan = InvestigationPlan.model_validate(raw).model_dump()
    except ValidationError as exc:
        return None, "schema: " + "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
                                            for e in exc.errors()[:3])
    for text in (plan["goal"], plan["reason"], plan["stop_condition"]):
        if any(ord(c) < 32 and c not in "\n\t" for c in text):
            return None, "control characters in plan text"
    agent = plan["next_agent"]
    b = state["budgets"]
    if agent in E.SPECIALISTS:
        if not plan["required_evidence"]:
            return None, f"a {agent} step must request evidence"
        if b["probe_calls"] + len(plan["required_evidence"]) > C["max_probe_calls"]:
            return None, "probe budget exceeded"
        done = _done(state)
        scope = set(state["scope"].get("stations") or [])
        for item in plan["required_evidence"]:
            name, _, at = item.partition("@")
            spec = E.PROBES.get(name)
            if spec is None:
                return None, f"unknown probe '{name}' (the allowlist is {sorted(E.PROBES)})"
            if spec.specialist != agent:
                return None, f"probe '{name}' belongs to {spec.specialist}, not {agent}"
            kind = incident["kind"]
            if kind not in spec.kinds and not (kind == "quality" and name in (
                    "signal_health", "sensor_health")):
                return None, f"probe '{name}' does not apply to a {kind} incident"
            sid = incident.get("station_id")
            if at:
                if not at.isdigit():
                    return None, f"station in '{item}' must be an integer id"
                sid = int(at)
                if sid not in scope:
                    return None, f"station {sid} is outside this investigation's scope {sorted(scope)}"
            if (name, sid) in done:
                return None, f"'{item}' was already collected"
    elif agent == "simulation":
        if b["simulations"] >= C["max_simulations"]:
            return None, "simulation budget spent"
        ctx = context(deps, incident, state)
        testable = {t["hypothesis"] for t in simulation_tests(ctx, incident, state)}
        wanted = plan["hypotheses_to_test"] or sorted(testable)
        bad = [h for h in wanted if h not in testable]
        if bad or not wanted:
            return None, (f"not testable in the twin: {bad or 'nothing'}; testable now: "
                          f"{sorted(testable)}")
        plan["hypotheses_to_test"] = wanted
    elif plan["required_evidence"]:
        return None, "a decide step requests no evidence"
    return plan, None


# =================================================================== collect
def _collect(deps: Deps, inv: dict[str, Any], incident: dict[str, Any], state: dict[str, Any],
             plan: dict[str, Any]) -> tuple[list[E.Evidence], list[str]]:
    ctx = context(deps, incident, state)
    items: list[E.Evidence] = []
    probes = []
    keys = [h["key"] for h in state["hypotheses"]]
    for item in plan["required_evidence"]:
        name, _, at = item.partition("@")
        sid = int(at) if at else incident.get("station_id")
        spec = E.PROBES[name]
        got = (spec.fn(ctx, sid, keys) if name == "similar_cases" else spec.fn(ctx, sid))
        if name == "model_health" and got:
            state["model_health"] = got[0].data
        if name == "defect_trace":
            for g in got:
                if g.provenance != "missing":
                    state.setdefault("traces", {})[str(g.station_id)] = g.data
        items.extend(got)
        probes.append(name)
        status = "missing" if got and all(g.provenance == "missing" for g in got) else "collected"
        state["evidence_requests"].append({"probe": name, "station_id": sid,
                                           "specialist": spec.specialist, "status": status,
                                           "requested_at_step": state["budgets"]["steps"]})
    state["budgets"]["probe_calls"] += len(plan["required_evidence"])
    return items, probes


# =================================================================== simulation
def _observed_context(ctx: E.ProbeContext, incident: dict[str, Any], state: dict[str, Any]
                      ) -> dict[str, Any] | None:
    """The physical condition the twin reproduces -- read from OBSERVED evidence, never from
    the hypothesis under test, so the test cannot assume its own conclusion."""
    sid = incident.get("station_id")
    for e in state["evidence"]:
        d = e.get("data") or {}
        if e["probe"] == "state_mix" and e.get("station_id") == sid:
            if d.get("blocked_share", 0) >= 0.15:
                return {"scenario": "buffer_congestion", "station_id": sid,
                        "severity": round(min(1.0, max(0.1, d["blocked_share"])), 2),
                        "basis": f"S{sid} blocked {d['blocked_share']:.0%} ({e['evidence_id']})"}
            if d.get("down_share", 0) >= 0.15:
                return {"scenario": "station_breakdown", "station_id": sid,
                        "severity": round(min(1.0, max(0.1, d["down_share"])), 2),
                        "basis": f"S{sid} down {d['down_share']:.0%} ({e['evidence_id']})"}
        if e["probe"] == "signal_health" and abs(d.get("deviation_pct") or 0) >= d.get("threshold_pct", 8):
            return {"scenario": "torque_degradation", "station_id": e["station_id"],
                    "severity": round(min(1.0, abs(d["deviation_pct"]) / 20.0), 2),
                    "basis": f"{d.get('signal')} {d['deviation_pct']:+.1f}% ({e['evidence_id']})"}
        if (e["probe"] == "defect_trace" and e["provenance"] == "measured"
                and d.get("t", 0) >= 3 and abs(d.get("difference_pct") or 0) >= 4):
            return {"scenario": "torque_degradation", "station_id": e["station_id"],
                    "severity": round(min(1.0, abs(d["difference_pct"]) / 20.0), 2),
                    "basis": f"defective units {d['difference_pct']:+.1f}% at S{e['station_id']} "
                             f"({e['evidence_id']})"}
        if e["probe"] == "zone_climate" and (d.get("excess_over_ambient") or 0) >= 5:
            return {"scenario": "high_humidity", "station_id": sid,
                    "severity": round(min(1.0, d["excess_over_ambient"] / 20.0), 2),
                    "basis": f"humidity excess {d['excess_over_ambient']:+.1f} ({e['evidence_id']})"}
    return None


def next_window(start_ts: str, min_duration: float = 0.0, skip: int = 0) -> dict[str, Any] | None:
    """The first maintenance window (simulation clock) after `start_ts` long enough for the
    job; `skip` passes over that many qualifying windows."""
    now = E._ts(start_ts)
    found = []
    for day in range(0, 40):
        date = (now + timedelta(days=day)).date()
        for w in CONFIG["maintenance_windows"]:
            if w["weekday"] is not None and date.weekday() != w["weekday"]:
                continue
            if w["duration_min"] < min_duration:
                continue
            hh, mm = (int(x) for x in w["start"].split(":"))
            start = datetime(date.year, date.month, date.day, hh, mm)
            if start <= now:
                continue
            found.append({**w, "start": start.isoformat(),
                          "end": (start + timedelta(minutes=w["duration_min"])).isoformat(),
                          "hours_until": round((start - now).total_seconds() / 3600.0, 2)})
        if len(found) > skip + 1:
            break
    found.sort(key=lambda w: w["start"])
    return found[skip] if len(found) > skip else None


def simulation_tests(ctx: E.ProbeContext, incident: dict[str, Any], state: dict[str, Any]
                     ) -> list[dict[str, Any]]:
    """Experiments that would DISCRIMINATE between the leading hypotheses. Nothing when the
    data cannot be trusted, when 'no fault' leads, or when the budget is spent."""
    if state["budgets"]["simulations"] >= C["max_simulations"]:
        return []
    if (state.get("model_health") or {}).get("status") == "degraded":
        return []
    hyps = state["hypotheses"]
    if hyps[0]["key"] in (H.NO_FAULT, "sensor_fault"):
        return []
    tested = {x["hypothesis"] for x in state["experiments"]}
    context_ = _observed_context(ctx, incident, state)
    sid = incident.get("station_id")
    up, down = E.neighbours(ctx.topo, sid) if sid is not None else (None, None)
    out = []
    for h in hyps[:2]:
        if h["confidence"] < 0.15 or h["key"] in tested:
            continue
        key, test = h["key"], None
        scenario = (context_ or {}).get("scenario")
        # Judge a throughput incident on the SYMPTOM the station shows: blocking when the
        # reproduced condition is congestion, downtime for a breakdown. Line output alone
        # cannot separate causes when the line's constraint is elsewhere (measured: +0.00
        # UPH on every seed of scenario A).
        objective = {"buffer_congestion": "blocking",
                     "station_breakdown": "downtime"}.get(scenario, "throughput")
        if key == "downstream_congestion" and down is not None:
            if scenario in MITIGATIONS_FOR["increase_feed_buffer"]:
                test = (sid, [{"type": "mitigation", "params": {"action_type": "increase_feed_buffer"}}],
                        objective, f"relieve the S{sid}->S{down} transfer (modelled conveyor fix)")
            else:
                test = (down, [{"type": "feed_buffer_delta", "params": {"delta": 2}}], objective,
                        f"+2 buffer slots between S{sid} and S{down}")
        elif key == "local_degradation" and sid is not None:
            item = ({"type": "mitigation", "params": {"action_type": "maintenance_ticket"}}
                    if scenario in MITIGATIONS_FOR["maintenance_ticket"]
                    else {"type": "cycle_time_pct", "params": {"pct": -8.0}})
            test = (sid, [item], objective, f"restore S{sid} ({item['type']})")
        elif key == "upstream_starvation" and up is not None:
            test = (sid, [{"type": "feed_buffer_delta", "params": {"delta": 2}}], objective,
                    f"+2 buffer slots feeding S{sid}")
        elif key.startswith("process_deviation@"):
            k = int(key.split("@")[1])
            if ctx.station(k).get("sensor_tier") != "manual" and (context_ or {}).get("scenario") == "torque_degradation":
                w = next_window(incident["trigger_ts"], 60)
                delay = int(min(240, (w or {}).get("hours_until", 4) * 60))
                test = (k, [{"type": "maintenance", "params": {"delay_min": 0}},
                            {"type": "maintenance", "params": {"delay_min": delay}}],
                        "quality", f"repair S{k} now vs in {delay} min")
        elif key == "environment_control" and context_:
            paint = sorted(int(s) for s in (ctx.rules.get("paint_env") or {}).get("stations", []))
            if paint:
                test = (paint[0], [{"type": "mitigation", "params": {"action_type": "hvac_setpoint"}}],
                        "quality", "restore the zone humidity set-point")
        if test is None:
            continue
        station, items, objective, label = test
        horizon = 3600.0
        if objective == "quality":
            horizon = min(14400.0, 3600.0 + max(i["params"].get("delay_min", 0) for i in items) * 60)
        out.append({"hypothesis": key, "hypothesis_title": h["title"], "station_id": station,
                    "interventions": items, "objective": objective, "label": label,
                    "context": context_, "horizon_s": horizon})
    return out


def _simulate(deps: Deps, inv: dict[str, Any], incident: dict[str, Any], state: dict[str, Any],
              plan: dict[str, Any]) -> list[str]:
    from serve.routers.experiments import ExperimentRequest, create_experiment_for
    ctx = context(deps, incident, state)
    started = []
    for t in simulation_tests(ctx, incident, state):
        if t["hypothesis"] not in plan["hypotheses_to_test"]:
            continue
        if state["budgets"]["simulations"] >= C["max_simulations"]:
            break
        ctx_ = t["context"]
        req = ExperimentRequest(
            plant_id=incident["plant_id"], station_id=t["station_id"],
            objective=t["objective"], interventions=t["interventions"], profile="smoke",
            horizon_s=t["horizon_s"],
            incident=({"scenario": ctx_["scenario"], "severity": ctx_["severity"],
                       "station_id": ctx_["station_id"]} if ctx_ else None),
            origin_run_id=inv["investigation_id"])
        entry = {**t, "status": "queued", "experiment_id": None}
        try:
            view = create_experiment_for(deps.ops, deps.main_con, deps.plants, req, "system")
            entry["experiment_id"] = view["experiment_id"]
            started.append(view["experiment_id"])
        except Exception as exc:  # noqa: BLE001 - a refused experiment is evidence, not a crash
            entry.update(status="failed", error=getattr(exc, "detail", str(exc)))
        state["experiments"].append(entry)
        state["budgets"]["simulations"] += 1
    return started


def _experiment_view(ops: Any, experiment_id: str) -> dict[str, Any]:
    from serve.routers.experiments import _view
    return _view(ops, experiment_id)


def _ingest_simulations(deps: Deps, inv: dict[str, Any], state: dict[str, Any]) -> bool:
    """True when every experiment has finished and its evidence is stored."""
    views = {}
    for x in state["experiments"]:
        if x.get("ingested") or not x.get("experiment_id"):
            continue
        v = _experiment_view(deps.ops, x["experiment_id"])
        if v["status"] in ("queued", "running"):
            x["status"] = v["status"]
            return False
        views[x["experiment_id"]] = v
    t0 = time.time()
    items: list[E.Evidence] = []
    for x in state["experiments"]:
        if x.get("ingested"):
            continue
        x["ingested"] = True
        v = views.get(x.get("experiment_id") or "")
        if v is None or v["status"] != "done":
            x["status"] = (v or {}).get("status", "failed")
            items.append(E.Evidence("simulation", "twin_experiment", x["station_id"], "missing",
                                    f"twin test '{x['label']}' did not complete: "
                                    f"{(v or {}).get('error') or x.get('error')}",
                                    {"experiment_id": x.get("experiment_id")}))
            continue
        x["status"], x["verdict"] = "done", v.get("verdict")
        items.append(_experiment_evidence(x, v))
    # A null result only DISCRIMINATES when another arm recovered something: if nothing
    # tested recovers any output, the reproduced condition costs the line nothing in the
    # twin -- a finding about impact, not evidence against the diagnosis (measured on the
    # first scenario-A run: +0.00 UPH on every seed of every arm).
    recovered = any(x.get("effect") == "supports" for x in state["experiments"])
    for ev in items:
        x = next((x for x in state["experiments"]
                  if x.get("experiment_id") == ev.data.get("experiment_id")), None)
        if x and x.get("effect") == "shows no recovery for" and not recovered:
            x["effect"] = "is inconclusive for"
            ev.effects = {}
            ev.summary = ev.summary.replace("shows no recovery for", "is inconclusive for") +                 " -- no tested change moves the objective, so the twin cannot separate the causes"
    step_id = _record_step(deps.ops, inv["investigation_id"], state, "simulate", "simulation",
                           evidence_ids=[i.evidence_id for i in items],
                           summary="; ".join(i.summary for i in items)[:500], started=t0)
    state["evidence"].extend(_store_evidence(deps.ops, inv["investigation_id"], step_id, items))
    record_agent_run(deps.ops, inv=inv, agent="simulation", started=t0,
                     tools=[x["experiment_id"] for x in state["experiments"] if x.get("experiment_id")],
                     evidence_read=len(items))
    return True


def _experiment_evidence(x: dict[str, Any], v: dict[str, Any]) -> E.Evidence:
    """A finished paired experiment as evidence on the hypothesis it was designed to test.

    Rule: the intervention aligned with H recovers the objective with a paired 95% interval
    clear of zero -> +1.5; makes it worse -> -1.0; positive but uncertain -> +0.3;
    no recovery -> -0.8 (the constraint is elsewhere)."""
    base = next(s for s in v["scenarios"] if s["is_baseline"])
    x["baseline_uph"] = ((base.get("metrics") or {}).get("throughput_uph") or {}).get("mean")
    arms = [s for s in v["scenarios"] if not s["is_baseline"] and s["supported"]]
    x["arms"] = [{"label": s["label"], "vs_baseline": {
        m: (s.get("vs_baseline") or {}).get(m) for m in ("throughput_uph", "expected_defects",
                                                         "blocked_pct", "starved_pct", "down_pct")}}
        for s in arms]
    first = (arms[0].get("vs_baseline") or {}) if arms else {}
    metric = OBJECTIVE_METRIC[x["objective"]]
    d = first.get(metric) or {}
    x["objective_metric"], x["objective_delta"] = metric, d
    x["throughput_delta"] = first.get("throughput_uph")
    x["defects_delta"] = first.get("expected_defects")
    up = metric == "throughput_uph"
    better = d.get("ci_low", 0) > 0 if up else d.get("ci_high", 0) < 0
    worse = d.get("ci_high", 0) < 0 if up else d.get("ci_low", 0) > 0
    gain = d.get("mean", 0.0) if up else -d.get("mean", 0.0)
    w, verdict = ((1.5, "supports") if better else (-1.0, "contradicts") if worse else
                  (0.3, "is too uncertain to separate") if gain > 0 else
                  (-0.8, "shows no recovery for"))
    x["effect"] = verdict
    unit = {"throughput_uph": "UPH", "expected_defects": "expected defects",
            "blocked_pct": "points of blocked time", "down_pct": "points of downtime"}.get(metric, metric)
    summary = (f"twin: {x['label']} -> {d.get('mean', 0):+.2f} {unit} "
               f"(95% CI {d.get('ci_low', 0):+.2f} .. {d.get('ci_high', 0):+.2f}, "
               f"{v['seed_count']} paired seeds); {verdict} '{x['hypothesis_title']}'")
    return E.Evidence("simulation", "twin_experiment", x["station_id"], "simulated", summary,
                      {"experiment_id": x["experiment_id"], "metric": metric, "delta": d,
                       "baseline_uph": x["baseline_uph"], "context": x.get("context"),
                       "simulator_version": v.get("simulator_version")},
                      {x["hypothesis"]: w})


# =================================================================== decide
def _money(ctx: E.ProbeContext, key: str) -> tuple[float, str]:
    row = db.query_one(ctx.main_con, "SELECT value, source FROM economics WHERE plant_id=? AND key=?",
                       (ctx.plant_id, key))
    if not row:
        return 0.0, f"configs/economics.yaml has no '{key}'"
    return float(row["value"]), f"configs/economics.yaml {key}: {row['source'][:80]}"


def _cost(ctx: E.ProbeContext, action_type: str, achieved_uph: float) -> dict[str, Any]:
    spec = CATALOGUE[action_type]
    per_h, src = _money(ctx, "downtime_cost_per_hour")
    tech = CONFIG["business"]["technician_cost_per_hour"]
    downtime = spec["downtime_min"] / 60.0 * per_h
    labour = spec["technician_hours"] * float(tech["value"])
    return {"value_usd": round(downtime + labour, 2), "basis": "ESTIMATED",
            "downtime_min": spec["downtime_min"], "downtime_min_basis": "ASSUMPTION",
            "lost_units": round(spec["downtime_min"] / 60.0 * achieved_uph, 1),
            "formula": "downtime_min/60 x downtime_cost_per_hour + technician_hours x rate",
            "sources": [src, tech["source"],
                        "docs/ASSUMPTIONS.md, Enterprise action assumptions (durations)"]}


def maintenance_plan(ctx: E.ProbeContext, incident: dict[str, Any], state: dict[str, Any],
                     action_type: str, station_id: int, achieved_uph: float) -> dict[str, Any] | None:
    """Now vs the next window vs the one after, with every number sourced."""
    spec = CATALOGUE[action_type]
    if spec["downtime_min"] <= 0:
        return None
    per_h, src = _money(ctx, "downtime_cost_per_hour")
    rework, rsrc = _money(ctx, "rework_cost_per_unit")
    fail_ev = next((e for e in state["evidence"] if e["probe"] == "failure_history"
                    and e.get("station_id") == station_id), None)
    rate = (fail_ev or {}).get("data", {}).get("failure_rate_per_h")
    if rate is None:
        rate = (ctx.base["down_episodes"].get(station_id, 0) + 1) / ctx.base["span_h"]
    # Exposure per hour while the fault stands: from the twin when it was tested, else the
    # observed excess defect rate at the gate, else unknown (and said so).
    exp = next((x for x in state["experiments"] if x.get("status") == "done"
                and x.get("station_id") == station_id), None)
    defects_per_h, basis = None, "not quantified"
    if exp and exp.get("objective") == "quality" and exp.get("arms") and len(exp["arms"]) > 1:
        now_d = (exp["arms"][0]["vs_baseline"].get("expected_defects") or {}).get("mean")
        late_d = (exp["arms"][1]["vs_baseline"].get("expected_defects") or {}).get("mean")
        delay_h = max(exp["interventions"][1]["params"]["delay_min"] / 60.0, 1e-6)
        separable = any((a["vs_baseline"].get("expected_defects") or {}).get("ci_high", 0) < 0
                        for a in exp["arms"])
        # Only a twin that actually RESPONDS to the fault may price the delay; otherwise
        # the observed excess at the gate is the better (and labelled) source.
        if now_d is not None and late_d is not None and separable:
            defects_per_h, basis = max(0.0, (late_d - now_d) / delay_h), f"SIMULATED ({exp['experiment_id']})"
    if defects_per_h is None:
        dr = next((e for e in state["evidence"] if e["probe"] == "defect_rate"), None)
        if dr and dr["data"].get("inspected"):
            excess = max(0.0, dr["data"]["rate"] - dr["data"]["baseline_rate"])
            defects_per_h, basis = excess * achieved_uph, "PROJECTED (observed excess rate x UPH, linear)"
    uph_gain = None
    if exp and exp.get("objective") == "throughput" and exp.get("throughput_delta"):
        uph_gain = max(0.0, exp["throughput_delta"].get("mean") or 0.0)
    unit_value = per_h / max(achieved_uph, 1e-6)

    def option(label: str, hours: float, lost_units: float, w: dict[str, Any] | None) -> dict[str, Any]:
        p_fail = 1 - math.exp(-rate * hours)
        exposure = (defects_per_h or 0.0) * hours
        loss_units = (uph_gain or 0.0) * hours
        cost = (lost_units * unit_value + exposure * rework + loss_units * unit_value)
        return {"label": label, "window": w, "hours_until": round(hours, 2),
                "downtime_min": spec["downtime_min"] if lost_units else 0,
                "lost_units": round(lost_units, 1),
                "expected_defect_exposure": round(exposure, 2) if defects_per_h is not None else None,
                "expected_throughput_loss_units": round(loss_units, 1) if uph_gain else None,
                "failure_probability": round(p_fail, 4),
                "expected_cost_usd": round(cost, 0),
                "bases": {"lost_units": "ESTIMATED (downtime x achieved UPH)",
                          "defect_exposure": basis,
                          "failure_probability": "ESTIMATED (Poisson on the recorded breakdown rate)",
                          "expected_cost_usd": "ESTIMATED"},
                "sources": [src, rsrc]}
    ts = incident["trigger_ts"]
    w1 = next_window(ts, spec["downtime_min"])
    w2 = next_window(ts, spec["downtime_min"], skip=1)
    opts = [option("Now (unplanned stop)", 0.0, spec["downtime_min"] / 60.0 * achieved_uph, None)]
    if w1:
        opts.append(option(f"Next window: {w1['name']}", w1["hours_until"], 0.0, w1))
    if w2:
        opts.append(option(f"Defer: {w2['name']}", w2["hours_until"], 0.0, w2))
    best = min(opts, key=lambda o: o["expected_cost_usd"])
    return {"options": opts, "recommended": best["label"],
            "rule": "lowest expected cost = lost output + defect exposure x rework cost + "
                    "throughput loss until repaired", "interim": "increase_inspection"
            if best["hours_until"] > 0 and defects_per_h else None}


ACTIONS_FOR: dict[str, list[tuple[str, str]]] = {
    "downstream_congestion": [("conveyor_buffer_adjustment", "Inspect and adjust the S{S}->S{D} transfer conveyor/buffer"),
                              ("maintenance_inspection", "Inspect S{D} during the next maintenance window"),
                              ("line_rebalance", "Request a temporary workload rebalance at S{D}")],
    "local_degradation": [("maintenance_work_order", "Raise a maintenance work order for S{S}"),
                          ("maintenance_inspection", "Inspect S{S} tooling")],
    "upstream_starvation": [("maintenance_inspection", "Inspect S{U} (the station feeding S{S})")],
    "sensor_fault": [("sensor_calibration", "Check and recalibrate the sensors at S{S}")],
    "variant_mix": [("notify_supervisor", "Ask production planning to review today's variant sequence")],
    "process_deviation": [("manual_inspection", "Audit the procedure at S{S} with the shift lead")],
    "environment_control": [("hvac_correction", "Restore the paint-zone humidity set-point"),
                            ("increase_inspection", "Increase paint inspection until humidity recovers")],
    "ambient_driven": [("increase_inspection", "Increase paint inspection while ambient humidity is high")],
    "material_lot": [("quality_hold", "Hold the affected lot's units for inspection")],
}


OBJECTIVE_METRIC = {"throughput": "throughput_uph", "quality": "expected_defects",
                    "blocking": "blocked_pct", "downtime": "down_pct"}


def _measures(effect: str, item: dict[str, Any]) -> bool:
    """Whether a twin intervention measured the same change as an option's twin effect --
    only then is the experiment's interval that option's expected impact."""
    kind = item["type"]
    if kind == "mitigation":
        return item["params"].get("action_type") == effect
    return {"feed_buffer_delta": "increase_feed_buffer", "maintenance": "maintenance_ticket",
            "cycle_time_pct": "maintenance_ticket"}.get(kind) == effect


def _fmt(t: str, s: Any, d: Any, u: Any) -> str:
    return t.replace("{S}", str(s)).replace("{D}", str(d)).replace("{U}", str(u))


def build_options(deps: Deps, ctx: E.ProbeContext, incident: dict[str, Any],
                  state: dict[str, Any], decision: str) -> list[dict[str, Any]]:
    a = state["assessment"]
    top = state["hypotheses"][0]
    key = top["key"]
    sid = top.get("station_id") or incident.get("station_id")
    if key.startswith("process_deviation@"):
        sid = int(key.split("@")[1])
    up, down = E.neighbours(ctx.topo, sid) if sid is not None else (None, None)
    uph = float(deps.plants[incident["plant_id"]].get("achieved_uph") or 50.0)
    items: list[tuple[str, str, int | None]] = []
    if decision == "recommend":
        base = key.split("@")[0]
        for at, title in ACTIONS_FOR.get(base, []):
            target = down if "{D}" in title and at != "conveyor_buffer_adjustment" else (
                up if "{U}" in title else sid)
            items.append((at, _fmt(title, sid, down, up), target))
        if base == "process_deviation" and incident["kind"] == "quality":
            trace = (state.get("traces") or {}).get(str(sid)) or {}
            if trace.get("potentially_affected_units"):
                items.insert(0, ("quality_hold", f"Hold {trace['potentially_affected_count']} "
                                 f"in-flight unit(s) that passed S{sid} in the affected window", sid))
            if ctx.station(sid).get("sensor_tier") != "manual":
                items.insert(1, ("maintenance_work_order", f"Repair S{sid} fastening tool", sid))
    elif decision == "withheld":
        target = sid if sid is not None else incident.get("station_id")
        items.append(("manual_inspection", f"Manual inspection at S{target}: the AI recommendation "
                                           "is withheld until it is confirmed", target))
        if a["assessment"] == "SENSOR_UNRELIABLE":
            items.append(("sensor_calibration", f"Restore the sensors at S{target}", target))
    items.append(("notify_supervisor", f"Notify the shift supervisors: {a['root_cause_title']} "
                                       f"({a['assessment']}, {a['confidence']:.0%})", incident.get("station_id")))
    items.append(("continue_monitoring", "Continue monitoring; no intervention", incident.get("station_id")))
    options = []
    seen = set()
    for n, (at, title, target) in enumerate(items, start=1):
        if at in PROHIBITED or at not in CATALOGUE or (at, target) in seen:
            continue
        seen.add((at, target))
        spec = CATALOGUE[at]
        exp = next((x for x in state["experiments"] if x.get("status") == "done"
                    and x["hypothesis"] == key and spec["twin_effect"] != "none"
                    and _measures(spec["twin_effect"], x["interventions"][0])), None)
        impact = None
        if exp:
            pick = lambda d: {k: (d or {}).get(k) for k in ("mean", "ci_low", "ci_high")}  # noqa: E731
            impact = {"basis": "SIMULATED", "experiment_id": exp["experiment_id"],
                      "tested_as": exp["label"], "station_id": exp["station_id"],
                      "objective_metric": exp.get("objective_metric"),
                      "objective_delta": pick(exp.get("objective_delta")),
                      "improves": exp.get("effect") == "supports",
                      "throughput_uph": pick(exp.get("throughput_delta")),
                      "baseline_uph": exp.get("baseline_uph")}
            if exp["objective"] == "quality":
                impact["expected_defects"] = pick(exp.get("defects_delta"))
        params: dict[str, Any] = {"description": title}
        if at == "quality_hold":
            trace = (state.get("traces") or {}).get(str(target)) or {}
            params["units"] = trace.get("potentially_affected_units") or []
            if not params["units"]:
                continue
        if at == "notify_supervisor":
            params["channel"] = "shift-supervisors"
        plan = maintenance_plan(ctx, incident, state, at, target, uph) if target is not None else None
        if plan:
            params["schedule"] = next((o["window"] for o in plan["options"]
                                       if o["label"] == plan["recommended"]), None)
        cost = _cost(ctx, at, uph) if spec["adapter"] != "none" else None
        gov = governor.evaluate(at, assessment=a["assessment"], confidence=a["confidence"],
                                sensor_ok=state.get("sensor_ok", True),
                                model_status=(state.get("model_health") or {}).get("status", "healthy"),
                                simulation={"verdict": "improved" if exp.get("effect") == "supports"
                                            else exp.get("verdict")} if exp else None,
                                cost_usd=(cost or {}).get("value_usd"), role="system")
        options.append({"option_id": f"OPT-{n}", "action_type": at, "title": title,
                        "station_id": target, "parameters": params, "adapter": spec["adapter"],
                        "risk": spec["risk"], "reversible": spec["reversible"],
                        "autonomy_level_required": spec["level"], "expected_impact": impact,
                        "cost": cost, "maintenance_plan": plan, "governor": gov})
    rank = {"AUTO_EXECUTE_ALLOWED": 1, "HUMAN_APPROVAL_REQUIRED": 0, "RECOMMEND_ONLY": 3,
            "INVESTIGATION_ONLY": 4, "BLOCKED": 5}

    def order(o: dict[str, Any]) -> tuple:
        # Governor first (what can actually proceed), then a simulated gain whose interval
        # clears zero, then the root cause's own action order, notification and
        # monitoring last. Stable and deterministic.
        improves = bool((o.get("expected_impact") or {}).get("improves"))
        return (rank[o["governor"]["decision"]] if o["action_type"] not in (
            "notify_supervisor", "continue_monitoring") else 9, -improves,
                int(o["option_id"].split("-")[1]))
    options.sort(key=order)
    for i, o in enumerate(options):
        o["rank"] = i + 1
    return options


def rationale(state: dict[str, Any]) -> dict[str, Any]:
    """WHY THIS RECOMMENDATION, built only from persisted evidence rows."""
    hyps = state["hypotheses"]
    top, runner = hyps[0], hyps[1]
    ev = {e["evidence_id"]: e for e in state["evidence"]}
    lines = []
    for c in sorted(top["contributions"], key=lambda c: -c["delta"]):
        if c["delta"] > 0 and c["evidence_id"] in ev:
            lines.append({"evidence_id": c["evidence_id"], "text": ev[c["evidence_id"]]["summary"],
                          "supports": top["key"], "delta": c["delta"]})
    against = sorted(((c, h) for h in hyps[1:] for c in h["contributions"] if c["delta"] < 0),
                     key=lambda ch: ch[0]["delta"])
    seen = {line["evidence_id"] for line in lines}
    for c, h in against:
        if c["evidence_id"] in ev and c["evidence_id"] not in seen and len(lines) < 7:
            seen.add(c["evidence_id"])
            lines.append({"evidence_id": c["evidence_id"], "text": ev[c["evidence_id"]]["summary"],
                          "contradicts": h["key"], "delta": c["delta"]})
    for e in state["evidence"]:
        if e["probe"] == "twin_experiment" and e["evidence_id"] not in seen:
            lines.append({"evidence_id": e["evidence_id"], "text": e["summary"], "twin": True,
                          "delta": 0.0})
    a = state["assessment"]
    return {"points": lines[:8],
            "therefore": (f"'{top['title']}' is more consistent with the evidence than "
                          f"'{runner['title']}' ({top['confidence']:.0%} vs {runner['confidence']:.0%})."
                          if a["assessment"] == "CONFIDENT" else
                          f"ROOT CAUSE UNRESOLVED -- {a['reason']}."),
            "scoring": "posterior = softmax(ln prior + sum quality x weight); see docs/AGENT_ARCHITECTURE.md"}


def _decide(deps: Deps, inv: dict[str, Any], incident: dict[str, Any], state: dict[str, Any],
            stop_reason: str) -> None:
    t0 = time.time()
    ctx = context(deps, incident, state)
    for p, s in _mandatory_left(state, incident):   # governance input is never skipped
        got = E.PROBES[p].fn(ctx, s)
        if p == "model_health":
            state["model_health"] = got[0].data
        step_id = _record_step(deps.ops, inv["investigation_id"], state, "collect",
                               "data_quality", summary=f"governance check: {p}",
                               evidence_ids=[g.evidence_id for g in got])
        state["evidence"].extend(_store_evidence(deps.ops, inv["investigation_id"], step_id, got))
        state["evidence_requests"].append({"probe": p, "station_id": s, "specialist": "data_quality",
                                           "status": "collected", "requested_at_step": state["budgets"]["steps"]})
    H.score(state["hypotheses"], state["evidence"])
    H.finalise(state["hypotheses"])
    top = state["hypotheses"][0]
    implicated = top.get("station_id")
    if top["key"].startswith("process_deviation@"):
        implicated = int(top["key"].split("@")[1])
    implicated = implicated if implicated is not None else incident.get("station_id")
    mat = E.maturity(ctx, implicated) if implicated is not None else "UNKNOWN"
    state["sensor_maturity"][str(implicated)] = mat
    comp = E.sensor_completeness(ctx, implicated) if implicated is not None else {}
    sensor_ok = comp.get("completeness") is None or comp["completeness"] >= float(
        CONFIG["data_quality"]["sensor_unreliable_below"])
    state["sensor_ok"] = sensor_ok
    scored = [e for e in state["evidence"] if e["probe"] not in ("model_health", "operator_report",
                                                                  "trace_candidates", "similar_cases")]
    coverage = (sum(1 for e in scored if e["provenance"] != "missing") / len(scored)) if scored else 0.0
    a = H.assess(state["hypotheses"], cap=float(CONFIG["maturity_caps"][mat]), coverage=coverage,
                 model_status=(state.get("model_health") or {}).get("status", "healthy"),
                 sensor_reliable=sensor_ok)
    a["implicated_station"] = implicated
    a["sensor_maturity"] = mat
    state["assessment"] = a
    state["confidence"], state["uncertainty"] = a["confidence"], a["uncertainty"]
    if a["assessment"] == "CONFIDENT" and top["key"] == H.NO_FAULT:
        decision = "no_action"
    elif a["assessment"] == "CONFIDENT":
        decision = "recommend"
    else:
        decision = "withheld"
    state["decision"] = decision
    options = build_options(deps, ctx, incident, state, decision)
    state["trace"] = (state.get("traces") or {}).get(str(implicated))
    rec_id = "REC-" + secrets.token_hex(4).upper()
    status = {"recommend": "proposed", "withheld": "withheld", "no_action": "no_action"}[decision]
    why = rationale(state)
    db.execute(deps.ops, """INSERT INTO recommendations
        (recommendation_id, incident_id, investigation_id, plant_id, created_at, updated_at,
         status, assessment, root_cause_key, root_cause_title, confidence, options, rationale)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (rec_id, incident["incident_id"], inv["investigation_id"], incident["plant_id"], _now(),
         _now(), status, a["assessment"], a["root_cause_key"] if decision != "withheld" else None,
         a["root_cause_title"], a["confidence"], json.dumps(options, default=str),
         json.dumps(why)))
    for o in options:
        g = o["governor"]
        db.execute(deps.ops, """INSERT INTO autonomy_decisions
            (decision_id, created_at, incident_id, recommendation_id, option_id, action_type,
             decision, required_level, granted_level, evaluated_for_role, inputs, reasons)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("AD-" + secrets.token_hex(5), _now(), incident["incident_id"], rec_id, o["option_id"],
             o["action_type"], g["decision"], g["required_level"], g["granted_level"], "system",
             json.dumps(g["inputs"]), json.dumps(g["reasons"])))
    state["proposed_actions"] = [o["option_id"] for o in options]
    state["recommendation_id"] = rec_id
    state["stop_reason"] = stop_reason
    # L4: whitelisted IT workflows run now, through the same gateway and audit trail.
    for o in options:
        if o["governor"]["decision"] == "AUTO_EXECUTE_ALLOWED":
            try:
                act = G.request_action(deps.ops, incident=incident, recommendation_id=rec_id,
                                       option=o, requested_by="commander",
                                       approved_by="policy:L4", autonomy_level="L4")
                G.execute(deps.ops, act)
                _audit(deps.ops, "auto-monitor", "action_auto_executed", "enterprise_action",
                       act["action_id"], {"action_type": o["action_type"], "policy": "L4"})
            except G.ActionRejected as exc:
                _audit(deps.ops, "auto-monitor", "action_auto_rejected", "recommendation", rec_id,
                       {"option_id": o["option_id"], "reason": str(exc)})
    station = ctx.station(incident.get("station_id"))
    case_memory.upsert_from_decision(deps.ops, incident, station, state,
                                     {"options": options})
    _set_incident_status(deps.ops, incident["incident_id"],
                         {"recommend": "awaiting_decision", "withheld": "needs_manual_check",
                          "no_action": "monitoring"}[decision])
    _record_step(deps.ops, inv["investigation_id"], state, "decide", "governor",
                 summary=f"{a['assessment']} -> {decision}: {a['reason']}", started=t0)
    record_agent_run(deps.ops, inv=inv, agent="governor", started=t0,
                     tools=[o["action_type"] for o in options], evidence_read=len(state["evidence"]),
                     policy_status=decision)
    _audit(deps.ops, "auto-monitor", "investigation_decided", "investigation",
           inv["investigation_id"], {"assessment": a["assessment"], "decision": decision,
                                     "recommendation_id": rec_id, "stop_reason": stop_reason})


# =================================================================== the state machine
def advance(deps: Deps, investigation_id: str, plan: dict[str, Any] | None = None,
            planner_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """One transition. Serialised per investigation; safe to call from the HTTP handler,
    the background advancer and the LLM driver alike."""
    with _lock(investigation_id):
        inv, state = load(deps.ops, investigation_id)
        if inv["status"] in ("completed", "failed"):
            return state
        incident = get_incident(deps.ops, inv["incident_id"])
        inv = {**inv, "incident_id": incident["incident_id"]}
        try:
            status = _transition(deps, inv, incident, state, plan, planner_meta)
        except Exception as exc:  # noqa: BLE001 - persisted as a failed investigation
            state["stop_reason"] = f"error: {type(exc).__name__}: {exc}"[:300]
            state["phase"] = "failed"
            _save(deps.ops, investigation_id, state, "failed")
            _set_incident_status(deps.ops, incident["incident_id"], "investigation_failed")
            _audit(deps.ops, "auto-monitor", "investigation_failed", "investigation",
                   investigation_id, {"error": state["stop_reason"]})
            raise
        _save(deps.ops, investigation_id, state, status)
        return state


def _timed_out(state: dict[str, Any]) -> bool:
    return _age_s(state["budgets"]["started_at"]) > float(C["timeout_s"])


def _transition(deps: Deps, inv: dict[str, Any], incident: dict[str, Any],
                state: dict[str, Any], plan: dict[str, Any] | None,
                meta: dict[str, Any] | None) -> str:
    phase = state["phase"]
    if phase == "observe":
        if not state.get("window"):
            w = evidence_window(deps.ops, incident)
            if w is None:
                state["current_step"] = {"agent": "commander", "reason": "gathering "
                                         f"{int(C['post_trigger_s'])} s of post-detection telemetry"}
                return "awaiting_telemetry"
            state["window"] = w
        _observe(deps, inv, incident, state)
        return "running"
    if _timed_out(state) and phase != "decide":
        _decide(deps, inv, incident, state, "timeout")
        state["phase"] = "completed"
        return "completed"
    if phase == "awaiting_simulation":
        if not _ingest_simulations(deps, inv, state):
            return "awaiting_simulation"
        H.score(state["hypotheses"], state["evidence"])
        state["phase"] = "plan"
        return "running"
    if phase != "plan":
        return inv["status"]
    t0 = time.time()
    source, fallback, rejection = "deterministic", False, None
    if plan is not None:
        tokens = int((meta or {}).get("prompt_tokens") or 0) + int((meta or {}).get("completion_tokens") or 0)
        state["budgets"]["llm_tokens"] += tokens
        state["budgets"]["llm_cost_usd"] += float((meta or {}).get("cost_usd") or 0.0)
        over = (state["budgets"]["llm_tokens"] > C["max_llm_tokens"]
                or state["budgets"]["llm_cost_usd"] > C["max_llm_cost_usd"])
        checked, rejection = validate_plan(deps, incident, state, plan)
        if over:
            checked, rejection = None, "LLM token/cost budget exhausted"
            state["planner_mode"] = "deterministic"
        if checked is None:
            fallback = True
            state["fallbacks"].append({"step": state["budgets"]["steps"], "reason": rejection,
                                       "raw": json.dumps(plan, default=str)[:500]})
            _record_step(deps.ops, inv["investigation_id"], state, "plan", "commander",
                         plan=plan if isinstance(plan, dict) else {"raw": str(plan)[:500]},
                         plan_source="llm", plan_valid=False, rejection=rejection,
                         summary=f"LLM plan rejected: {rejection}")
            plan = None
        else:
            plan, source = checked, "llm"
    elif state["planner_mode"] == "llm" and not _driver_stale(inv):
        return "awaiting_planner"
    if plan is None:
        plan, err = validate_plan(deps, incident, state,
                                  deterministic_plan(deps, incident, state))
        if plan is None:   # the deterministic planner must never propose an invalid plan
            raise RuntimeError(f"deterministic plan failed validation: {err}")
    state["investigation_plan"] = plan
    state["current_step"] = {"agent": plan["next_agent"], "reason": plan["reason"],
                             "required_evidence": plan["required_evidence"]}
    state["budgets"]["steps"] += 1
    agent = plan["next_agent"]
    if agent == "decide":
        _record_step(deps.ops, inv["investigation_id"], state, "plan", "commander", plan=plan,
                     plan_source=source, plan_valid=True, summary=plan["reason"], started=t0)
        _decide(deps, inv, incident, state, plan["reason"])
        state["phase"] = "completed"
        return "completed"
    if agent == "simulation":
        started = _simulate(deps, inv, incident, state, plan)
        _record_step(deps.ops, inv["investigation_id"], state, "plan", "simulation", plan=plan,
                     plan_source=source, plan_valid=True,
                     summary=f"{plan['reason']} -> experiments {', '.join(started) or 'none started'}",
                     started=t0)
        record_agent_run(deps.ops, inv=inv, agent="commander", started=t0, tools=["plan"],
                         evidence_read=0, fallback=fallback, **_llm(meta, source))
        state["phase"] = "awaiting_simulation"
        return "awaiting_simulation"
    evidence, probes = _collect(deps, inv, incident, state, plan)
    step_id = _record_step(deps.ops, inv["investigation_id"], state, "collect", agent, plan=plan,
                           plan_source=source, plan_valid=True,
                           evidence_ids=[e.evidence_id for e in evidence],
                           summary=plan["reason"], started=t0)
    state["evidence"].extend(_store_evidence(deps.ops, inv["investigation_id"], step_id, evidence))
    H.score(state["hypotheses"], state["evidence"])
    record_agent_run(deps.ops, inv=inv, agent=agent, started=t0, tools=probes,
                     evidence_read=len(evidence), fallback=fallback, **_llm(meta, source))
    return "running"


def _llm(meta: dict[str, Any] | None, source: str) -> dict[str, Any]:
    if source != "llm" or not meta:
        return {}
    return {"provider": meta.get("provider"), "model": meta.get("model"),
            "prompt_tokens": int(meta.get("prompt_tokens") or 0),
            "completion_tokens": int(meta.get("completion_tokens") or 0),
            "cost_usd": meta.get("cost_usd"), "cost_source": meta.get("cost_source")}


def _driver_stale(inv: dict[str, Any]) -> bool:
    return _age_s(inv.get("driver_heartbeat_at")) > float(C["planner_stale_s"])


def heartbeat(ops: Any, investigation_id: str) -> None:
    db.execute(ops, "UPDATE investigations SET driver_heartbeat_at=? WHERE investigation_id=?",
               (_now(), investigation_id))


def fall_back_to_deterministic(ops: Any, investigation_id: str, reason: str) -> None:
    """The LLM driver is gone or out of budget: the deterministic planner takes over."""
    with _lock(investigation_id):
        inv, state = load(ops, investigation_id)
        if state.get("planner_mode") != "llm":
            return
        state["planner_mode"] = "deterministic"
        state["fallbacks"].append({"step": state["budgets"]["steps"], "reason": reason})
        _save(ops, investigation_id, state,
              "running" if inv["status"] == "awaiting_planner" else inv["status"])


def run_to_block(deps: Deps, investigation_id: str, max_transitions: int = 40) -> dict[str, Any]:
    """Advance until the investigation completes or waits on something external (a twin
    experiment, an LLM planner). What tests and the synchronous API path use."""
    state: dict[str, Any] = {}
    for _ in range(max_transitions):
        state = advance(deps, investigation_id)
        row = db.query_one(deps.ops, "SELECT status FROM investigations WHERE investigation_id=?",
                           (investigation_id,))
        if row["status"] in ("completed", "failed", "awaiting_simulation", "awaiting_planner",
                             "awaiting_telemetry"):
            break
    return state


# =================================================================== background advancer
_ADVANCER: dict[str, Any] = {}


CLOSABLE = ("open", "needs_manual_check", "awaiting_decision", "investigation_failed",
            "monitoring", "action_approved")


def close_ended_runs(ops: Any) -> int:
    """Close incidents whose simulation run has ended before anyone acted (ADR-070).

    Only states nobody can still act on usefully: an investigation still running on a
    finished run's frozen data may complete, and resolved / executed / rejected incidents
    keep their outcome. A pending recommendation becomes `expired`, so Decisions stops
    offering it."""
    rows = db.query_all(ops, "SELECT i.incident_id, i.status FROM incidents i JOIN simulation_runs r "
                             "ON r.run_id = i.sim_run_id WHERE r.status NOT IN ('running','paused')")
    closed = 0
    for row in rows:
        if row["status"] not in CLOSABLE:
            continue
        active = db.query_one(ops, "SELECT 1 AS x FROM investigations WHERE incident_id=? AND status "
                                   "IN ('running','awaiting_telemetry','awaiting_simulation',"
                                   "'awaiting_planner')", (row["incident_id"],))
        if active:
            continue
        db.execute(ops, "UPDATE recommendations SET status='expired', updated_at=? WHERE "
                        "incident_id=? AND status IN ('proposed','withheld')",
                   (_now(), row["incident_id"]))
        _set_incident_status(ops, row["incident_id"], "closed")
        closed += 1
    return closed


def tick(deps: Deps) -> int:
    """One pass over every active investigation: one step each, then close incidents whose
    run has ended. Returns how many investigations moved."""
    try:
        close_ended_runs(deps.ops)
    except Exception as exc:  # noqa: BLE001 - housekeeping must not stop investigations
        print(f"[commander] closing ended runs failed: {exc}")
    moved = 0
    for row in db.query_all(deps.ops, "SELECT investigation_id, status, planner_mode, "
                                      "driver_heartbeat_at FROM investigations WHERE status IN "
                                      "('running','awaiting_telemetry','awaiting_simulation',"
                                      "'awaiting_planner')"):
        try:
            if row["planner_mode"] == "llm" and _driver_stale(row):
                fall_back_to_deterministic(deps.ops, row["investigation_id"],
                                           "LLM planner silent past planner_stale_s")
            elif row["planner_mode"] == "llm" and row["status"] != "awaiting_simulation":
                continue
            advance(deps, row["investigation_id"])
            moved += 1
        except Exception as exc:  # noqa: BLE001 - one bad investigation must not stop the rest
            print(f"[commander] {row['investigation_id']}: {exc}")
    return moved


def start_advancer(deps: Deps, interval_s: float) -> threading.Event:
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(interval_s):
            tick(deps)

    threading.Thread(target=loop, daemon=True, name="incident-commander").start()
    _ADVANCER["stop"] = stop
    return stop


# =================================================================== launch / twin / outcome
def default_planner_mode() -> str:
    """Deterministic unless the LLM planner is explicitly enabled AND a key is present.
    Opt-in on purpose: a free provider tier allows ~50 requests a day (CLAUDE.md §8), and
    line observability must never wait on a quota."""
    import os
    from serve.routers.triage import _credential_present
    if os.environ.get("PULSETWIN_COMMANDER_PLANNER", "deterministic") == "llm" \
            and _credential_present() and os.environ.get("PULSETWIN_JOBS_ENABLED", "1") != "0":
        return "llm"
    return "deterministic"


def launch(deps: Deps, incident_id: str, started_by: str, mode: str = "auto",
           manager_request: dict[str, Any] | None = None) -> str:
    """Start an investigation; with the LLM planner, also spawn its detached driver
    (genai/commander.py) the way triage runs are spawned -- per-run log, session token in
    the environment, never on argv. A failed spawn falls back to deterministic."""
    import os
    import subprocess
    import sys
    planner = default_planner_mode() if mode == "auto" else mode
    inv_id = start(deps, incident_id, started_by, planner_mode=planner,
                   manager_request=manager_request)
    if planner != "llm":
        return inv_id
    try:
        from serve import session_store
        from serve.routers.triage import ROOT, _jobs_dir
        jobs = _jobs_dir()
        jobs.mkdir(parents=True, exist_ok=True)
        token = session_store.issue("manager", f"commander-{inv_id}", ttl_s=float(C["timeout_s"]))["token"]
        log = open(jobs / f"{inv_id}.commander.log", "w", encoding="utf-8")  # noqa: SIM115
        subprocess.Popen(
            [sys.executable, "-m", "genai.commander", "--investigation-id", inv_id,
             "--incident-id", incident_id,
             "--api-base", os.environ.get("PULSETWIN_API_BASE", "http://127.0.0.1:7860")],
            cwd=str(ROOT), env={**os.environ, "PYTHONPATH": str(ROOT),
                                "PULSETWIN_API_TOKEN": token},
            stdout=log, stderr=subprocess.STDOUT)
        heartbeat(deps.ops, inv_id)
    except Exception as exc:  # noqa: BLE001 - the deterministic planner always works
        fall_back_to_deterministic(deps.ops, inv_id, f"LLM driver failed to start: {exc}")
    return inv_id


KIND_DETECTOR = {"throughput": "starvation", "equipment": "tool_wear",
                 "environment": "paint_env", "quality": "tool_wear"}


def apply_to_twin(deps: Deps, action: dict[str, Any], incident: dict[str, Any]) -> str | None:
    """An executed action with a modelled twin effect is applied to the RUNNING simulated
    twin -- never to equipment -- and opens the ADR-064 monitoring window, so predicted and
    observed can be compared by the same deterministic evaluator."""
    effect = CATALOGUE[action["action_type"]]["twin_effect"]
    if effect == "none" or deps.live is None or not incident.get("sim_run_id"):
        return None
    station = action.get("station_id") or incident.get("station_id")
    if station is None:
        return None
    detector = KIND_DETECTOR[incident["kind"]]
    if not deps.live.apply_mitigation(incident["sim_run_id"], action["action_id"], detector,
                                      int(station), effect):
        return None
    inv = latest_investigation(deps.ops, incident["incident_id"]) or {}
    same_station = False
    if inv:
        _, st = load(deps.ops, inv["investigation_id"])
        same_station = any(x.get("station_id") == int(station) and x.get("status") == "done"
                           for x in st.get("experiments", []))
    # A projection is compared with an observation only when the experiment measured the
    # SAME station; otherwise the outcome records "no simulated analogue" (measured: a
    # scenario-A projection at S20 was once compared with an outcome at S19).
    outcome_id = deps.live.open_outcome(
        incident["sim_run_id"],
        {"proposal_id": action["action_id"], "plant_id": incident["plant_id"],
         "station_id": int(station), "detector": detector,
         "triage_run_id": inv.get("investigation_id") if same_station else None}, effect)
    if outcome_id:
        db.execute(deps.ops, "UPDATE enterprise_actions SET outcome_id=? WHERE action_id=?",
                   (outcome_id, action["action_id"]))
        _set_incident_status(deps.ops, incident["incident_id"], "monitoring")
    return outcome_id


def on_outcome_evaluated(ops: Any, outcome_id: str) -> None:
    """Close the loop: the evaluated outcome becomes case memory and settles the incident."""
    action = db.query_one(ops, "SELECT * FROM enterprise_actions WHERE outcome_id=?", (outcome_id,))
    if action is None:
        return
    outcome = db.query_one(ops, "SELECT * FROM action_outcomes WHERE outcome_id=?", (outcome_id,))
    incident = get_incident(ops, action["incident_id"])
    recovery = None
    try:
        recovery = (E._ts(outcome["window_ends_at"]) - E._ts(incident["trigger_ts"])).total_seconds()
    except (TypeError, ValueError, KeyError):
        pass
    case_memory.record_outcome(ops, incident["incident_id"], outcome, recovery)
    _set_incident_status(ops, incident["incident_id"],
                         "resolved" if outcome["status"] in ("successful", "partially_successful")
                         else "outcome_" + outcome["status"])
    _audit(ops, "auto-monitor", "incident_outcome_recorded", "incident", incident["incident_id"],
           {"outcome_id": outcome_id, "status": outcome["status"],
            "prediction_error": outcome.get("prediction_error")})
