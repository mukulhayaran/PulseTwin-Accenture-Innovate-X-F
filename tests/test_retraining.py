"""mlops/retrainer.py end-to-end: champion-challenger training against the real
committed simulated data. Each run genuinely retrains a HistGradientBoostingClassifier
(offline/ml/train.py), so this file is slower than the rest of the suite -- that is the
honest cost of testing "does retraining actually work" rather than mocking it away.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

if not (ROOT / "data" / "plant_a" / "features.parquet").exists():
    pytest.skip("simulated data not generated -- run `make generate train`",
                allow_module_level=True)

os.environ.setdefault("OMP_NUM_THREADS", "1")


@pytest.fixture()
def isolated_ops_db(tmp_path, monkeypatch):
    """Retraining writes to model_registry -- isolate it per test run so this file
    doesn't accumulate state in the shared dev tempdir database across pytest runs."""
    db_path = tmp_path / "pulsetwin_ops_test.db"
    monkeypatch.setenv("PULSETWIN_OPS_DB", str(db_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("PULSETWIN_JOBS_DIR", str(tmp_path))
    yield db_path


def test_first_run_bootstraps_and_promotes(isolated_ops_db):
    from mlops import model_registry
    from mlops.retrainer import run

    result = run("configs/plant_a.yaml", job_id="test-bootstrap")
    assert result["status"] == "complete"
    assert result["report"]["recommendation"] == "PROMOTE"
    assert result["report"]["champion"] is None  # nothing to compare against yet

    champion = model_registry.get_champion("plant_a")
    assert champion is not None
    assert champion["model_id"] == result["challenger_id"]
    assert champion["status"] == "champion"


def test_second_run_produces_a_real_comparison(isolated_ops_db):
    from mlops import model_registry
    from mlops.retrainer import run

    run("configs/plant_a.yaml")  # bootstrap
    result = run("configs/plant_a.yaml", job_id="test-compare")

    report = result["report"]
    assert report["champion"] is not None
    assert report["recommendation"] in ("PROMOTE", "REJECT")
    assert report["requires_human_approval"] is True
    for key in ("beats_pr_auc", "beats_precision_at_budget", "beats_or_matches_calibration"):
        assert key in report["criteria"]

    challenger = model_registry.get(result["challenger_id"])
    assert challenger["status"] in ("challenger", "champion")


def test_job_status_file_is_written(isolated_ops_db, tmp_path):
    from mlops.retrainer import run

    run("configs/plant_a.yaml", job_id="test-job-status")
    status_file = tmp_path / "pulsetwin_retrain_jobs" / "test-job-status.json"
    assert status_file.exists()
    payload = json.loads(status_file.read_text(encoding="utf-8"))
    assert payload["status"] == "complete"
    assert payload["plant_id"] == "plant_a"


def test_promote_and_rollback_round_trip(isolated_ops_db):
    from mlops import model_registry
    from mlops.retrainer import run

    run("configs/plant_a.yaml")   # champion v1
    result = run("configs/plant_a.yaml")  # challenger v2
    challenger_id = result["challenger_id"]

    model_registry.promote(challenger_id, promoted_by="test")
    champion = model_registry.get_champion("plant_a")
    assert champion["model_id"] == challenger_id

    rolled_back = model_registry.rollback("plant_a")
    assert rolled_back["rolled_back_to"] != challenger_id
    champion_after = model_registry.get_champion("plant_a")
    assert champion_after["model_id"] == rolled_back["rolled_back_to"]
