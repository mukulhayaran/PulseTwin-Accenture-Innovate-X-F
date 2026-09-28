"""The serve-side mirror of configs/agentic.yaml (ADR-067).

serve/ cannot import yaml (CLAUDE.md §2), so the reviewable YAML and this dict hold the
same values and tests/test_incident_commander.py::test_config_mirror asserts they are
EQUAL. Change one, change both.
"""

from __future__ import annotations

from typing import Any


def _a(adapter: str, level: str, risk: str, reversible: bool, downtime_min: int,
       technician_hours: float, twin_effect: str) -> dict[str, Any]:
    return {"adapter": adapter, "level": level, "risk": risk, "reversible": reversible,
            "downtime_min": downtime_min, "technician_hours": technician_hours,
            "twin_effect": twin_effect}


CONFIG: dict[str, Any] = {
    "commander": {
        "max_steps": 10, "max_probe_calls": 20, "max_simulations": 2, "timeout_s": 900,
        "max_llm_tokens": 60000, "max_llm_cost_usd": 0.50, "evidence_window_s": 900,
        "pre_trigger_s": 300, "post_trigger_s": 600,
        "baseline_min_events": 20, "station_scope_hops": 2, "planner_stale_s": 120,
        "min_information_gain": 0.02,
    },
    "confidence": {
        "recommendation_min": 0.70, "autonomous_low_risk_min": 0.90,
        "stop_confidence": 0.80, "conflict_margin": 0.15, "min_evidence_coverage": 0.50,
    },
    "maturity_caps": {"HIGH": 0.95, "MEDIUM": 0.85, "LOW": 0.70, "MANUAL_ONLY": 0.65,
                      "UNKNOWN": 0.50},
    "evidence_quality": {"measured": 1.0, "simulated": 0.8, "inferred": 0.6, "manual": 0.5,
                         "missing": 0.0},
    "model_health": {"psi_warn": 0.10, "psi_block": 0.25},
    "data_quality": {"sensor_unreliable_below": 0.60},
    "autonomy": {
        "default_level": "L2", "levels": ["L0", "L1", "L2", "L3", "L4"],
        "role_ceiling": {"supervisor": "L1", "manager": "L3", "leadership": "L2",
                         "system": "L4"},
        "execute_roles": ["manager", "system"],
    },
    "actions": {
        "catalogue": {
            "continue_monitoring": _a("none", "L2", "none", True, 0, 0.0, "none"),
            "notify_supervisor": _a("notification", "L4", "none", True, 0, 0.0, "none"),
            "manual_inspection": _a("cmms", "L3", "low", True, 0, 0.5, "none"),
            "sensor_calibration": _a("cmms", "L3", "low", True, 15, 1.0, "none"),
            "maintenance_inspection": _a("cmms", "L3", "low", True, 25, 1.0, "none"),
            "conveyor_buffer_adjustment": _a("cmms", "L3", "low", True, 25, 1.5,
                                             "increase_feed_buffer"),
            "maintenance_work_order": _a("cmms", "L3", "medium", False, 45, 2.0,
                                         "maintenance_ticket"),
            "hvac_correction": _a("cmms", "L3", "low", True, 0, 1.0, "hvac_setpoint"),
            "line_rebalance": _a("mes", "L3", "medium", True, 0, 0.5, "rebalance_cycle"),
            "quality_hold": _a("qms", "L3", "medium", True, 0, 0.5, "none"),
            "increase_inspection": _a("qms", "L3", "low", True, 0, 0.5, "none"),
        },
        "auto_execute_whitelist": ["notify_supervisor"],
        "supervisor_handled": ["continue_monitoring", "notify_supervisor", "manual_inspection",
                               "increase_inspection"],
        "prohibited": ["plc_write", "setpoint_change", "safety_override", "emergency_stop",
                       "interlock_bypass"],
        "max_cost_usd_without_leadership": 25000,
    },
    "business": {
        "technician_cost_per_hour": {
            "value": 95, "currency": "USD",
            "source": "docs/ASSUMPTIONS.md, Enterprise action assumptions: fully loaded "
                      "maintenance technician rate",
        },
    },
    "maintenance_windows": [
        {"window_id": "nightly_changeover", "name": "Nightly changeover", "weekday": None,
         "start": "02:00", "duration_min": 60, "available_teams": 1},
        {"window_id": "sunday_maintenance", "name": "Sunday maintenance window",
         "weekday": 6, "start": "06:00", "duration_min": 480, "available_teams": 3},
    ],
    "quality_watch": {"window_inspections": 30, "alpha": 0.01, "min_defects": 3},
}

C = CONFIG["commander"]
CONF = CONFIG["confidence"]
CATALOGUE: dict[str, dict[str, Any]] = CONFIG["actions"]["catalogue"]
PROHIBITED: frozenset[str] = frozenset(CONFIG["actions"]["prohibited"])
LEVELS: list[str] = CONFIG["autonomy"]["levels"]


def level_index(level: str) -> int:
    return LEVELS.index(level)
