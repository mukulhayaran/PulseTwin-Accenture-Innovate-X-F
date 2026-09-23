"""Project MQTT messages into the shared live-dashboard tables.

The API reads these same PostgreSQL tables through ``LiveSimulationManager``. This keeps
MQTT transport concerns in ingestion while making browser SSE independent of which
producer supplied the events.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


TERMINAL_STATUSES = {"stopped", "completed", "failed"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp(value: Any) -> str:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat()
    return str(value or _utc_now())


class LiveProjection:
    """Small PostgreSQL projection used by the single-threaded MQTT subscriber."""

    def __init__(self, con: Any):
        self.con = con
        rows = con.execute(
            "SELECT plant_id, station_id FROM station_config WHERE is_inspection=TRUE"
        ).fetchall()
        self.inspection_stations = {(str(row[0]), int(row[1])) for row in rows}
        meta = con.execute("SELECT plant_id, alert_budget FROM plant_meta").fetchall()
        self.alert_budgets = {str(row[0]): int(row[1] or 0) for row in meta}

    def lifecycle(self, plant_id: str, body: dict[str, Any]) -> None:
        run_id = str(body["run_id"])
        status = str(body.get("status") or "running")
        started_at = _timestamp(body.get("started_at"))
        ended_at = _timestamp(body.get("ended_at")) if status in TERMINAL_STATUSES else None
        self.con.execute(
            """INSERT INTO simulation_runs
                   (run_id,plant_id,seed,scenario,status,speed,severity,event_count,
                    alerts_count,units_in_progress,max_events,source,producer,started_at,
                    ended_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,0,0,0,%s,%s,'mqtt',%s,%s)
               ON CONFLICT (run_id) DO UPDATE SET
                   status=EXCLUDED.status, ended_at=EXCLUDED.ended_at""",
            (run_id, plant_id, int(body.get("seed") or 0), body.get("scenario") or "normal",
             status, float(body.get("speed") or 1), float(body.get("severity") or 0),
             int(body.get("max_events") or 1000000), body.get("source") or "real",
             started_at, ended_at),
        )

    def station_state(self, plant_id: str, station_id: int, body: dict[str, Any]) -> None:
        run_id = self._ensure_run(plant_id, body)
        unit_id = body.get("unit_id")
        if not unit_id:
            return
        ts = _timestamp(body.get("ts"))
        source = str(body.get("source") or "real")
        risk = body.get("calibrated_risk")
        event_type = "alert" if body.get("alert") else "station_update"
        event = dict(body)
        event.update({
            "run_id": run_id, "plant_id": plant_id, "station_id": station_id,
            "source": source, "event_type": event_type, "ts": ts,
        })
        if body.get("alert"):
            self._open_alert(run_id, plant_id, str(unit_id), station_id, risk, ts)
        event["alerts_used"] = int(self.con.execute(
            "SELECT COUNT(*) FROM active_alerts WHERE run_id=%s", (run_id,)
        ).fetchone()[0])
        event["alerts_budget"] = self.alert_budgets.get(plant_id, 0)
        seq = self._append_event(run_id, event)
        state = str(body.get("station_state") or "working")
        self.con.execute(
            """INSERT INTO unit_current_state
                   (run_id,plant_id,unit_id,current_station,status,risk,variant,lot_id,updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (run_id,unit_id) DO UPDATE SET
                   current_station=EXCLUDED.current_station,status=EXCLUDED.status,
                   risk=EXCLUDED.risk,variant=COALESCE(EXCLUDED.variant,unit_current_state.variant),
                   lot_id=COALESCE(EXCLUDED.lot_id,unit_current_state.lot_id),
                   updated_at=EXCLUDED.updated_at""",
            (run_id, plant_id, str(unit_id), station_id, state, risk,
             body.get("variant"), body.get("lot_id"), ts),
        )
        self.con.execute(
            """INSERT INTO station_current_state
                   (run_id,plant_id,station_id,state,unit_id,cycle_time_s,risk,updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (run_id,station_id) DO UPDATE SET
                   state=EXCLUDED.state,unit_id=EXCLUDED.unit_id,
                   cycle_time_s=EXCLUDED.cycle_time_s,risk=EXCLUDED.risk,
                   updated_at=EXCLUDED.updated_at""",
            (run_id, plant_id, station_id, state, str(unit_id),
             body.get("cycle_time_s"), risk, ts),
        )
        self._refresh_counts(run_id, ts, seq)
        if (plant_id, station_id) in self.inspection_stations:
            self._inspection_outcome(run_id, plant_id, str(unit_id), station_id, ts, source)

    def prediction(self, plant_id: str, result: dict[str, Any]) -> None:
        """Replace recorded risk with the online model result when a run id is known."""
        run_id = result.get("run_id")
        unit_id = result.get("unit_id")
        risk = result.get("probability")
        if not run_id or not unit_id or risk is None:
            return
        self.con.execute(
            "UPDATE unit_current_state SET risk=%s WHERE run_id=%s AND unit_id=%s",
            (float(risk), str(run_id), str(unit_id)),
        )

    def _ensure_run(self, plant_id: str, body: dict[str, Any]) -> str:
        run_id = str(body.get("run_id") or f"LIVE-{plant_id}-{datetime.now(timezone.utc):%Y%m%d}")
        self.con.execute(
            """INSERT INTO simulation_runs
                   (run_id,plant_id,seed,scenario,status,speed,severity,event_count,
                    alerts_count,units_in_progress,max_events,source,producer,started_at)
               VALUES (%s,%s,0,'normal','running',1,0,0,0,0,1000000,%s,'mqtt',%s)
               ON CONFLICT (run_id) DO NOTHING""",
            (run_id, plant_id, body.get("source") or "real", _utc_now()),
        )
        return run_id

    def _append_event(self, run_id: str, event: dict[str, Any]) -> int:
        row = self.con.execute(
            "UPDATE simulation_runs SET event_count=event_count+1 WHERE run_id=%s RETURNING event_count",
            (run_id,),
        ).fetchone()
        seq = int(row[0])
        self.con.execute(
            """INSERT INTO simulation_events
                   (run_id,seq,ts,event_type,unit_id,station_id,payload)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (run_id, seq, event["ts"], event["event_type"], event.get("unit_id"),
             event.get("station_id"), json.dumps(event, separators=(",", ":"))),
        )
        return seq

    def _open_alert(self, run_id: str, plant_id: str, unit_id: str, station_id: int,
                    risk: Any, ts: str) -> None:
        self.con.execute(
            """INSERT INTO active_alerts
                   (alert_id,run_id,plant_id,unit_id,station_id,risk,status,opened_at)
               VALUES (%s,%s,%s,%s,%s,%s,'OPEN',%s)
               ON CONFLICT (alert_id) DO UPDATE SET
                   station_id=EXCLUDED.station_id,risk=EXCLUDED.risk""",
            (f"{run_id}:{unit_id}", run_id, plant_id, unit_id, station_id,
             float(risk or 0), ts),
        )

    def _refresh_counts(self, run_id: str, ts: str, seq: int) -> None:
        alerts = self.con.execute(
            "SELECT COUNT(*) FROM active_alerts WHERE run_id=%s", (run_id,)
        ).fetchone()[0]
        units = self.con.execute(
            "SELECT COUNT(*) FROM unit_current_state WHERE run_id=%s AND inspected=0", (run_id,)
        ).fetchone()[0]
        self.con.execute(
            """UPDATE simulation_runs SET simulation_time=%s,event_count=%s,
                   alerts_count=%s,units_in_progress=%s WHERE run_id=%s""",
            (ts, seq, int(alerts), int(units), run_id),
        )

    def _inspection_outcome(self, run_id: str, plant_id: str, unit_id: str,
                            station_id: int, ts: str, source: str) -> None:
        row = self.con.execute(
            "SELECT actual_defect FROM units WHERE plant_id=%s AND unit_id=%s",
            (plant_id, unit_id),
        ).fetchone()
        if row is None:
            return
        outcome = int(row[0] or 0)
        self.con.execute(
            """UPDATE unit_current_state SET inspected=1,actual_defect=%s
                 WHERE run_id=%s AND unit_id=%s""", (outcome, run_id, unit_id),
        )
        self.con.execute(
            """UPDATE active_alerts SET status='VALIDATED',outcome=%s,outcome_at=%s,validated_at=%s
                 WHERE run_id=%s AND unit_id=%s""", (outcome, ts, ts, run_id, unit_id),
        )
        decisions = self.con.execute(
            """SELECT id,selected_action FROM containment_decisions
                 WHERE run_id=%s AND target_unit_id=%s AND status='ACTIONED'""",
            (run_id, unit_id),
        ).fetchall()
        for decision_id, action in decisions:
            acted = action in ("HOLD", "INSPECT")
            result = (
                "correct_intervention" if acted and outcome else
                "unnecessary_intervention" if acted else
                "missed_defect" if outcome else "correct_monitor"
            )
            self.con.execute(
                """UPDATE containment_decisions SET status='VALIDATED',actual_outcome=%s,
                       outcome_at=%s,validation_result=%s WHERE id=%s""",
                (outcome, ts, result, decision_id),
            )
        event = {
            "run_id": run_id, "plant_id": plant_id, "source": source,
            "event_type": "inspection_outcome", "ts": ts, "unit_id": unit_id,
            "station_id": station_id, "actual_defect": outcome,
        }
        seq = self._append_event(run_id, event)
        self._refresh_counts(run_id, ts, seq)
