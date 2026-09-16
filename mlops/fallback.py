"""SPC-based fallback: simpler and more conservative than the gradient-boosted ensemble,
running in shadow mode alongside it. When drift_detector.py flags the primary model
degraded, serve/ switches to surfacing THIS instead (serve/fallback_scorer.py loads the
baselines this module exports) and labels it plainly: "FALLBACK MODE -- simplified
statistical rules active while primary model is being validated."

Two classical techniques:

- 3-sigma rule: a reading beyond +/-3 standard deviations of its station's own rolling
  mean is a violation. This is the part cheap enough to run at REQUEST TIME inside serve/
  (numpy + a small precomputed baseline, no pandas -- see export_baselines()).
- the 8 Nelson rules: pattern detection across a station's CONSECUTIVE units (not within
  one unit's single visit -- a control chart needs a sequence). Too data-hungry to run
  per-request; used here OFFLINE to evaluate how the fallback would have performed across
  the whole replay window (evaluate_shadow()), which is what
  GET /api/model/health.fallback.shadow_precision/recall report.

This module is offline-tier (mlops/, not serve/) and may use pandas -- see CLAUDE.md SS2.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from offline.config_schema import load_plant_config

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ARTIFACTS = ROOT / "artifacts"

SIG_COLS = ("torque_nm", "vibration_mm_s", "temperature_c", "weld_current_a",
            "humidity_pct", "film_thickness_um")


# --------------------------------------------------------------------- Nelson rules

def nelson_rules(z: np.ndarray) -> dict[str, np.ndarray]:
    """z: 1-D standardised readings, (x - rolling_mean) / rolling_std, in chronological
    order for ONE station across consecutive units. Returns one boolean array per rule,
    True at the index where the pattern ending there is a violation.
    """
    n = len(z)
    out = {f"rule_{i}": np.zeros(n, dtype=bool) for i in range(1, 9)}
    if n == 0:
        return out

    out["rule_1"] = np.abs(z) > 3.0                                    # 1 point beyond 3s

    sign = np.sign(z)
    for i in range(8, n):                                              # 9 same side
        w = sign[i - 8:i + 1]
        if w[0] != 0 and np.all(w == w[0]):
            out["rule_2"][i] = True

    for i in range(5, n):                                              # 6 steadily trending
        d = np.diff(z[i - 5:i + 1])
        if np.all(d > 0) or np.all(d < 0):
            out["rule_3"][i] = True

    for i in range(13, n):                                             # 14 alternating
        d = np.sign(np.diff(z[i - 13:i + 1]))
        if d.size and np.all(d != 0) and np.all(d[1:] == -d[:-1]):
            out["rule_4"][i] = True

    for i in range(2, n):                                              # 2 of 3 beyond 2s, same side
        w = z[i - 2:i + 1]
        if (w > 2.0).sum() >= 2 or (w < -2.0).sum() >= 2:
            out["rule_5"][i] = True

    for i in range(4, n):                                              # 4 of 5 beyond 1s, same side
        w = z[i - 4:i + 1]
        if (w > 1.0).sum() >= 4 or (w < -1.0).sum() >= 4:
            out["rule_6"][i] = True

    for i in range(14, n):                                             # 15 within 1s (too tight)
        if np.all(np.abs(z[i - 14:i + 1]) < 1.0):
            out["rule_7"][i] = True

    for i in range(7, n):                                              # 8 beyond 1s, mixed side
        if np.all(np.abs(z[i - 7:i + 1]) > 1.0):
            out["rule_8"][i] = True

    return out


def combined_violation(z: np.ndarray) -> np.ndarray:
    """Any-rule-fires boolean per index, plus rule_1 (3-sigma) counted double: a single
    wild outlier should not need eight rules' worth of corroboration to matter."""
    rules = nelson_rules(z)
    fired = np.zeros(len(z), dtype=int)
    for name, arr in rules.items():
        fired += arr.astype(int) * (2 if name == "rule_1" else 1)
    return fired > 0


# --------------------------------------------------------------- baselines for serve/

def export_baselines(plant_id: str, window: int = 200) -> Path:
    """Per (station, signal) rolling mean/std from the training population, flattened to
    plain numpy arrays -- serve/fallback_scorer.py loads exactly this file, nothing else.
    """
    tel = pd.read_parquet(DATA / plant_id / "telemetry.parquet")
    station_ids, signals, means, stds = [], [], [], []
    for sid, grp in tel.groupby("station_id"):
        grp = grp.sort_values("entry_ts")
        for sig in SIG_COLS:
            if sig not in grp or grp[sig].notna().sum() < window:
                continue
            series = grp[sig].dropna().to_numpy()[-window:]
            station_ids.append(int(sid))
            signals.append(sig)
            means.append(float(series.mean()))
            stds.append(float(series.std() or 1.0))

    out_path = ARTIFACTS / f"fallback_{plant_id}.npz"
    np.savez_compressed(
        out_path,
        station_id=np.asarray(station_ids, dtype=np.int32),
        signal=np.asarray(signals, dtype=object),
        mean=np.asarray(means, dtype=np.float64),
        std=np.asarray(stds, dtype=np.float64),
    )
    return out_path


# ------------------------------------------------------------------- shadow evaluation

def evaluate_shadow(plant_id: str) -> dict:
    """How would the fallback have done across the whole replay-eligible dataset?

    Per station, z-score each signal against a rolling window of the PRECEDING units
    (never future units -- the same leakage discipline as offline/ml/featurize.py), flag
    any-rule violations, then call a unit "fallback-flagged" if any station on its journey
    was flagged. Compared against the simulator's true_defect (offline validation only,
    never fed to the model -- ADR-014).
    """
    tel = pd.read_parquet(DATA / plant_id / "telemetry.parquet")
    scores = pd.read_parquet(DATA / plant_id / "scores.parquet").set_index("unit_seq")

    flagged_units: set[int] = set()
    for sid, grp in tel.groupby("station_id"):
        grp = grp.sort_values("entry_ts")
        for sig in SIG_COLS:
            if sig not in grp or grp[sig].notna().sum() < 30:
                continue
            sub = grp[["unit_seq", sig]].dropna().reset_index(drop=True)
            vals = sub[sig].to_numpy()
            roll_mean = pd.Series(vals).rolling(50, min_periods=10).mean().shift(1).to_numpy()
            roll_std = pd.Series(vals).rolling(50, min_periods=10).std().shift(1).to_numpy()
            with np.errstate(invalid="ignore", divide="ignore"):
                z = (vals - roll_mean) / np.where(roll_std > 1e-9, roll_std, np.nan)
            z = np.nan_to_num(z, nan=0.0)
            violations = combined_violation(z)
            flagged_units.update(int(u) for u in sub.loc[violations, "unit_seq"])

    y_true = scores["y_true"].to_numpy()
    pred = np.zeros(len(scores), dtype=bool)
    idx_of = {seq: i for i, seq in enumerate(scores.index)}
    for seq in flagged_units:
        if seq in idx_of:
            pred[idx_of[seq]] = True

    tp = int(np.sum(pred & (y_true == 1)))
    fp = int(np.sum(pred & (y_true == 0)))
    fn = int(np.sum(~pred & (y_true == 1)))
    precision = tp / max(1, tp + fp)
    recall = tp / max(1, tp + fn)
    return {
        "plant_id": plant_id, "flagged_units": len(flagged_units),
        "total_units": len(scores), "shadow_precision": round(precision, 4),
        "shadow_recall": round(recall, 4), "true_positives": tp, "false_positives": fp,
        "false_negatives": fn,
    }


def _write_metric(plant_id: str, key: str, value: float) -> None:
    """Writes into the SAME `metrics` table build_artifact.py bakes -- a deliberate,
    direct write to the artifact file, not through serve's read-only connection
    (ADR-011's read-only guard is about accidental writes from request handlers; this is
    an offline maintenance step, exactly like build_artifact.py itself)."""
    import os
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        import psycopg
        with psycopg.connect(database_url) as con:
            con.execute(
                "INSERT INTO metrics (plant_id, key, value, context) VALUES (%s,%s,%s,'shadow') "
                "ON CONFLICT (plant_id, key, context) DO UPDATE SET value = EXCLUDED.value",
                (plant_id, key, value),
            )
    else:
        con = sqlite3.connect(ARTIFACTS / "pulsetwin.db")
        con.execute("INSERT OR REPLACE INTO metrics VALUES (?,?,?,?)", (plant_id, key, value, "shadow"))
        con.commit()
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    cfg = load_plant_config(args.config)
    pid = cfg.plant_id

    baseline_path = export_baselines(pid)
    result = evaluate_shadow(pid)
    _write_metric(pid, "fallback_shadow_precision", result["shadow_precision"])
    _write_metric(pid, "fallback_shadow_recall", result["shadow_recall"])

    print(f"=== {pid} SPC/Nelson fallback (shadow) ===")
    print(f"  baselines -> {baseline_path.name}")
    print(f"  flagged {result['flagged_units']}/{result['total_units']} units")
    print(f"  shadow precision {result['shadow_precision']:.2%}   "
          f"shadow recall {result['shadow_recall']:.2%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
