"""Enterprise Action Gateway (ADR-067): the only way an approved recommendation leaves
PulseTwin.

Every adapter implements the same four calls a production CMMS / MES / QMS integration
needs -- validate, execute, get_status, rollback -- and the gateway wraps them with the
guarantees that must not depend on an adapter being written carefully:

  * prohibited action types (PLC writes, setpoint changes, safety overrides) are refused
    before any adapter is chosen -- there is no adapter that could carry one;
  * execution is IDEMPOTENT: the key is a hash of (recommendation, option, action, params),
    `enterprise_actions.idempotency_key` is UNIQUE, and a second execute returns the first
    receipt instead of creating a second work order;
  * every transition is persisted before and after the adapter call, so a crash between
    them leaves a row in `executing` that a retry reconciles, never a silent duplicate.

The adapters here are REFERENCE adapters (`adapter_mode: reference_mock`): they validate
and shape requests exactly as the real systems' APIs would (SAP PM notification + order,
MES work instruction, QMS hold, Teams/email message) and return deterministic external
references, but they call no external system -- none is configured in this deployment.
docs/ENTERPRISE_INTEGRATION.md maps each to its production endpoint.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from typing import Any

from serve import db
from serve.agentic_config import CATALOGUE, PROHIBITED


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ActionRejected(ValueError):
    """Validation failed: nothing was sent anywhere."""


class EnterpriseActionAdapter:
    """The interface every CMMS / MES / QMS / messaging integration implements."""
    name: str
    system: str

    def validate(self, action: dict[str, Any]) -> None:
        """Raise ActionRejected when the target system would refuse this request."""
        raise NotImplementedError

    def execute(self, action: dict[str, Any]) -> dict[str, Any]:
        """Return the receipt. Deterministic reference from the idempotency key, so a
        replayed call names the same external object a real system would dedupe to."""
        ref = f"{self.system}-{int(hashlib.sha256(action['idempotency_key'].encode()).hexdigest()[:8], 16) % 10**7:07d}"
        return {"external_reference": ref, "status": "executed", "adapter_mode": "reference_mock",
                "system": self.system, "request": self.request(action)}

    def get_status(self, action: dict[str, Any]) -> dict[str, Any]:
        return {"external_reference": action.get("external_reference"),
                "status": action.get("status"), "adapter_mode": "reference_mock"}

    def rollback(self, action: dict[str, Any]) -> dict[str, Any]:
        return {"rollback_reference": f"{action.get('external_reference')}-CANCEL",
                "status": "rolled_back", "adapter_mode": "reference_mock"}

    def request(self, action: dict[str, Any]) -> dict[str, Any]:
        """The payload the real system's API would receive."""
        raise NotImplementedError


def _need_station(action: dict[str, Any]) -> None:
    if action.get("station_id") is None:
        raise ActionRejected(f"{action['action_type']} needs a station")


class CMMSAdapter(EnterpriseActionAdapter):
    """SAP PM / IBM Maximo shaped: a maintenance notification that becomes an order."""
    name, system = "cmms", "CMMS-WO"

    def validate(self, action: dict[str, Any]) -> None:
        _need_station(action)
        window = (action.get("parameters") or {}).get("schedule")
        if window is not None and not isinstance(window, dict):
            raise ActionRejected("schedule must be a maintenance-window object")

    def request(self, action: dict[str, Any]) -> dict[str, Any]:
        p = action.get("parameters") or {}
        return {"notification_type": "M2" if action["action_type"] == "maintenance_work_order" else "M1",
                "functional_location": f"{action['plant_id']}/S{action['station_id']}",
                "short_text": p.get("description", action["action_type"])[:40],
                # A manager's explicit priority wins over the risk-derived default.
                "priority": {"high": "1", "normal": "2", "low": "3"}.get(
                    p.get("priority"), {"medium": "2", "low": "3"}.get(action.get("risk_level"), "3")),
                "planned_window": p.get("schedule"),
                "source": f"PulseTwin incident {action['incident_id']}"}


class MESAdapter(EnterpriseActionAdapter):
    """A work instruction / line-balance change request -- a REQUEST to the MES team, never
    a recipe or PLC parameter write."""
    name, system = "mes", "MES-WI"

    def validate(self, action: dict[str, Any]) -> None:
        _need_station(action)

    def request(self, action: dict[str, Any]) -> dict[str, Any]:
        return {"change_request": action["action_type"], "line": action["plant_id"],
                "station": action["station_id"], "parameters": action.get("parameters") or {},
                "requires_engineering_signoff": True}


class QMSAdapter(EnterpriseActionAdapter):
    """A quality hold / inspection plan change on named units."""
    name, system = "qms", "QMS-HOLD"

    def validate(self, action: dict[str, Any]) -> None:
        units = (action.get("parameters") or {}).get("units")
        if action["action_type"] == "quality_hold" and not units:
            raise ActionRejected("a quality hold must name the units it holds")
        if units and (not isinstance(units, list) or len(units) > 500):
            raise ActionRejected("units must be a list of at most 500 unit ids")

    def request(self, action: dict[str, Any]) -> dict[str, Any]:
        p = action.get("parameters") or {}
        return {"hold_type": action["action_type"], "units": p.get("units", []),
                "reason": p.get("description", ""), "station_of_origin": action.get("station_id")}


class NotificationAdapter(EnterpriseActionAdapter):
    """Microsoft Teams / e-mail. Addressed to a ROLE channel, never a named person."""
    name, system = "notification", "MSG"

    def validate(self, action: dict[str, Any]) -> None:
        channel = (action.get("parameters") or {}).get("channel", "shift-supervisors")
        if channel not in ("shift-supervisors", "maintenance-planning", "quality-engineering"):
            raise ActionRejected(f"unknown notification channel '{channel}'")

    def request(self, action: dict[str, Any]) -> dict[str, Any]:
        p = action.get("parameters") or {}
        return {"channel": p.get("channel", "shift-supervisors"),
                "text": p.get("description", "")[:500]}


ADAPTERS: dict[str, EnterpriseActionAdapter] = {
    a.name: a for a in (CMMSAdapter(), MESAdapter(), QMSAdapter(), NotificationAdapter())}


def idempotency_key(recommendation_id: str | None, option_id: str | None, action_type: str,
                    parameters: dict[str, Any]) -> str:
    raw = json.dumps([recommendation_id, option_id, action_type, parameters], sort_keys=True,
                     default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def adapter_for(action_type: str) -> EnterpriseActionAdapter:
    if action_type in PROHIBITED:
        raise ActionRejected(f"'{action_type}' is a prohibited OT/safety action; no adapter "
                             "exists for it and none may be added")
    spec = CATALOGUE.get(action_type)
    if spec is None:
        raise ActionRejected(f"'{action_type}' is not in the enterprise action catalogue")
    if spec["adapter"] == "none":
        raise ActionRejected(f"'{action_type}' coordinates nothing: there is nothing to execute")
    return ADAPTERS[spec["adapter"]]


def request_action(ops: Any, *, incident: dict[str, Any], recommendation_id: str | None,
                   option: dict[str, Any], requested_by: str, approved_by: str | None,
                   autonomy_level: str) -> dict[str, Any]:
    """Create (or return the existing) action row for an approved option. Idempotent."""
    action_type = option["action_type"]
    adapter = adapter_for(action_type)
    params = option.get("parameters") or {}
    key = idempotency_key(recommendation_id, option.get("option_id"), action_type, params)
    existing = db.query_one(ops, "SELECT * FROM enterprise_actions WHERE idempotency_key=?", (key,))
    if existing:
        return existing
    draft = {"action_type": action_type, "plant_id": incident["plant_id"],
             "station_id": option.get("station_id"), "parameters": params,
             "incident_id": incident["incident_id"], "idempotency_key": key,
             "risk_level": CATALOGUE[action_type]["risk"]}
    adapter.validate(draft)
    action_id = "ACT-" + secrets.token_hex(5)
    now = _now()
    db.execute(ops, """INSERT INTO enterprise_actions
        (action_id, incident_id, recommendation_id, option_id, plant_id, station_id,
         action_type, parameters, requested_by, approved_by, approval_timestamp, risk_level,
         autonomy_level, adapter, idempotency_key, created_at, status, retry_count,
         audit_metadata, predicted)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)
        ON CONFLICT (idempotency_key) DO NOTHING""",
        (action_id, incident["incident_id"], recommendation_id, option.get("option_id"),
         incident["plant_id"], option.get("station_id"), action_type, json.dumps(params),
         requested_by, approved_by, now if approved_by else None, draft["risk_level"],
         autonomy_level, adapter.name, key, now, "approved" if approved_by else "requested",
         json.dumps({"adapter_mode": "reference_mock", "system": adapter.system}),
         json.dumps(option.get("expected_impact") or {})))
    return db.query_one(ops, "SELECT * FROM enterprise_actions WHERE idempotency_key=?", (key,))


def execute(ops: Any, action: dict[str, Any]) -> dict[str, Any]:
    """Execute once. An action already executed returns its stored receipt unchanged."""
    if action["status"] in ("executed", "rolled_back"):
        return {**action, "replayed": True}
    if action["status"] not in ("approved", "failed", "executing"):
        raise ActionRejected(f"action is {action['status']}; only an approved action executes")
    adapter = adapter_for(action["action_type"])
    body = {**action, "parameters": _loads(action.get("parameters")) or {}}
    adapter.validate(body)
    db.execute(ops, "UPDATE enterprise_actions SET status='executing', retry_count=retry_count+? "
                    "WHERE action_id=?", (1 if action["status"] == "failed" else 0,
                                          action["action_id"]))
    try:
        receipt = adapter.execute(body)
    except Exception as exc:  # noqa: BLE001 - an adapter failure is recorded, not raised raw
        db.execute(ops, "UPDATE enterprise_actions SET status='failed', last_error=? "
                        "WHERE action_id=?", (str(exc)[:500], action["action_id"]))
        raise
    meta = _loads(action.get("audit_metadata")) or {}
    db.execute(ops, """UPDATE enterprise_actions SET status='executed', executed_at=?,
                       external_reference=?, audit_metadata=?, last_error=NULL WHERE action_id=?""",
               (_now(), receipt["external_reference"],
                json.dumps({**meta, "receipt": receipt}), action["action_id"]))
    return db.query_one(ops, "SELECT * FROM enterprise_actions WHERE action_id=?",
                        (action["action_id"],))


def rollback(ops: Any, action: dict[str, Any]) -> dict[str, Any]:
    if action["status"] != "executed":
        raise ActionRejected(f"only an executed action can be rolled back (it is {action['status']})")
    spec = CATALOGUE[action["action_type"]]
    if not spec["reversible"]:
        raise ActionRejected(f"'{action['action_type']}' is irreversible; raise a new action instead")
    out = adapter_for(action["action_type"]).rollback(action)
    db.execute(ops, "UPDATE enterprise_actions SET status='rolled_back', rollback_reference=? "
                    "WHERE action_id=?", (out["rollback_reference"], action["action_id"]))
    return db.query_one(ops, "SELECT * FROM enterprise_actions WHERE action_id=?",
                        (action["action_id"],))


def _loads(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v
