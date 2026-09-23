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
