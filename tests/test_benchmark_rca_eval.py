"""RCA evaluation against a tiny fixture DB/config/inspections file -- not the real
several-GB artifact. `stations_named_by_factors` (U029528, real data, checked by hand
before this module was written) is what proves the ranking logic is right; these tests
pin the harness around it: ground-truth station extraction, the flagged-subset
restriction, and the hit-rate arithmetic.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest
import yaml

from benchmark.rca_eval import (
    _ground_truth_stations,
    _ground_truth_units,
    run_deterministic_eval,
    stations_named_by_factors,
)

UNIT_FACTORS_DDL = """
CREATE TABLE unit_factors (
    unit_id TEXT, plant_id TEXT, rank INTEGER, feature TEXT, direction TEXT,
    contribution REAL, value REAL, plain_language TEXT,
    PRIMARY KEY (unit_id, plant_id, rank))
"""

TINY_CONFIG = {
    "stations": [
        {"id": 3, "name": "Structural Fastening A", "defect_role": "mode_c_torque"},
        {"id": 8, "name": "Vibration-Critical Weld", "defect_role": "mode_c_vibration"},
        {"id": 15, "name": "Paint Surface Prep", "defect_role": "mode_b_origin"},
        {"id": 17, "name": "Clear Coat Application", "defect_role": "mode_a_contributor"},
        {"id": 18, "name": "Cure Oven", "defect_role": "mode_a_contributor"},
        {"id": 1, "name": "Frame Load"},  # no defect_role -- must be ignored
    ]
}


@pytest.fixture
def config_path(tmp_path) -> Path:
    p = tmp_path / "plant_test.yaml"
    p.write_text(yaml.safe_dump(TINY_CONFIG), encoding="utf-8")
    return p


@pytest.fixture
def db_path(tmp_path) -> Path:
    """A minimal SQLite file shaped like the real artifact's unit_factors table for
    three units: a clean mode_b hit, a mode_a case where the top factor is the
    correlated mode_b station (the real U029526-style pattern), and one unflagged unit
    that must never appear in the flagged subset."""
    p = tmp_path / "tiny_artifact.db"
    con = sqlite3.connect(p)
    con.execute(UNIT_FACTORS_DDL)
    rows = [
        ("U1", "plant_test", 0, "s15_checklist_time_s", "high", 0.5, 20.0, ""),
        ("U1", "plant_test", 1, "paint_buffer_dwell_s", "high", 0.3, 600.0, ""),
        ("U2", "plant_test", 0, "s15_checklist_time_s", "high", 0.6, 22.0, ""),
        ("U2", "plant_test", 1, "paint_buffer_dwell_s", "high", 0.4, 650.0, ""),
        ("U3", "plant_test", 0, "s3_torque_nm_dev_median", "low", 0.7, -3.0, ""),
        ("U3", "plant_test", 1, "s8_vibration_mm_s_roc", "high", 0.5, 1.2, ""),
    ]
    con.executemany(
        "INSERT INTO unit_factors VALUES (?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return p


@pytest.fixture
def insp_path(tmp_path) -> Path:
    p = tmp_path / "inspections.parquet"
    df = pd.DataFrame([
        # U1: mode_b, flagged (has unit_factors rows) -- rank-0 IS the mode_b station.
        {"unit_id": "U1", "true_defect": 1, "defect_mode": "mode_b"},
        # U2: mode_a, flagged -- rank-0 is the CORRELATED mode_b station (S15), true
        # mode_a cause (paint dwell) only shows up at rank-1. Top-1 must miss, top-3 hit.
        {"unit_id": "U2", "true_defect": 1, "defect_mode": "mode_a"},
        # U3: mode_c, flagged, both contributing stations correctly ranked.
        {"unit_id": "U3", "true_defect": 1, "defect_mode": "mode_c"},
        # U4: a real mode_b defect that was NEVER flagged (no unit_factors rows) --
        # must be excluded from the evaluated sample entirely, not counted as a miss.
        {"unit_id": "U4", "true_defect": 1, "defect_mode": "mode_b"},
        # U5: not a real defect at all -- must never enter the ground-truth pool.
        {"unit_id": "U5", "true_defect": 0, "defect_mode": ""},
    ])
    df.to_parquet(p, index=False)
    return p


# ---------------------------------------------------------------- ground-truth stations
def test_ground_truth_stations_reads_defect_role_from_config(config_path) -> None:
    gt = _ground_truth_stations(config_path)
    assert gt["mode_a"] == {17, 18}
    assert gt["mode_b"] == {15}
    assert gt["mode_c"] == {3, 8}


def test_stations_without_defect_role_are_ignored(config_path) -> None:
    gt = _ground_truth_stations(config_path)
    assert 1 not in gt["mode_a"] | gt["mode_b"] | gt["mode_c"]


# ------------------------------------------------------------ stations_named_by_factors
def test_named_stations_follow_the_s_prefix_convention() -> None:
    factors = [
        {"rank": 0, "feature": "s15_checklist_time_s"},
        {"rank": 1, "feature": "s3_torque_nm_dev_median"},
    ]
    assert stations_named_by_factors(factors) == [15, 3]


def test_paint_buffer_dwell_implicates_both_mode_a_stations() -> None:
    factors = [{"rank": 0, "feature": "paint_buffer_dwell_s"}]
    assert stations_named_by_factors(factors) == [17, 18]


def test_named_stations_are_deduplicated_preserving_first_rank() -> None:
    factors = [
        {"rank": 0, "feature": "s15_checklist_time_s"},
        {"rank": 1, "feature": "s15_something_else"},
    ]
    assert stations_named_by_factors(factors) == [15]


def test_unparseable_feature_names_are_skipped_not_fatal() -> None:
    factors = [{"rank": 0, "feature": "some_global_feature_with_no_station"}]
    assert stations_named_by_factors(factors) == []


# -------------------------------------------------------------------- ground_truth_units
def test_ground_truth_units_excludes_unflagged_and_non_defects(insp_path, db_path) -> None:
    from benchmark import rca_eval

    con, _ops, _backend = rca_eval.db.open_database(db_path)
    sample, coverage = _ground_truth_units("plant_test", n=10, seed=42, con=con,
                                           insp_path=insp_path)
    assert set(sample["unit_id"]) == {"U1", "U2", "U3"}
    assert "U4" not in set(sample["unit_id"])  # real defect, never flagged
    assert "U5" not in set(sample["unit_id"])  # not a real defect
    assert coverage["confirmed_defects_total"] == 4       # U1, U2, U3, U4
    assert coverage["confirmed_defects_flagged"] == 3     # U1, U2, U3
    assert coverage["flagged_rate"] == pytest.approx(0.75)


# ---------------------------------------------------------------------- end to end
def test_run_deterministic_eval_end_to_end(config_path, db_path, insp_path) -> None:
    summary, detail = run_deterministic_eval(
        plant_id="plant_test", n_units=10, seed=42,
        config_path=config_path, db_path=db_path, insp_path=insp_path,
    )
    by_unit = detail.set_index("unit_id").to_dict("index")

    assert by_unit["U1"]["top1_hit"] is True     # mode_b, clean rank-0 hit
    assert by_unit["U2"]["top1_hit"] is False    # mode_a, correlated rank-0 miss
    assert by_unit["U2"]["top3_hit"] is True     # ... but paint dwell rescues top-3
    assert by_unit["U3"]["top1_hit"] is True     # mode_c, both stations correctly ranked

    assert summary["n_units"] == 3
    assert summary["top1_hit_rate"] == pytest.approx(2 / 3)
    assert summary["top3_hit_rate"] == pytest.approx(1.0)
    assert summary["detection_coverage"]["confirmed_defects_total"] == 4
    assert summary["detection_coverage"]["confirmed_defects_flagged"] == 3


def test_run_deterministic_eval_raises_a_clear_error_when_nothing_is_flagged(
    config_path, tmp_path
) -> None:
    empty_insp = tmp_path / "empty_inspections.parquet"
    pd.DataFrame([{"unit_id": "Z1", "true_defect": 1, "defect_mode": "mode_a"}]).to_parquet(
        empty_insp, index=False)
    empty_db = tmp_path / "empty_artifact.db"
    con = sqlite3.connect(empty_db)
    con.execute(UNIT_FACTORS_DDL)
    con.commit()
    con.close()

    with pytest.raises(ValueError, match="no FLAGGED confirmed"):
        run_deterministic_eval(
            plant_id="plant_test", n_units=10, seed=42,
            config_path=config_path, db_path=empty_db, insp_path=empty_insp,
        )
