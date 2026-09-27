"""Small, dependency-free readers for operational policy values.

The serving image intentionally excludes PyYAML.  These readers extract only the
well-known scalar keys the API executes, keeping the reviewable YAML file as the single
source of truth without widening the serving import wall.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def containment_action_thresholds(path: Path | None = None) -> tuple[float, float]:
    text = (path or ROOT / "configs" / "alert_policies.yaml").read_text(encoding="utf-8")

    def scalar(name: str) -> float:
        match = re.search(rf"(?m)^\s*{re.escape(name)}:\s*([0-9]+(?:\.[0-9]+)?)\s*$", text)
        if not match:
            raise RuntimeError(f"missing policy value containment_actions.{name}")
        return float(match.group(1))

    hold = scalar("hold_risk")
    inspect = scalar("inspect_risk")
    if not 1 >= hold > inspect > 0:
        raise RuntimeError("containment action thresholds must satisfy 1 >= hold > inspect > 0")
    return hold, inspect


OUTCOME_METRICS = ("stalled_share", "humidity_excess_pct", "signal_deviation_pct")


def outcome_policy(path: Path | None = None) -> dict[str, object]:
    """configs/alert_policies.yaml `outcome_evaluation:` (ADR-064), as numbers.

    Every key is flat and unique precisely so this regex reader cannot pick up a value
    from another block. A missing key is a startup error, not a silent default."""
    text = (path or ROOT / "configs" / "alert_policies.yaml").read_text(encoding="utf-8")

    def scalar(name: str) -> float:
        match = re.search(rf"(?m)^\s*{re.escape(name)}:\s*([0-9]+(?:\.[0-9]+)?)\b", text)
        if not match:
            raise RuntimeError(f"missing policy value outcome_evaluation.{name}")
        return float(match.group(1))

    metrics = {}
    for m in OUTCOME_METRICS:
        t = {k: scalar(f"{m}_{k}") for k in ("success_rel", "partial_rel", "harm_rel",
                                              "min_baseline")}
        if not 1 >= t["success_rel"] > t["partial_rel"] > 0 or t["harm_rel"] <= 0:
            raise RuntimeError(f"outcome thresholds for {m} must satisfy "
                               "1 >= success_rel > partial_rel > 0 < harm_rel")
        metrics[m] = t
    return {"window_s": scalar("outcome_window_s"),
            "baseline_window_s": scalar("outcome_baseline_window_s"),
            "min_events": int(scalar("outcome_min_events")),
            "metrics": metrics}
