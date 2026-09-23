"""Shared offline/live feature definitions.

This module is deliberately standard-library only so both the pandas offline pipeline and
the lightweight MQTT ingestion image can import the same window definitions and numeric
formulas. Values passed in ``previous`` must be strictly earlier observations; the
current unit never contributes to its own baseline.
"""

from __future__ import annotations

import math
import statistics
from typing import Iterable


FEATURE_CONTRACT_VERSION = "pulsetwin_features_v2"
TRAILING_MEDIAN_UNITS = 50
TRAILING_Z_UNITS = 200
ROLLING_STD_UNITS = 10
RATE_OF_CHANGE_LAG = 3
LOT_TRAILING_UNITS = 100
NOTE_CODES = ("OK", "MINOR_ADJ", "TOOL_SWAP", "REWORK_FLAG", "MATERIAL_VAR")


def _finite(values: Iterable[float]) -> list[float]:
    return [float(value) for value in values if value is not None and math.isfinite(float(value))]


def signal_features(
    prefix: str,
    value: float,
    previous: Iterable[float],
    previous_same_variant: Iterable[float],
) -> dict[str, float]:
    """Return live features matching ``offline.ml.featurize`` for one sensor value."""
    current = float(value)
    history = _finite(previous)
    variant_history = _finite(previous_same_variant)
    result = {prefix: current}

    median_window = variant_history[-TRAILING_MEDIAN_UNITS:]
    if len(median_window) >= 8:
        result[f"{prefix}_dev_median"] = current - statistics.median(median_window)

    z_window = history[-TRAILING_Z_UNITS:]
    if len(z_window) >= 20:
        sd = statistics.stdev(z_window)
        if sd > 0:
            result[f"{prefix}_z"] = (current - statistics.mean(z_window)) / sd

    if len(history) >= RATE_OF_CHANGE_LAG:
        result[f"{prefix}_roc"] = current - history[-RATE_OF_CHANGE_LAG]

    std_window = history[-ROLLING_STD_UNITS:]
    if len(std_window) >= 4:
        result[f"{prefix}_roll_std"] = statistics.stdev(std_window)

    return result
