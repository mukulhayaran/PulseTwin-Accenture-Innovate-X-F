"""Within-alert discrimination: does the model RANK the units it flags, or only flag them?

    python -m offline.benchmarks.score_diversity
    python -m offline.benchmarks.score_diversity --plant plant_a --json

PR-AUC measures whether the model separates defects from non-defects across the whole
population. It says nothing about the question a supervisor actually faces, which is:
"forty units are flagged and I have time for six -- which six?" A model can have a
respectable PR-AUC and be completely useless at that, if every flagged unit carries the
same score.

That is exactly what this model does, and it was already known qualitatively (CLAUDE.md
section 8: all 648 alerting units on plant A share risk_raw = 0.880117 to six decimals).
This module turns it into a measured, tracked, regression-testable number instead of a
paragraph in a README, and emits a WARNING when the alert cohort collapses.

WHY IT HAPPENS, measured rather than guessed: every tree in the ensemble splits on
`paint_buffer_dwell_s` at the root. Once a unit is past the dwell threshold and under the
checklist threshold, no later split discriminates, so every such unit lands in the same
leaf in EVERY tree and the ensemble sum is identical. The model identifies the at-risk
SET correctly and cannot order it.

WHAT THIS MODULE MUST NOT BE USED FOR. Adding noise to scores, jittering predictions, or
any transformation that raises `unique_ratio` without adding information would improve
every number here and improve nothing real. The metrics exist to detect the problem, not
to be optimised. `ranking_lift_auc` is the honest check: it is insensitive to monotone
score transformations, so jitter cannot move it.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "artifacts" / "pulsetwin.db"
OUT_PATH = ROOT / "artifacts" / "benchmarks" / "score_diversity.json"

# A cohort whose scores collapse below these is flagged. Chosen so the KNOWN plant A
# collapse trips them clearly while a healthy continuous score does not.
COLLAPSE_UNIQUE_RATIO = 0.25
COLLAPSE_ENTROPY = 0.50
TOP_K = (5, 10, 20, 50)
SCORE_DECIMALS = 9  # float noise must not be mistaken for discrimination


def _round(values: np.ndarray) -> np.ndarray:
    return np.round(values.astype(np.float64), SCORE_DECIMALS)


def normalised_entropy(values: np.ndarray) -> float:
    """Shannon entropy of the score VALUE distribution, divided by log(n).

    1.0 means every unit has its own distinct score; 0.0 means they all share one.
    Normalising by log(n) makes cohorts of different sizes comparable, which matters
    because the alert cohort changes size with the threshold.
    """
    if values.size <= 1:
        return 0.0
    _, counts = np.unique(_round(values), return_counts=True)
    probabilities = counts / counts.sum()
    entropy = -np.sum(probabilities * np.log(probabilities))
    # max(0.0, ...) because a single-valued cohort gives -0.0 through float negation,
    # and an entropy printed as "-0.000" reads like a bug in the metric rather than a
    # total collapse in the model.
    return max(0.0, float(entropy / math.log(values.size)))


def auc(scores: np.ndarray, labels: np.ndarray) -> float:
    """ROC-AUC by rank (Mann-Whitney). NaN when one class is absent.

    Ties are handled by average ranks, which is the entire point here: a cohort with
    identical scores must score 0.5, not an artefact of sort order.
    """
    positives = labels == 1
    n_pos, n_neg = int(positives.sum()), int((~positives).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    order = np.argsort(scores, kind="mergesort")
    ranks = np.empty(len(scores), dtype=np.float64)
    ranks[order] = np.arange(1, len(scores) + 1)
    # average ranks within tied score groups
    sorted_scores = scores[order]
    start = 0
    for i in range(1, len(sorted_scores) + 1):
        if i == len(sorted_scores) or sorted_scores[i] != sorted_scores[start]:
            ranks[order[start:i]] = (start + 1 + i) / 2.0
            start = i
    return float((ranks[positives].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def analyse(scores: np.ndarray, labels: np.ndarray, label: str) -> dict:
    """Diversity and within-cohort ranking metrics for one cohort."""
    scores = np.asarray(scores, dtype=np.float64)
    labels = np.asarray(labels, dtype=np.int64)
    n = int(scores.size)
    if n == 0:
        return {"cohort": label, "n": 0, "status": "EMPTY"}

    rounded = _round(scores)
    unique = int(np.unique(rounded).size)
    q1, q3 = np.percentile(scores, [25, 75])
    values, counts = np.unique(rounded, return_counts=True)
    modal_share = float(counts.max() / n)

    out = {
        "cohort": label,
        "n": n,
        "n_positive": int(labels.sum()),
        "base_rate": float(labels.mean()),
        "unique_scores": unique,
        "unique_ratio": unique / n,
        "score_entropy": normalised_entropy(scores),
        "score_std": float(scores.std(ddof=1)) if n > 1 else 0.0,
        "score_iqr": float(q3 - q1),
        "score_min": float(scores.min()),
        "score_max": float(scores.max()),
        "modal_score": float(values[counts.argmax()]),
        "modal_share": modal_share,
        "ranking_lift_auc": auc(scores, labels),
    }

    # precision@k / recall@k: take the k highest-scoring units. With heavy ties this is
    # order-dependent, so ties are broken RANDOMLY under a fixed seed and the expectation
    # is what gets reported -- taking whichever tied units happen to sort first would
    # flatter a collapsed model.
    rng = np.random.default_rng(42)
    total_positive = int(labels.sum())
    for k in TOP_K:
        if k > n:
            continue
        hits = []
        for _ in range(200):
            order = np.lexsort((rng.random(n), -scores))[:k]
            hits.append(int(labels[order].sum()))
        mean_hits = float(np.mean(hits))
        out[f"precision_at_{k}"] = mean_hits / k
        out[f"recall_at_{k}"] = mean_hits / total_positive if total_positive else float("nan")

    lift = out["ranking_lift_auc"]
    warnings = []
    if out["unique_ratio"] < COLLAPSE_UNIQUE_RATIO:
        warnings.append(
            f"SCORE COLLAPSE: {unique} unique scores across {n} units "
            f"({out['unique_ratio']:.1%}); the model cannot rank within this cohort"
        )
    if out["score_entropy"] < COLLAPSE_ENTROPY:
        warnings.append(f"LOW SCORE ENTROPY: {out['score_entropy']:.3f}")
    if modal_share > 0.5:
        warnings.append(
            f"MODAL SCORE DOMINATES: {modal_share:.1%} of units share the score "
            f"{out['modal_score']:.6f}"
        )
    if not math.isnan(lift) and abs(lift - 0.5) < 0.05:
        warnings.append(
            f"NO WITHIN-COHORT RANKING SIGNAL: AUC {lift:.3f} is indistinguishable "
            f"from random ordering"
        )
    out["warnings"] = warnings
    out["status"] = "COLLAPSED" if warnings else "OK"
    return out


def for_plant(con: sqlite3.Connection, plant_id: str) -> dict:
    rows = con.execute(
        """SELECT risk_score, calibrated_risk, alert_fired, actual_defect, true_defect, split
             FROM units WHERE plant_id=?""",
        (plant_id,),
    ).fetchall()
    if not rows:
        return {"plant_id": plant_id, "status": "NO DATA"}

    raw = np.array([r[0] for r in rows], dtype=np.float64)
    cal = np.array([r[1] for r in rows], dtype=np.float64)
    alert = np.array([r[2] or 0 for r in rows], dtype=np.int64)
    # true_defect is the simulator's ground truth; actual_defect is what inspection SAW.
    # Within-alert discrimination is about what the model could in principle order, so it
    # is scored against the truth, with the observed label reported alongside.
    truth = np.array([r[4] if r[4] is not None else (r[3] or 0) for r in rows], dtype=np.int64)
    observed = np.array([r[3] or 0 for r in rows], dtype=np.int64)
    split = np.array([r[5] or "" for r in rows])

    alerted = alert == 1
    test = split == "test"

    result = {
        "plant_id": plant_id,
        "n_units": len(rows),
        "n_alerts": int(alerted.sum()),
        "cohorts": {
            "all_units": analyse(cal, truth, "all scored units"),
            "alerted": analyse(cal[alerted], truth[alerted], "alerted units only"),
            "test_split": analyse(cal[test], truth[test], "held-out test split"),
            "alerted_raw_score": analyse(raw[alerted], truth[alerted], "alerted, RAW score"),
        },
    }
    result["cohorts"]["alerted_observed_label"] = analyse(
        cal[alerted], observed[alerted], "alerted units, inspection-observed label"
    )
    collapsed = [
        name for name, cohort in result["cohorts"].items()
        if cohort.get("status") == "COLLAPSED"
    ]
    result["collapsed_cohorts"] = collapsed
    result["status"] = "COLLAPSED" if collapsed else "OK"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plant", default=None, help="limit to one plant")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--json", action="store_true", help="print the full JSON")
    parser.add_argument("--fail-on-collapse", action="store_true",
                        help="exit 1 if any cohort collapses (informational in CI)")
    args = parser.parse_args(argv)

    path = Path(args.db)
    if not path.exists():
        print(f"artifact not found: {path}", file=sys.stderr)
        return 1

    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        plants = (
            [args.plant]
            if args.plant
            else [r[0] for r in con.execute("SELECT plant_id FROM plant_meta ORDER BY plant_id")]
        )
        report = {
            "generated_by": "offline/benchmarks/score_diversity.py",
            "collapse_thresholds": {
                "unique_ratio": COLLAPSE_UNIQUE_RATIO,
                "entropy": COLLAPSE_ENTROPY,
            },
            "plants": {pid: for_plant(con, pid) for pid in plants},
        }
    finally:
        con.close()

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for pid, plant in report["plants"].items():
            print(f"\n=== {pid}: {plant.get('n_alerts')} alerts of {plant.get('n_units')} units")
            for name, cohort in plant.get("cohorts", {}).items():
                if cohort.get("n", 0) == 0:
                    continue
                print(
                    f"  {name:28s} n={cohort['n']:5d} unique={cohort['unique_scores']:5d} "
                    f"({cohort['unique_ratio']:6.1%})  H={cohort['score_entropy']:.3f}  "
                    f"sd={cohort['score_std']:.5f}  IQR={cohort['score_iqr']:.5f}  "
                    f"rank-AUC={cohort['ranking_lift_auc']:.3f}  [{cohort['status']}]"
                )
                for warning in cohort.get("warnings", []):
                    print(f"      ! {warning}")
    print(f"\nwrote {OUT_PATH.relative_to(ROOT)}")

    collapsed = [p for p, v in report["plants"].items() if v.get("status") == "COLLAPSED"]
    return 1 if (collapsed and args.fail_on_collapse) else 0


if __name__ == "__main__":
    sys.exit(main())
