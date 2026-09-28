"""The Autonomy Governor (ADR-067): how much authority the AI has for one action, now.

    L0 OBSERVE      explain state only
    L1 INVESTIGATE  run diagnostic probes and simulations
    L2 RECOMMEND    generate, simulate and rank recommendations
    L3 COORDINATE   create an enterprise workflow request AFTER human approval
    L4 AUTO-EXECUTE whitelisted low-risk IT workflows only (a notification)
    OT CONTROL      never available to any AI path -- not a level, a refusal

The GRANTED level is the lowest of what the evidence, the data, the model, the action and
the caller's role each allow. Every input and every reason is returned and persisted to
`autonomy_decisions`, so a decision can be replayed and argued with.

Decisions:
    BLOCKED                    prohibited action type (OT write, safety override ...)
    INVESTIGATION_ONLY         the evidence/data/model cannot support acting
    HUMAN_APPROVAL_REQUIRED    L3: a person approves, then the gateway coordinates
    AUTO_EXECUTE_ALLOWED       L4 whitelist AND confidence >= autonomous floor
    RECOMMEND_ONLY             allowed to show (L2), nothing to execute (e.g. monitoring)
"""

from __future__ import annotations

from typing import Any

from serve.agentic_config import CATALOGUE, CONF, CONFIG, LEVELS, PROHIBITED, level_index

DIAGNOSTIC = frozenset({"continue_monitoring", "notify_supervisor", "manual_inspection",
                        "sensor_calibration", "maintenance_inspection"})


def evaluate(action_type: str, *, assessment: str, confidence: float, sensor_ok: bool,
             model_status: str, simulation: dict[str, Any] | None, cost_usd: float | None,
             role: str, plant_policy: dict[str, Any] | None = None) -> dict[str, Any]:
    reasons: list[str] = []
    inputs = {"action_type": action_type, "assessment": assessment,
              "confidence": confidence, "sensor_ok": sensor_ok, "model_status": model_status,
              "simulation_verdict": (simulation or {}).get("verdict"),
              "cost_usd": cost_usd, "role": role}
    if action_type in PROHIBITED or action_type not in CATALOGUE:
        return _out("BLOCKED", None, "L0", inputs,
                    [f"'{action_type}' is " + ("a prohibited OT/safety action: never available "
                                                "to generative AI" if action_type in PROHIBITED
                                                else "not in the enterprise action catalogue")])
    spec = CATALOGUE[action_type]
    required = spec["level"]
    ceilings = {"role": CONFIG["autonomy"]["role_ceiling"].get(role, "L0")}

    # evidence
    if assessment == "CONFIDENT":
        ceilings["evidence"] = "L4" if confidence >= CONF["autonomous_low_risk_min"] else "L3"
    elif assessment in ("LOW_CONFIDENCE", "CONFLICTING_EVIDENCE", "INSUFFICIENT_DATA"):
        ceilings["evidence"] = "L3" if action_type in DIAGNOSTIC else "L1"
        reasons.append(f"assessment {assessment}: only diagnostic actions may proceed")
    else:  # MODEL_DEGRADED / SENSOR_UNRELIABLE
        ceilings["evidence"] = "L3" if action_type in DIAGNOSTIC else "L1"
        reasons.append(f"{assessment}: recommendations blocked, investigation only")
    if not sensor_ok and action_type not in DIAGNOSTIC:
        ceilings["data"] = "L1"
        reasons.append("sensor health below policy: no corrective action on unreliable data")
    if model_status == "degraded" and action_type not in DIAGNOSTIC:
        ceilings["model"] = "L1"
        reasons.append("model drift above the block threshold")
    # simulation: a corrective action with a twin effect needs a supporting experiment
    if spec["twin_effect"] != "none":
        verdict = (simulation or {}).get("verdict")
        if verdict is None:
            reasons.append("not tested in the digital twin: impact is unquantified")
        elif verdict != "improved":
            reasons.append(f"digital-twin test verdict '{verdict}': expected impact not "
                           "established")
    # reversibility / cost
    if not spec["reversible"]:
        reasons.append("irreversible action: human approval is mandatory")
    limit = float(CONFIG["actions"]["max_cost_usd_without_leadership"])
    if cost_usd is not None and cost_usd > limit:
        ceilings["cost"] = "L2"
        reasons.append(f"estimated cost ${cost_usd:,.0f} exceeds ${limit:,.0f}: "
                       "leadership sign-off outside this workflow")
    if plant_policy and plant_policy.get("max_level"):
        ceilings["plant"] = plant_policy["max_level"]

    granted = min((level_index(v) for v in ceilings.values()), default=0)
    granted_level = LEVELS[granted]
    # An L4-whitelisted action may always take the slower L3 route through a person.
    approval_need = min(level_index(required), level_index("L3"))
    if action_type == "continue_monitoring":
        decision = "RECOMMEND_ONLY"
    elif (action_type in CONFIG["actions"]["auto_execute_whitelist"]
          and granted >= level_index("L4")
          and confidence >= CONF["autonomous_low_risk_min"]):
        decision = "AUTO_EXECUTE_ALLOWED"
    elif granted >= approval_need:
        decision = "HUMAN_APPROVAL_REQUIRED"
    else:
        decision = "INVESTIGATION_ONLY"
    if decision == "INVESTIGATION_ONLY" and not reasons:
        reasons.append(f"granted {granted_level} is below the {required} this action needs")
    if decision == "HUMAN_APPROVAL_REQUIRED":
        reasons.append(f"{required} action: a manager approves before anything is coordinated")
    return _out(decision, required, granted_level, inputs, reasons, ceilings)


def _out(decision: str, required: str | None, granted: str, inputs: dict[str, Any],
         reasons: list[str], ceilings: dict[str, str] | None = None) -> dict[str, Any]:
    return {"decision": decision, "required_level": required, "granted_level": granted,
            "inputs": inputs, "reasons": reasons, "ceilings": ceilings or {}}
