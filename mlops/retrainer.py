"""Champion-challenger retraining pipeline.

    python -m mlops.retrainer --config configs/plant_a.yaml [--job-id ID]

1. Train a challenger on the SAME featurised data the champion trained on, using a
   deliberately different hyperparameter set (CHALLENGER_HYPERPARAMS below) -- CLAUDE.md's
   "seed 42 everywhere" rules out manufacturing a difference by varying random_state, so
   the difference has to be a real, documented configuration choice instead (see
   docs/MLOPS_LIFECYCLE.md). `output_suffix="_challenger"` (offline/ml/train.run) means
   this never overwrites the champion's own model.pkl / metrics.json / scores.parquet.
2. Export it to .npz (offline/export_model.export, the same bridge build_artifact.py
   uses) and register it in `model_registry` as a challenger.
3. Compare it against the current champion (mlops/evaluation.compare) and record a
   PROMOTE/REJECT recommendation. First-ever run for a plant has no champion to compare
   against, so it bootstraps by promoting unconditionally.
4. If --job-id is given (serve/routers/admin.py sets one when a human triggers this over
   the API), the full result is ALSO written as a status file under a runtime jobs
   directory, so the thin serve/ layer can poll progress without importing anything from
   mlops/ -- the import wall (CLAUDE.md SS2) does not allow it to.

Triggered manually or on a schedule -- never on every prediction (finals brief SS5,
anti-patterns: no Airflow/Kubeflow/MLflow, a plain script that runs end-to-end is enough).
mlops/ is offline-tier and may use pandas/sklearn transitively through offline.ml.train.
"""

from __future__ import annotations

import argparse
import json
import os
import pickle
import tempfile
import time
from pathlib import Path

from offline.config_schema import load_plant_config
from offline.export_model import export
from offline.ml import train as train_module
from offline.ml.defect_model import DEFAULT_HYPERPARAMS

from mlops import model_registry
from mlops.evaluation import compare

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"

CHALLENGER_HYPERPARAMS = {
    **DEFAULT_HYPERPARAMS,
    "max_iter": 450,           # more boosting rounds
    "max_leaf_nodes": 23,      # shallower trees per round
    "l2_regularization": 0.5,  # less shrinkage, given the smaller leaves
}


def _jobs_dir() -> Path:
    base = Path(os.environ.get("PULSETWIN_JOBS_DIR", tempfile.gettempdir()))
    out = base / "pulsetwin_retrain_jobs"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _write_job_status(job_id: str, status: dict) -> None:
    (_jobs_dir() / f"{job_id}.json").write_text(
        json.dumps(status, indent=2, default=float), encoding="utf-8"
    )


def run(config_path: str, job_id: str | None = None) -> dict:
    cfg = load_plant_config(config_path)
    pid = cfg.plant_id
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    challenger_id = f"{pid}_defect_v{int(time.time())}"

    if job_id:
        _write_job_status(job_id, {"status": "running", "plant_id": pid,
                                    "started_at": started_at})

    try:
        challenger_metrics = train_module.run(
            config_path, hyperparams=CHALLENGER_HYPERPARAMS, output_suffix="_challenger",
        )

        d = train_module.DATA_ROOT / pid
        with (d / "model_challenger.pkl").open("rb") as fh:
            bundle = pickle.load(fh)

        npz_path = ARTIFACTS / f"model_{pid}_challenger.npz"
        export(bundle["model"], npz_path, bundle["feature_names"])

        model_registry.register(
            model_id=challenger_id, model_type="defect_risk", version=int(time.time()),
            plant_id=pid, trained_at=challenger_metrics["trained_at"],
            training_data_range=f"n_train={challenger_metrics['n_train']}, "
                                 f"n_validate={challenger_metrics['n_validate']}, "
                                 f"n_test={challenger_metrics['n_test']}",
            metrics=challenger_metrics, artifact_path=str(npz_path), status="challenger",
            notes=f"hyperparams={CHALLENGER_HYPERPARAMS}",
        )

        champion = model_registry.get_champion(pid)
        if champion is None:
            model_registry.promote(challenger_id, promoted_by="system_bootstrap")
            report = {
                "champion": None,
                "challenger": {"model_id": challenger_id, "pr_auc": challenger_metrics["pr_auc"]},
                "recommendation": "PROMOTE", "requires_human_approval": False,
                "note": "no prior champion registered for this plant -- bootstrapped",
            }
        else:
            report = compare(champion["metrics"], challenger_metrics,
                              champion["model_id"], challenger_id)

        result = {
            "status": "complete", "plant_id": pid, "challenger_id": challenger_id,
            "started_at": started_at, "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "report": report,
        }
        if job_id:
            _write_job_status(job_id, result)
        return result

    except Exception as e:
        model_registry.mark_failed(challenger_id, notes=str(e)[:500])
        result = {"status": "failed", "plant_id": pid, "challenger_id": challenger_id,
                  "started_at": started_at, "error": str(e)}
        if job_id:
            _write_job_status(job_id, result)
        raise


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--job-id", default=None)
    args = ap.parse_args()
    result = run(args.config, args.job_id)

    print(f"=== {result['plant_id']} retrain: {result['status']} ===")
    if result["status"] == "complete":
        r = result["report"]
        print(f"  challenger {result['challenger_id']}")
        print(f"  recommendation: {r['recommendation']}")
        if r.get("champion"):
            print(f"  champion pr_auc={r['champion']['pr_auc']:.3f}  "
                  f"challenger pr_auc={r['challenger']['pr_auc']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
