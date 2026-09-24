"""Dataset adapter tests -- AI4I against a real (small) slice of the actual UCI release,
Bosch against a SYNTHETIC fixture shaped like the real thing (line/station/feature-encoded
column names, genuine sparsity, low positive rate) because the real dataset needs Kaggle
authentication this test suite must not depend on. Neither test needs network access.

`tests/fixtures/ai4i_sample.csv` is 300 real rows sampled from the actual UCI release
(`benchmark/adapters/ai4i.py`'s own download target), not fabricated.
`tests/fixtures/bosch_sample.csv` is synthetic and must never be mistaken for real Bosch
data -- see its generation comment in this repo's history for exactly how it was built.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from benchmark import registry, train as bt
from benchmark.adapters.ai4i import AI4IAdapter
from benchmark.adapters.bosch import BoschAdapter, parse_feature_name

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture
def ai4i_adapter(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "DATA_DIR", tmp_path)
    adapter = AI4IAdapter(registry.AI4I)
    adapter.config.raw_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "ai4i_sample.csv", adapter.config.raw_path)
    return adapter


@pytest.fixture
def bosch_adapter(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "DATA_DIR", tmp_path)
    adapter = BoschAdapter(registry.BOSCH, chunk_rows=137)  # force multiple chunks
    adapter.config.raw_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "bosch_sample.csv", adapter.config.raw_path)
    return adapter


# --------------------------------------------------------------------------------- AI4I
def test_ai4i_download_is_a_noop_when_raw_already_present(ai4i_adapter) -> None:
    before = ai4i_adapter.config.raw_path.read_bytes()
    ai4i_adapter.download()  # must not touch the network or overwrite the fixture
    assert ai4i_adapter.config.raw_path.read_bytes() == before


def test_ai4i_validate_raw_passes_on_the_real_fixture(ai4i_adapter) -> None:
    ai4i_adapter.validate_raw()  # raises on failure


def test_ai4i_load_matches_fixture_shape(ai4i_adapter) -> None:
    df = ai4i_adapter.load()
    assert len(df) == 300
    assert "Machine failure" in df.columns


def test_ai4i_stratified_sampling_never_drops_the_positive_class(ai4i_adapter) -> None:
    df = ai4i_adapter.load(sample_size=60)
    assert df["Machine failure"].sum() > 0, "a naive sample could have lost all positives"
    assert len(df) <= 60


def test_ai4i_normalize_populates_real_semantic_fields_but_not_line_station(ai4i_adapter) -> None:
    df = ai4i_adapter.load()
    out = ai4i_adapter.normalize(df)
    assert out["torque"].notna().all()
    assert out["temperature"].notna().all()
    assert out["dataset_origin"].eq("ai4i").all()
    # AI4I has no production-line structure -- these must stay NA, not fabricated.
    assert out["line_id"].isna().all()
    assert out["station_id"].isna().all()


def test_ai4i_feature_matrix_is_all_numeric(ai4i_adapter) -> None:
    df = ai4i_adapter.load()
    X, y = ai4i_adapter.feature_matrix(df)
    assert X.select_dtypes(exclude="number").empty
    assert set(np.unique(y)) <= {0, 1}


def test_ai4i_split_is_stratified_and_covers_every_row(ai4i_adapter) -> None:
    df = ai4i_adapter.load()
    tr, va, te = ai4i_adapter.create_train_test_split(df, strategy="stratified")
    assert len(tr) + len(va) + len(te) == len(df)
    for part in (tr, va, te):
        assert part["Machine failure"].sum() > 0, "every split should keep some positives"


def test_ai4i_rejects_an_unsupported_split_strategy(ai4i_adapter) -> None:
    df = ai4i_adapter.load()
    with pytest.raises(ValueError, match="stratified"):
        ai4i_adapter.create_train_test_split(df, strategy="chronological")


def test_ai4i_end_to_end_training_produces_real_metrics(ai4i_adapter) -> None:
    df = ai4i_adapter.load()
    tr, va, te = ai4i_adapter.create_train_test_split(df, strategy="stratified")
    Xtr, ytr = ai4i_adapter.feature_matrix(tr)
    Xva, yva = ai4i_adapter.feature_matrix(va)
    Xte, yte = ai4i_adapter.feature_matrix(te)
    fs = bt.build_feature_set(Xtr, ytr, Xva, yva, Xte, yte)
    model, importance, threshold_result = bt.train_and_evaluate(fs)
    assert 0.0 <= model.metrics["pr_auc"] <= 1.0
    assert 0.0 <= threshold_result["precision"] <= 1.0
    assert "confusion_matrix" in threshold_result
    assert importance  # feature importance is non-empty


def test_ai4i_metadata_reflects_the_loaded_data(ai4i_adapter) -> None:
    meta = ai4i_adapter.get_metadata()
    assert meta.n_rows == 300
    assert meta.target_prevalence == pytest.approx(15 / 300)


# -------------------------------------------------------------------------------- Bosch
def test_parse_feature_name_recovers_line_station_feature() -> None:
    assert parse_feature_name("L3_S36_F3939") == {
        "line_id": "L3", "station_id": "S36", "feature_id": "F3939"}
    assert parse_feature_name("Id") is None
    assert parse_feature_name("Response") is None


def test_bosch_download_raises_manual_instruction_when_raw_is_missing(tmp_path, monkeypatch) -> None:
    from benchmark.adapters.base import ManualDownloadRequired

    monkeypatch.setattr(registry, "DATA_DIR", tmp_path)
    adapter = BoschAdapter(registry.BOSCH)
    with pytest.raises(ManualDownloadRequired, match="Kaggle"):
        adapter.download()


def test_bosch_validate_raw_passes_on_the_synthetic_fixture(bosch_adapter) -> None:
    bosch_adapter.validate_raw()


def test_bosch_chunked_load_reads_every_row_across_multiple_chunks(bosch_adapter) -> None:
    """chunk_rows=137 against a 600-row fixture forces 5 chunks -- this is the thing
    that would silently truncate to one chunk's worth of data if the chunking loop had
    an off-by-one or an early break."""
    df = bosch_adapter.load()
    assert len(df) == 600


def test_bosch_stratified_sample_never_drops_the_positive_class(bosch_adapter) -> None:
    df = bosch_adapter.load(sample_size=200)
    assert df["Response"].sum() > 0
    assert len(df) <= 200


def test_bosch_sample_is_cached_to_parquet(bosch_adapter) -> None:
    bosch_adapter.load(sample_size=150)
    cache = bosch_adapter._parquet_cache_path(150)
    assert cache.exists()
    # A second load must hit the cache, not re-scan the CSV -- corrupt the raw file to
    # prove the cache path is actually taken, not just present.
    bosch_adapter.config.raw_path.write_text("not a valid csv at all")
    df2 = bosch_adapter.load(sample_size=150)
    assert len(df2) <= 150  # succeeded despite the raw file now being garbage


def test_bosch_normalize_leaves_every_physical_signal_field_null(bosch_adapter) -> None:
    """The core factual claim of this adapter: Bosch's anonymized features cannot
    populate torque/temperature/vibration/pressure, ever."""
    df = bosch_adapter.load()
    out = bosch_adapter.normalize(df)
    for field in ("torque", "temperature", "vibration", "pressure"):
        assert out[field].isna().all(), f"{field} should be NULL for every Bosch row"
    assert out["dataset_origin"].eq("bosch").all()
    assert out["sensor_coverage"].between(0, 1).all()


def test_bosch_feature_matrix_only_uses_parsed_columns(bosch_adapter) -> None:
    df = bosch_adapter.load()
    X, y = bosch_adapter.feature_matrix(df)
    assert "Id" not in X.columns and "Response" not in X.columns
    assert all(parse_feature_name(c) for c in X.columns)
    assert set(np.unique(y)) <= {0, 1}


def test_bosch_station_of_matches_feature_matrix_columns(bosch_adapter) -> None:
    df = bosch_adapter.load()
    X, _y = bosch_adapter.feature_matrix(df)
    station_of = bosch_adapter.station_of(df)
    assert set(X.columns) <= set(station_of)


def test_bosch_end_to_end_training_handles_real_sparsity(bosch_adapter) -> None:
    """HistGradientBoostingClassifier natively handles NaN -- no imputation step should
    be required before this runs."""
    df = bosch_adapter.load()
    tr, va, te = bosch_adapter.create_train_test_split(df, strategy="stratified")
    Xtr, ytr = bosch_adapter.feature_matrix(tr)
    Xva, yva = bosch_adapter.feature_matrix(va)
    Xte, yte = bosch_adapter.feature_matrix(te)
    fs = bt.build_feature_set(Xtr, ytr, Xva, yva, Xte, yte)
    model, _importance, threshold_result = bt.train_and_evaluate(fs)
    assert 0.0 <= model.metrics["pr_auc"] <= 1.0
    assert "confusion_matrix" in threshold_result
