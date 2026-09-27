"""Can defect prediction reach 75-80% precision AND recall? Two prototypes, measured.

    python -m offline.experiments.p75_prototype --stage oracle           # all variants, both plants
    python -m offline.experiments.p75_prototype --stage train --variant V3
    python -m offline.experiments.p75_prototype --option2                # at-risk target on V0

Offline experiment only. Everything is written under data/_exp/ (gitignored); nothing in
artifacts/, configs/ or the shipped data/<plant>/ is touched.

Option 1 makes the simulated defect physics more deterministic, one cause at a time
(cumulative variants V0..V4), and reports the ORACLE -- the simulator's own propensity --
because no model can beat it: if the oracle cannot clear 0.75/0.75, no model will.

Option 2 keeps today's physics and changes the TARGET from "failed inspection" (a
Bernoulli draw) to "went through an observable failure condition", and compares the model
against a hand-written rule on the same conditions -- if the rule matches, the ML adds
nothing and the report says so.

Operating points: thresholds are CHOSEN on validation and MEASURED on test (ADR-061).
  best_f1  -- validation threshold maximising F1, plus the alerts/shift it implies
  budget   -- validation threshold giving max_alerts_per_shift (the shipped 6/shift)
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")  # ADR-016 -- before sklearn imports

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.metrics import average_precision_score, precision_recall_curve  # noqa: E402

from offline.config_schema import load_economics_config, load_plant_config  # noqa: E402
from offline.ml.defect_model import train  # noqa: E402
from offline.ml.featurize import build_features  # noqa: E402
from offline.simulator import generate  # noqa: E402
from offline.simulator.physics import _station_targets  # noqa: E402

EXP_ROOT = Path(__file__).resolve().parents[2] / "data" / "_exp"
PLANTS = ("plant_a", "plant_b")
TARGET = 0.75

# Cumulative: each variant removes one more cause of irreducible error.
VARIANTS: dict[str, dict] = {
    "V0": {},
    "V1": {"label_noise_rate": 0.0, "inspection_detection_rate": 0.99,
           "inspection_false_reject_rate": 0.0},
}
VARIANTS["V2"] = {**VARIANTS["V1"], "w_lot": 0.0, "w_noise": 0.0}
VARIANTS["V3"] = {**VARIANTS["V2"], "mode_a.strong_batch_factor": 1.0}
VARIANTS["V4"] = {**VARIANTS["V3"], "hazard_cap": 0.99, "intensity_sharpening": 6.0}
DESCRIBE = {
    "V0": "shipped physics",
    "V1": "+ clean inspection labels",
    "V2": "+ no hidden lot / noise hazard",
    "V3": "+ paint batch quality irrelevant",
    "V4": "+ sharper, near-deterministic hazards",
}


def variant_config(plant: str, variant: str):
    cfg = load_plant_config(f"{plant}.yaml")
    raw = cfg.model_dump()
    dp = raw["defect_physics"]
    for key, val in VARIANTS[variant].items():
        node = dp
        *path, leaf = key.split(".")
        for p in path:
            node = node[p]
        node[leaf] = val
    return type(cfg).model_validate(raw)


def ensure_data(plant: str, variant: str) -> Path:
    root = EXP_ROOT / variant
    if not (root / plant / "risk_conditions.parquet").exists():
        generate.run(variant_config(plant, variant), out_root=root)
    return root / plant


def load(plant: str, variant: str):
    d = ensure_data(plant, variant)
    cfg = variant_config(plant, variant)
    tel = pd.read_parquet(d / "telemetry.parquet")
    ck = pd.read_parquet(d / "checklists.parquet")
    ins = pd.read_parquet(d / "inspections.parquet")
    fs = build_features(cfg, tel, ck, ins)
    return cfg, fs, ins.set_index("unit_seq"), pd.read_parquet(d / "risk_conditions.parquet")


# ------------------------------------------------------------------ operating points

def _counts(y: np.ndarray, alert: np.ndarray) -> dict:
    tp = int((alert & (y == 1)).sum())
    fp = int((alert & (y == 0)).sum())
    fn = int((~alert & (y == 1)).sum())
    return {"precision": tp / max(1, tp + fp), "recall": tp / max(1, tp + fn),
            "alerts": int(alert.sum()), "tp": tp, "fp": fp, "fn": fn}


def operating_points(y_va, s_va, y_te, s_te, shifts_va: int, shifts_te: int,
                     budget_per_shift: float) -> dict:
    P, R, T = precision_recall_curve(y_va, s_va)
    f1 = 2 * P[:-1] * R[:-1] / np.maximum(P[:-1] + R[:-1], 1e-12)
    thr_f1 = float(T[int(np.argmax(f1))]) if len(T) else 0.5
    k = int(round(budget_per_shift * shifts_va))
    thr_budget = float(np.sort(s_va)[::-1][min(k, len(s_va)) - 1]) if k else float("inf")
    # A threshold at a tied score admits every unit sharing it; `>` below the tie would
    # overshoot, so use `>=` and report the alert count honestly.
    out = {}
    for name, thr in (("best_f1", thr_f1), ("budget", thr_budget)):
        c = _counts(y_te, s_te >= thr)
        c["threshold"] = thr
        c["alerts_per_shift"] = c["alerts"] / max(1, shifts_te)
        out[name] = c
    Pt, Rt, _ = precision_recall_curve(y_te, s_te)
    ok = Rt >= TARGET
    out["test_pr_auc"] = float(average_precision_score(y_te, s_te))
    out["test_precision_at_recall_75"] = float(Pt[ok].max()) if ok.any() else 0.0
    out["positives_per_shift_test"] = float(y_te.sum()) / max(1, shifts_te)
    return out


def _shifts(fs, mask) -> int:
    econ = load_economics_config()
    return int(fs.meta.loc[mask, "day"].nunique() * econ.production["shifts_per_day"])


def _splits(fs):
    split = fs.meta["split"].to_numpy()
    return split == "validate", split == "test"


def _budget(cfg) -> float:
    return float(load_economics_config().max_alerts_per_shift)


# ------------------------------------------------------------------ stages

def oracle_row(plant: str, variant: str) -> dict:
    cfg, fs, ins, _ = load(plant, variant)
    va, te = _splits(fs)
    prop = ins["propensity"].reindex(fs.meta["unit_seq"]).to_numpy()
    op = operating_points(fs.y[va], prop[va], fs.y[te], prop[te],
                          _shifts(fs, va), _shifts(fs, te), _budget(cfg))
    return {"plant": plant, "variant": variant, "describe": DESCRIBE[variant],
            "defect_rate": float(fs.y.mean()),
            "propensity_p99": float(np.quantile(prop, 0.99)), **op}


def model_row(plant: str, variant: str) -> dict:
    cfg, fs, ins, _ = load(plant, variant)
    va, te = _splits(fs)
    m = train(fs, seed=cfg.simulation.seed)
    s = m.predict(fs.X)
    prop = ins["propensity"].reindex(fs.meta["unit_seq"]).to_numpy()
    op = operating_points(fs.y[va], s[va], fs.y[te], s[te],
                          _shifts(fs, va), _shifts(fs, te), _budget(cfg))
    oracle = float(average_precision_score(fs.y[te], prop[te]))
    return {"plant": plant, "variant": variant, "describe": DESCRIBE[variant],
            "oracle_pr_auc": oracle, "oracle_capture": op["test_pr_auc"] / max(1e-9, oracle),
            **op}


def _rule(cfg, fs) -> np.ndarray:
    """The three failure conditions as a domain expert would write them, on FEATURES.
    Percentile cut-offs are fitted on the train split only."""
    dp = cfg.defect_physics
    X = fs.X
    tr = fs.meta["split"].to_numpy() == "train"
    hits = np.zeros(len(X), dtype=bool)
    parts = []
    if "paint_buffer_dwell_s" in X and "paint_entry_humidity_pct" in X:
        hits |= ((X["paint_buffer_dwell_s"] > dp.mode_a.dwell_threshold_s)
                 & (X["paint_entry_humidity_pct"] > dp.mode_a.humidity_threshold_pct)).to_numpy()
        parts.append("mode_a")
    ck = f"s{dp.mode_b.contributing_station}_checklist_time_s"
    if ck in X:
        hits |= (X[ck] < dp.mode_b.checklist_rush_threshold_s).to_numpy()
        parts.append("mode_b")
    tq_st, vb_st = dp.mode_c.contributing_stations
    tq, vb = f"s{tq_st}_torque_nm", f"s{vb_st}_vibration_mm_s"
    if tq in X and vb in X:
        target = _station_targets(tq_st)["torque_nm"]
        deficit = 100.0 * (target - X[tq]) / target
        vcut = np.nanquantile(X.loc[tr, vb], dp.mode_c.vibration_excess_percentile / 100.0)
        hits |= ((deficit > dp.mode_c.torque_deficit_threshold_pct) & (X[vb] > vcut)).to_numpy()
        parts.append("mode_c")
    return hits, parts


def option2_row(plant: str) -> dict:
    cfg, fs, ins, rc = load(plant, "V0")
    va, te = _splits(fs)
    rc = rc.set_index("unit_seq").reindex(fs.meta["unit_seq"])
    at_risk = ((rc["obs_mode_a"] > 0) | (rc["signal_mode_b"] > 0)
               | (rc["signal_mode_c"] > 0)).to_numpy().astype(np.int8)
    true_defect = ins["true_defect"].reindex(fs.meta["unit_seq"]).to_numpy()

    fs_risk = type(fs)(X=fs.X, meta=fs.meta, y=at_risk, y_true=fs.y_true,
                       defect_mode=fs.defect_mode)
    m = train(fs_risk, seed=cfg.simulation.seed)
    s = m.predict(fs.X)
    op = operating_points(at_risk[va], s[va], at_risk[te], s[te],
                          _shifts(fs, va), _shifts(fs, te), _budget(cfg))
    rule, parts = _rule(cfg, fs)
    rule_te = _counts(at_risk[te], rule[te])
    rule_te["alerts_per_shift"] = rule_te["alerts"] / max(1, _shifts(fs, te))
    return {
        "plant": plant,
        "at_risk_prevalence": float(at_risk.mean()),
        "defect_coverage": float(at_risk[true_defect == 1].mean()),
        "defect_rate_inside_at_risk": float(true_defect[at_risk == 1].mean()),
        "defect_rate_outside_at_risk": float(true_defect[at_risk == 0].mean()),
        "model": op,
        "rule": {**rule_te, "conditions_used": parts},
    }


# ------------------------------------------------------------------ report

def _pct(x: float) -> str:
    return f"{100 * x:5.1f}%"


def print_oracle(rows):
    print("\nORACLE (simulator's own propensity = the best any model can do)")
    print(f"{'plant':8} {'var':3} {'what':38} {'F1 P':>6} {'F1 R':>6} {'al/sh':>6}"
          f" {'P@R75':>6} {'budget P':>8} {'budget R':>8} {'p99':>5}")
    for r in rows:
        b, f = r["budget"], r["best_f1"]
        print(f"{r['plant']:8} {r['variant']:3} {r['describe']:38} {_pct(f['precision'])} "
              f"{_pct(f['recall'])} {f['alerts_per_shift']:6.1f} "
              f"{_pct(r['test_precision_at_recall_75'])} {_pct(b['precision']):>8} "
              f"{_pct(b['recall']):>8} {r['propensity_p99']:5.2f}")


def print_model(r):
    f, b = r["best_f1"], r["budget"]
    print(f"\nMODEL {r['plant']} {r['variant']} ({r['describe']}): PR-AUC {r['test_pr_auc']:.3f}"
          f" = {_pct(r['oracle_capture'])} of oracle {r['oracle_pr_auc']:.3f}")
    print(f"  best-F1 threshold: P {_pct(f['precision'])}  R {_pct(f['recall'])}"
          f"  at {f['alerts_per_shift']:.1f} alerts/shift")
    print(f"  6/shift budget   : P {_pct(b['precision'])}  R {_pct(b['recall'])}"
          f"  at {b['alerts_per_shift']:.1f} alerts/shift"
          f"  (defects/shift {r['positives_per_shift_test']:.1f})")


def print_option2(r):
    m, rule = r["model"], r["rule"]
    print(f"\nOPTION 2 {r['plant']}: at-risk prevalence {_pct(r['at_risk_prevalence'])}, "
          f"covers {_pct(r['defect_coverage'])} of real defects; defect rate inside "
          f"{_pct(r['defect_rate_inside_at_risk'])} vs outside {_pct(r['defect_rate_outside_at_risk'])}")
    f = m["best_f1"]
    print(f"  model best-F1: P {_pct(f['precision'])}  R {_pct(f['recall'])}"
          f"  at {f['alerts_per_shift']:.1f} alerts/shift   PR-AUC {m['test_pr_auc']:.3f}")
    print(f"  rule ({'+'.join(rule['conditions_used'])}): P {_pct(rule['precision'])}"
          f"  R {_pct(rule['recall'])}  at {rule['alerts_per_shift']:.1f} alerts/shift")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage", choices=["oracle", "train"])
    ap.add_argument("--variant", choices=list(VARIANTS), default="V0")
    ap.add_argument("--plants", nargs="+", default=list(PLANTS))
    ap.add_argument("--option2", action="store_true")
    args = ap.parse_args()

    EXP_ROOT.mkdir(parents=True, exist_ok=True)
    report_path = EXP_ROOT / "p75_report.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else {}

    if args.stage == "oracle":
        rows = [oracle_row(p, v) for p in args.plants for v in VARIANTS]
        report["oracle"] = rows
        print_oracle(rows)
    if args.stage == "train":
        rows = [model_row(p, args.variant) for p in args.plants]
        report.setdefault("model", {})[args.variant] = rows
        for r in rows:
            print_model(r)
    if args.option2:
        rows = [option2_row(p) for p in args.plants]
        report["option2"] = rows
        for r in rows:
            print_option2(r)

    report_path.write_text(json.dumps(report, indent=2, default=float))
    print(f"\nwrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
