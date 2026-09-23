"""The sensor-coverage masking engine (ADR-046-adjacent; see docs/benchmarking.md).

One reusable engine applied to any feature dataframe, not per-adapter logic -- this is
exactly the thing `benchmark/adapters/base.py`'s docstring says the ABC deliberately does
NOT duplicate into every adapter.

Two strategies for this pass (RANDOM, STATION_LEVEL) rather than the full six some
proposals ask for: these two most directly answer "the twin must work with uneven sensor
coverage" -- RANDOM for generic missingness, STATION_LEVEL for "one whole station went
dark," the single most realistic and most-asked-about failure mode in this project's own
simulator story. FEATURE_GROUP/TEMPORAL/BURST_FAILURE/REAL_MISSINGNESS are a documented
fast-follow, not built here.

Never mutates the input -- every call returns a new dataframe.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

RANDOM = "random"
STATION_LEVEL = "station_level"
STRATEGIES = (RANDOM, STATION_LEVEL)

COVERAGE_LEVELS: tuple[int, ...] = (100, 80, 60, 40, 20)


def mask(
    X: pd.DataFrame,
    coverage_pct: int,
    strategy: str = RANDOM,
    *,
    station_of: dict[str, str] | None = None,
    seed: int = 42,
) -> pd.DataFrame:
    """Return a COPY of `X` with `(100 - coverage_pct)%` of it set to NaN.

    `station_of`: column -> station_id, required for STATION_LEVEL (e.g. Bosch's parsed
    feature metadata). Ignored for RANDOM.
    """
    if not 0 <= coverage_pct <= 100:
        raise ValueError(f"coverage_pct must be 0-100, got {coverage_pct}")
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}; supported: {STRATEGIES}")

    out = X.copy()
    if coverage_pct == 100:
        return out

    rng = np.random.default_rng(seed)
    drop_frac = (100 - coverage_pct) / 100.0

    if strategy == RANDOM:
        mask_arr = rng.random(out.shape) < drop_frac
        out = out.mask(mask_arr)
        return out

    # STATION_LEVEL: drop whole stations' worth of columns until the dropped share of
    # COLUMNS is >= drop_frac. Coarser than random by design -- it simulates "a station
    # went dark," not "individual readings are noisy."
    if not station_of:
        raise ValueError("STATION_LEVEL masking requires station_of (column -> station_id)")
    stations = sorted({s for s in station_of.values() if s is not None})
    rng.shuffle(np.array(stations, dtype=object)) if stations else None
    order = list(rng.permutation(stations)) if stations else []
    n_drop = round(len(order) * drop_frac)
    dropped_stations = set(order[:n_drop])
    drop_cols = [c for c, s in station_of.items() if s in dropped_stations and c in out.columns]
    out[drop_cols] = np.nan
    return out


def coverage_sweep(
    levels: tuple[int, ...] = COVERAGE_LEVELS,
    strategy: str = RANDOM,
) -> list[int]:
    return list(levels)
