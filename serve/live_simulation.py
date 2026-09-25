"""Shared live-synthetic run manager.

The committed stream remains the deterministic synthetic source, but unlike `/api/stream`
one background producer owns the clock and persists every emitted event. Every browser
therefore observes the same run, alert budget, unit position, and inspection outcome.
Future MQTT/MES adapters can write the same event envelope into these tables.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from serve import db
from serve.watch import PAINT_ENV, Firing, LineWatch, load_rules


SCENARIOS = (
    "normal",
    "torque_degradation",
    "bad_material_lot",
    "high_humidity",
    "rushed_checklist",
    "sensor_dropout",
    "station_breakdown",
    "buffer_congestion",
)


# ADR-051: what an APPROVED action does to the running twin. These magnitudes are
# MODELLED ASSUMPTIONS, not measurements (docs/ASSUMPTIONS.md, "Mitigation effects"): the
# replay stream cannot say how far a real HVAC correction or a tip change would move a
# signal, so an approved action removes `factor` of the matching scenario's effect.
#   scope "station": only events at the proposal's station
#   scope "zone":    every event that carries the scenario's signal (paint humidity)
#   scope "risk":    only the scenario's contribution to calibrated risk, not the signal
# Keys mirror genai/finding.py::ACTION_TYPES; tests/test_proposals.py asserts they agree.
MITIGATIONS: dict[str, dict[str, Any]] = {
    "hvac_setpoint": {"scenarios": ("high_humidity",), "factor": 1.0, "scope": "zone"},
    "resequence_paint_queue": {"scenarios": ("high_humidity",), "factor": 1.0,
                               "scope": "risk"},
    "maintenance_ticket": {"scenarios": ("torque_degradation", "station_breakdown"),
                           "factor": 1.0, "scope": "station"},
    "shift_weld_load": {"scenarios": ("torque_degradation",), "factor": 1.0,
                        "scope": "station"},
    "increase_feed_buffer": {"scenarios": ("buffer_congestion", "station_breakdown"),
                             "factor": 0.5, "scope": "station"},
    "rebalance_cycle": {"scenarios": ("buffer_congestion",), "factor": 0.5,
                        "scope": "station"},
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _noise(seed: int, seq: int, key: str) -> float:
    """Stable [-1, 1] noise without importing random (outside serve/'s import wall)."""
    raw = hashlib.sha256(f"{seed}:{seq}:{key}".encode("utf-8")).digest()[:8]
    return (int.from_bytes(raw, "big") / ((1 << 64) - 1)) * 2.0 - 1.0


class LiveSimulationManager:
    def __init__(self, main_con: Any, ops: Any, artifacts: Path, plants: dict[str, dict]):
        self.main_con = main_con
        self.ops = ops
        self.artifacts = artifacts
        self.plants = plants
        self._lock = threading.RLock()
        self._controls: dict[str, dict[str, Any]] = {}
        self._inspection_stations = {
            plant_id: {
                int(row["station_id"])
                for row in db.query_all(
                    self.main_con,
                    "SELECT station_id FROM station_config WHERE plant_id=? AND is_inspection=TRUE",
                    (plant_id,),
                )
            }
            for plant_id in plants
        }
        self._watch_rules = {pid: load_rules(artifacts, pid) for pid in plants}
        # A process restart must never leave a run claiming to be live.
        db.execute(
            self.ops,
            "UPDATE simulation_runs SET status='stopped', ended_at=?, "
            "error='backend restarted' WHERE status IN ('running','paused') "
            "AND producer='backend'",
            (_utc_now(),),
        )

    def start(
        self,
        plant_id: str,
        seed: int,
        scenario: str,
        speed: float,
        severity: float,
        affected_station: int | None,
        affected_lot: str | None,
        max_events: int,
        incident_start_s: float = 0,
    ) -> dict[str, Any]:
        if plant_id not in self.plants:
            raise ValueError(f"unknown plant '{plant_id}'")
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario '{scenario}'")
        existing = self.active(plant_id)
        if existing and existing["status"] in ("running", "paused"):
            raise RuntimeError(f"plant already has active run {existing['run_id']}")
        path = self.artifacts / f"stream_{plant_id}.jsonl"
        if not path.exists():
            raise ValueError(f"no synthetic stream artifact for '{plant_id}'")

        station_count = int(self.plants[plant_id].get("station_count") or 1)
        station = affected_station
        if station is None:
            # Default to a station present in the first emitted event so a selected
            # incident is visible immediately during a short judging demo.
            with path.open("r", encoding="utf-8") as preview:
                first = None
                for index, line in enumerate(preview):
                    if index == abs(seed) % 50:
                        first = json.loads(line)
                        break
            if first is None:
                raise ValueError(f"synthetic stream for '{plant_id}' is empty")
            station = int(first["station_id"])
        if station < 1 or station > station_count:
            raise ValueError(f"affected_station must be between 1 and {station_count}")

        run_id = f"SIM-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(3)}"
        started = _utc_now()
        db.execute(
            self.ops,
            """INSERT INTO simulation_runs
                   (run_id, plant_id, seed, scenario, status, speed, severity,
                    affected_station, affected_lot, incident_start_s, event_count, alerts_count,
                    units_in_progress, max_events, source, producer, started_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, plant_id, seed, scenario, "running", speed, severity,
             station, affected_lot, incident_start_s, 0, 0, 0, max_events,
             "live_synthetic", "backend", started),
        )
        control = {
            "stop": threading.Event(),
            "running": threading.Event(),
            "scenario": scenario,
            "severity": severity,
            "affected_station": station,
            "affected_lot": affected_lot,
            "incident_start_s": float(incident_start_s),
            "first_event_ts": None,
            "event_seq": 0,
            # ADR-051. None when no watch_rules_<plant>.json was built: the watch is off.
            "watch": (LineWatch(self._watch_rules[plant_id])
                      if self._watch_rules.get(plant_id) else None),
            "mitigations": [],          # approved actions, read by _transform
            "applied": {},              # proposal_id -> (detector, station) awaiting recovery
        }
        control["running"].set()
        with self._lock:
            self._controls[run_id] = control
        worker = threading.Thread(
            target=self._produce,
            args=(run_id, plant_id, seed, speed, max_events, path),
            name=f"pulsetwin-{run_id}",
            daemon=True,
        )
        control["thread"] = worker
        worker.start()
        return self.get(run_id)

    def active(self, plant_id: str) -> dict[str, Any] | None:
        return db.query_one(
            self.ops,
            """SELECT * FROM simulation_runs WHERE plant_id=?
                 AND status IN ('running','paused') ORDER BY started_at DESC LIMIT 1""",
            (plant_id,),
        )

    def get(self, run_id: str) -> dict[str, Any]:
        row = db.query_one(self.ops, "SELECT * FROM simulation_runs WHERE run_id=?", (run_id,))
        if not row:
            raise KeyError(run_id)
        return row

    def pause(self, run_id: str) -> dict[str, Any]:
        control = self._control(run_id)
        control["running"].clear()
        db.execute(self.ops, "UPDATE simulation_runs SET status='paused' WHERE run_id=?", (run_id,))
        return self.get(run_id)

    def resume(self, run_id: str) -> dict[str, Any]:
        control = self._control(run_id)
        control["running"].set()
        db.execute(self.ops, "UPDATE simulation_runs SET status='running' WHERE run_id=?", (run_id,))
        return self.get(run_id)

    def stop(self, run_id: str) -> dict[str, Any]:
        run = self.get(run_id)
        with self._lock:
            control = self._controls.get(run_id)
        if control is not None:
            control["stop"].set()
            control["running"].set()
        elif run.get("producer") != "mqtt":
            raise RuntimeError("run is not owned by this backend process")
        # An MQTT run is produced at the edge, so this process holds no control for it. Its
        # publisher ends it with a terminal lifecycle message -- and a publisher that crashed
        # never sends one, leaving the run `running` forever and blocking every new run on
        # the plant (found on the prod stack, 2026-09-25). Closing it here only records the
        # stop; a publisher that is genuinely alive re-asserts `running` on its next message.
        db.execute(
            self.ops,
            "UPDATE simulation_runs SET status='stopped', ended_at=? WHERE run_id=?",
            (_utc_now(), run_id),
        )
        return self.get(run_id)

    def inject(
        self, run_id: str, scenario: str, severity: float,
        affected_station: int | None, affected_lot: str | None,
    ) -> dict[str, Any]:
        if scenario not in SCENARIOS or scenario == "normal":
            raise ValueError("incident scenario must be one of the non-normal scenarios")
        control = self._control(run_id)
        run = self.get(run_id)
        station = affected_station or control["affected_station"]
        with self._lock:
            control.update({
                "scenario": scenario,
                "severity": severity,
                "affected_station": station,
                "affected_lot": affected_lot,
                "incident_start_s": 0.0,
            })
        db.execute(
            self.ops,
            """UPDATE simulation_runs SET scenario=?, severity=?, affected_station=?,
                   affected_lot=? WHERE run_id=?""",
            (scenario, severity, station, affected_lot, run_id),
        )
        payload = {
            "run_id": run_id, "plant_id": run["plant_id"], "source": "live_synthetic",
            "event_type": "incident_injected", "scenario": scenario,
            "severity": severity, "affected_station": station,
            "affected_lot": affected_lot, "ts": run.get("simulation_time") or _utc_now(),
        }
        self._append_event(run_id, payload)
        return self.get(run_id)

    def events_after(self, run_id: str, seq: int, limit: int = 250) -> list[dict[str, Any]]:
        rows = db.query_all(
            self.ops,
            """SELECT seq, payload FROM simulation_events WHERE run_id=? AND seq>?
                 ORDER BY seq LIMIT ?""",
            (run_id, seq, limit),
        )
        result = []
        for row in rows:
            payload = row["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            payload["seq"] = row["seq"]
            result.append(payload)
        return result

    def shutdown(self) -> None:
        with self._lock:
            controls = list(self._controls.values())
        for control in controls:
            control["stop"].set()
            control["running"].set()

    def _control(self, run_id: str) -> dict[str, Any]:
        self.get(run_id)
        with self._lock:
            control = self._controls.get(run_id)
        if not control:
            raise RuntimeError("run is not owned by this backend process")
        return control

    def _produce(
        self, run_id: str, plant_id: str, seed: int, speed: float,
        max_events: int, path: Path,
    ) -> None:
        seq = 0
        alerts: set[str] = set()
        active_units: set[str] = set()
        previous_ts: str | None = None
        try:
            with path.open("r", encoding="utf-8") as stream:
                start_offset = abs(seed) % 50
                for raw_index, line in enumerate(stream):
                    if raw_index < start_offset:
                        continue
                    control = self._control(run_id)
                    if control["stop"].is_set() or seq >= max_events:
                        break
                    while not control["running"].wait(timeout=0.25):
                        if control["stop"].is_set():
                            break
                    if control["stop"].is_set():
                        break
                    record = json.loads(line)
                    if previous_ts:
                        delta = max(0.0, (
                            datetime.fromisoformat(record["ts"])
                            - datetime.fromisoformat(previous_ts)
                        ).total_seconds())
                        if control["stop"].wait(min(delta / speed, 0.5)):
                            break
                    previous_ts = record["ts"]
                    seq += 1
                    event = self._transform(record, run_id, plant_id, seed, seq, control)
                    # A unit's FIRST alert event only -- every later tick while it is
                    # still in the alert budget must not fire a second investigation.
                    is_new_alert = False
                    if event.get("alert"):
                        is_new_alert = event["unit_id"] not in alerts
                        alerts.add(event["unit_id"])
                    active_units.add(event["unit_id"])
                    event["alerts_used"] = len(alerts)
                    event["alerts_budget"] = int(self.plants[plant_id]["alert_budget"])
                    self._append_event(run_id, event)
                    self._update_current_state(run_id, plant_id, event)
                    self._watch(run_id, plant_id, event, control)
                    db.execute(
                        self.ops,
                        """UPDATE simulation_runs SET simulation_time=?, event_count=?,
                               alerts_count=?, units_in_progress=? WHERE run_id=?""",
                        (event["ts"], seq, len(alerts), len(active_units), run_id),
                    )
                    if event.get("alert"):
                        self._open_alert(run_id, plant_id, event)
                        if is_new_alert:
                            self._trigger_auto_investigation(run_id, plant_id, event)
                    if self._is_inspection(plant_id, int(event["station_id"])):
                        self._inspection_outcome(run_id, plant_id, event)
                        active_units.discard(event["unit_id"])

            status = self.get(run_id)["status"]
            if status not in ("stopped", "failed"):
                db.execute(
                    self.ops,
                    "UPDATE simulation_runs SET status='completed', ended_at=? WHERE run_id=?",
                    (_utc_now(), run_id),
                )
        except Exception as exc:
            db.execute(
                self.ops,
                "UPDATE simulation_runs SET status='failed', ended_at=?, error=? WHERE run_id=?",
                (_utc_now(), str(exc)[:500], run_id),
            )

    def _transform(
        self, record: dict[str, Any], run_id: str, plant_id: str, seed: int,
        seq: int, control: dict[str, Any],
    ) -> dict[str, Any]:
        event = dict(record)
        if control.get("first_event_ts") is None:
            control["first_event_ts"] = event["ts"]
        elapsed_s = max(0.0, (
            datetime.fromisoformat(event["ts"])
            - datetime.fromisoformat(control["first_event_ts"])
        ).total_seconds())
        incident_active = elapsed_s >= float(control.get("incident_start_s") or 0)
        visible_scenario = control["scenario"] if incident_active else "normal"
        event.update({
            "run_id": run_id,
            "plant_id": plant_id,
            "source": "live_synthetic",
            "event_type": "alert" if event.get("alert") else "station_update",
            "scenario": visible_scenario,
            "incident_active": incident_active,
        })
        event["cycle_time_s"] = round(max(
            1.0, float(event.get("cycle_time_s") or 1.0) * (1.0 + 0.015 * _noise(seed, seq, "cycle"))
        ), 3)
        scenario = visible_scenario
        severity = float(control["severity"])
        affected = int(event["station_id"]) == int(control["affected_station"])
        lot_match = not control.get("affected_lot") or event.get("lot_id") == control["affected_lot"]
        triggered = affected and lot_match
        # ADR-051: an approved mitigation removes part of the scenario's effect.
        station_id = int(event["station_id"])
        keep = 1.0 - _mitigated(control, scenario, station_id, "signal")
        keep_risk = 1.0 - _mitigated(control, scenario, station_id, "risk")
        if scenario == "torque_degradation" and triggered:
            if "torque_nm" in event:
                event["torque_nm"] = round(
                    float(event["torque_nm"]) + 8.0 * severity * keep, 3)
            self._raise_risk(event, severity * keep_risk)
        elif scenario == "bad_material_lot" and lot_match:
            self._raise_risk(event, severity * 0.8)
        elif scenario == "high_humidity" and "humidity_pct" in event:
            event["humidity_pct"] = round(
                min(100.0, float(event["humidity_pct"]) + 20.0 * severity * keep), 3)
            self._raise_risk(event, severity * 0.55 * keep_risk)
        elif scenario == "rushed_checklist" and triggered and "checklist_time_s" in event:
            event["checklist_time_s"] = round(max(1.0, float(event["checklist_time_s"]) * (1 - 0.5 * severity)), 3)
            self._raise_risk(event, severity * 0.75)
        elif scenario == "sensor_dropout" and triggered:
            for key in list(event):
                if key in ("torque_nm", "vibration_mm_s", "temperature_c", "humidity_pct",
                           "film_thickness_um", "weld_current_a"):
                    event.pop(key, None)
            event["sensor_status"] = "dropout"
        elif scenario in ("station_breakdown", "buffer_congestion") and triggered:
            # A partial mitigation relieves the stall on that share of events, picked by
            # stable noise so a replay of the same seed stays reproducible. With nothing
            # applied `keep` is 1.0 and every triggered event stalls, exactly as before.
            if (_noise(seed, seq, "mitigate") + 1.0) / 2.0 < keep:
                down = scenario == "station_breakdown"
                event["station_state"] = "down" if down else "blocked"
                event["cycle_time_s"] = round(
                    event["cycle_time_s"] * (1 + (1.0 if down else 0.6) * severity), 3)
        return event

    # ------------------------------------------------------------ ADR-051: the watch

    def _watch(self, run_id: str, plant_id: str, event: dict[str, Any],
               control: dict[str, Any]) -> None:
        watch: LineWatch | None = control.get("watch")
        if watch is None:
            return
        for firing in watch.observe(event):
            self._on_firing(run_id, plant_id, firing)
        for proposal_id, (detector, station) in list(control["applied"].items()):
            if not watch.is_clear(detector, station):
                continue
            control["applied"].pop(proposal_id, None)
            db.execute(
                self.ops,
                "UPDATE action_proposals SET status='EFFECTIVE', effective_at=? "
                "WHERE proposal_id=?",
                (event["ts"], proposal_id),
            )
            _audit(self.ops, "proposal_effective", proposal_id,
                   {"detector": detector, "station_id": station})
            print(f"[watch] {run_id} {proposal_id} EFFECTIVE: {detector} at S{station} "
                  f"back within threshold after mitigation")

    def _on_firing(self, run_id: str, plant_id: str, firing: Firing) -> None:
        """Record the firing as a proposal row and start the matching agent.

        Same path as _trigger_auto_investigation: `start_triage_run`, best-effort, never
        allowed to take down the producer thread. One open proposal per detector and
        station, and at most `max_open_investigations` per run -- each agent run spends
        7-12 provider requests, and a free tier allows a fixed number per day.
        """
        open_rows = db.query_all(
            self.ops,
            "SELECT detector, station_id FROM action_proposals WHERE sim_run_id=? "
            "AND status IN ('OPEN','APPROVED','APPLIED')",
            (run_id,),
        )
        # paint_env is ZONE-wide: whichever paint station tripped it, it is one condition,
        # so it must not open a second proposal just because another station reported it.
        if any(r["detector"] == firing.detector
               and (firing.detector == PAINT_ENV or int(r["station_id"]) == firing.station_id)
               for r in open_rows):
            return
        cap = int((self._watch_rules.get(plant_id) or {}).get("max_open_investigations", 1))
        if len(open_rows) >= cap:
            print(f"[watch] {run_id} {firing.detector} at S{firing.station_id} fired, but "
                  f"{len(open_rows)} proposals are already open (cap {cap}); not investigated")
            return

        proposal_id = f"P-{secrets.token_hex(5)}"
        db.execute(
            self.ops,
            """INSERT INTO action_proposals (proposal_id, sim_run_id, plant_id, detector,
                   station_id, fired_at, trigger_evidence, status)
               VALUES (?,?,?,?,?,?,?,?)""",
            (proposal_id, run_id, plant_id, firing.detector, firing.station_id, firing.ts,
             json.dumps(firing.evidence, separators=(",", ":")), "OPEN"),
        )
        print(f"[watch] {run_id} FIRED {firing.detector} at S{firing.station_id} -> "
              f"{proposal_id} evidence={json.dumps(firing.evidence)}")
        if os.environ.get("PULSETWIN_JOBS_ENABLED", "1") == "0":
            return
        try:
            from serve.routers.triage import _credential_present, start_triage_run
            name = firing.evidence.get("station_name") or f"S{firing.station_id}"
            result = start_triage_run(
                self.ops,
                plant_id=plant_id,
                question=(f"Watch: {firing.detector.replace('_', ' ')} fired at station "
                          f"{firing.station_id} ({name})."),
                intent=firing.detector,
                role="auto-monitor",
                acting_role="system",
                # Resolved here, as the router does: "auto" stored on the row would tell
                # the proposals inbox nothing about whether a model was involved.
                mode="agent" if _credential_present() else "deterministic",
                api_base=os.environ.get("PULSETWIN_API_BASE", "http://127.0.0.1:7860"),
                station_id=firing.station_id,
            )
            db.execute(self.ops,
                       "UPDATE action_proposals SET triage_run_id=? WHERE proposal_id=?",
                       (result["run_id"], proposal_id))
            print(f"[watch] {proposal_id} -> {firing.detector} agent run {result['run_id']}")
        except Exception as exc:  # noqa: BLE001 - best-effort, see docstring
            print(f"[watch] agent trigger failed for {proposal_id}: {exc}")

    def apply_mitigation(self, run_id: str, proposal_id: str, detector: str,
                         station_id: int, action_type: str) -> bool:
        """Apply an APPROVED action to the running twin -- and only to the twin.

        False when this process owns no live run to apply it to: the human decision
        still stands and is audited, it simply is not simulated.
        """
        effect = MITIGATIONS.get(action_type)
        if effect is None:
            return False
        try:
            control = self._control(run_id)
        except (KeyError, RuntimeError):
            return False
        if self.get(run_id)["status"] not in ("running", "paused"):
            return False
        with self._lock:
            control["mitigations"].append({**effect, "station": int(station_id)})
            control["applied"][proposal_id] = (detector, int(station_id))
        if control.get("watch") is not None:
            control["watch"].mark_applied(detector, int(station_id))
        return True

    def _raise_risk(self, event: dict[str, Any], severity: float) -> None:
        threshold = float(self.plants[event["plant_id"]]["budget_threshold"])
        event["calibrated_risk"] = round(min(0.99, float(event.get("calibrated_risk") or 0) + 0.5 * severity), 4)
        if event["calibrated_risk"] >= threshold:
            event["alert"] = 1
            event["event_type"] = "alert"

    def _store_event(self, run_id: str, seq: int, event: dict[str, Any]) -> None:
        db.execute(
            self.ops,
            """INSERT INTO simulation_events
                   (run_id, seq, ts, event_type, unit_id, station_id, payload)
               VALUES (?,?,?,?,?,?,?)""",
            (run_id, seq, event.get("ts") or _utc_now(), event["event_type"],
             event.get("unit_id"), event.get("station_id"),
             json.dumps(event, separators=(",", ":"))),
        )

    def _append_event(self, run_id: str, event: dict[str, Any]) -> int:
        """Allocate a monotonic sequence shared by producer and incident injection."""
        control = self._control(run_id)
        with self._lock:
            control["event_seq"] += 1
            seq = int(control["event_seq"])
            self._store_event(run_id, seq, event)
        return seq

    def _update_current_state(self, run_id: str, plant_id: str, event: dict[str, Any]) -> None:
        db.execute(
            self.ops,
            """INSERT INTO unit_current_state
                   (run_id,plant_id,unit_id,current_station,status,risk,variant,lot_id,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(run_id,unit_id) DO UPDATE SET
                   current_station=excluded.current_station,status=excluded.status,
                   risk=excluded.risk,variant=excluded.variant,lot_id=excluded.lot_id,
                   updated_at=excluded.updated_at""",
            (run_id, plant_id, event["unit_id"], event["station_id"],
             event["station_state"], event.get("calibrated_risk"), event.get("variant"),
             event.get("lot_id"), event["ts"]),
        )
        db.execute(
            self.ops,
            """INSERT INTO station_current_state
                   (run_id,plant_id,station_id,state,unit_id,cycle_time_s,risk,updated_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(run_id,station_id) DO UPDATE SET
                   state=excluded.state,unit_id=excluded.unit_id,
                   cycle_time_s=excluded.cycle_time_s,risk=excluded.risk,
                   updated_at=excluded.updated_at""",
            (run_id, plant_id, event["station_id"], event["station_state"], event["unit_id"],
             event.get("cycle_time_s"), event.get("calibrated_risk"), event["ts"]),
        )

    def _open_alert(self, run_id: str, plant_id: str, event: dict[str, Any]) -> None:
        alert_id = f"{run_id}:{event['unit_id']}"
        db.execute(
            self.ops,
            """INSERT INTO active_alerts
                   (alert_id,run_id,plant_id,unit_id,station_id,risk,status,opened_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(alert_id) DO UPDATE SET station_id=excluded.station_id,
                   risk=excluded.risk""",
            (alert_id, run_id, plant_id, event["unit_id"], event["station_id"],
             event["calibrated_risk"], "OPEN", event["ts"]),
        )

    def _trigger_auto_investigation(
        self, run_id: str, plant_id: str, event: dict[str, Any],
    ) -> None:
        """Fire an unattended 'incident' investigation the moment a NEW alert opens, so a
        supervisor who has not even looked yet already has a verified finding waiting.

        Calls the exact same `start_triage_run` the manual "Investigate this incident"
        button calls (serve/routers/triage.py) -- same DB rows, same heartbeat, same
        subprocess, same deterministic verifier. There is no separate, weaker path for
        the case nobody asked yet.

        Best-effort and silent on failure: a triage/import problem must never take down
        the simulation producer thread, which is why this is a plain try/except around a
        local import rather than a module-level one.
        """
        if os.environ.get("PULSETWIN_JOBS_ENABLED", "1") == "0":
            return
        try:
            from serve.routers.triage import start_triage_run
            api_base = os.environ.get("PULSETWIN_API_BASE", "http://127.0.0.1:7860")
            result = start_triage_run(
                self.ops,
                plant_id=plant_id,
                question=(
                    f"Auto-triggered: a new alert opened on unit {event['unit_id']} "
                    f"at station {event['station_id']}."
                ),
                intent="incident",
                role="auto-monitor",
                acting_role="system",
                mode="auto",
                api_base=api_base,
                station_id=int(event["station_id"]),
            )
            db.execute(
                self.ops,
                "UPDATE active_alerts SET triage_run_id=? WHERE alert_id=?",
                (result["run_id"], f"{run_id}:{event['unit_id']}"),
            )
        except Exception as exc:  # noqa: BLE001 - best-effort, see docstring
            print(f"[live_simulation] auto-investigation trigger failed: {exc}")

    def _is_inspection(self, plant_id: str, station_id: int) -> bool:
        return station_id in self._inspection_stations.get(plant_id, set())

    def _inspection_outcome(
        self, run_id: str, plant_id: str, event: dict[str, Any],
    ) -> None:
        unit = db.query_one(
            self.main_con,
            "SELECT actual_defect FROM units WHERE plant_id=? AND unit_id=?",
            (plant_id, event["unit_id"]),
        )
        if not unit:
            return
        outcome = int(unit.get("actual_defect") or 0)
        now = event["ts"]
        db.execute(
            self.ops,
            """UPDATE unit_current_state SET inspected=1, actual_defect=?
                 WHERE run_id=? AND unit_id=?""",
            (outcome, run_id, event["unit_id"]),
        )
        db.execute(
            self.ops,
            """UPDATE active_alerts SET status='VALIDATED', outcome=?, outcome_at=?, validated_at=?
                 WHERE run_id=? AND unit_id=?""",
            (outcome, now, now, run_id, event["unit_id"]),
        )
        decisions = db.query_all(
            self.ops,
            """SELECT id, selected_action FROM containment_decisions
                 WHERE run_id=? AND target_unit_id=? AND status='ACTIONED'""",
            (run_id, event["unit_id"]),
        )
        for decision in decisions:
            acted = decision["selected_action"] in ("HOLD", "INSPECT")
            result = (
                "correct_intervention" if acted and outcome else
                "unnecessary_intervention" if acted else
                "missed_defect" if outcome else "correct_monitor"
            )
            db.execute(
                self.ops,
                """UPDATE containment_decisions SET status='VALIDATED', actual_outcome=?,
                       outcome_at=?, validation_result=? WHERE id=?""",
                (outcome, now, result, decision["id"]),
            )
        outcome_event = {
            "run_id": run_id, "plant_id": plant_id, "source": "live_synthetic",
            "event_type": "inspection_outcome", "ts": now,
            "unit_id": event["unit_id"], "station_id": event["station_id"],
            "actual_defect": outcome,
        }
        self._append_event(run_id, outcome_event)


def _mitigated(control: dict[str, Any], scenario: str, station_id: int, part: str) -> float:
    """The share of `scenario`'s effect removed by approved mitigations, 0..1.

    `part` "signal" ignores risk-only mitigations; "risk" counts every one, since
    removing a signal's excess also removes the risk that excess was adding.
    """
    best = 0.0
    for m in control.get("mitigations") or ():
        if scenario not in m["scenarios"]:
            continue
        if m["scope"] == "risk" and part != "risk":
            continue
        if m["scope"] == "station" and m["station"] != station_id:
            continue
        best = max(best, float(m["factor"]))
    return min(best, 1.0)


def _audit(ops: Any, action: str, resource_id: str, payload: dict[str, Any]) -> None:
    """An audit row for something the SYSTEM did, with no request to attribute it to."""
    try:
        db.execute(
            ops,
            """INSERT INTO audit_log (user_role, action, resource_type, resource_id, payload)
               VALUES (?,?,?,?,?)""",
            ("auto-monitor", action, "action_proposal", resource_id,
             json.dumps(payload, default=str)),
        )
    except Exception as exc:  # noqa: BLE001 - auditing must never break the producer
        print(f"[watch] audit failed for {resource_id}: {exc}")
