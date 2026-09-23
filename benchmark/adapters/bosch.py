"""Bosch Production Line Performance -- the headline real dataset.

Kaggle competition data (`bosch-production-line-performance`), ~1.18M rows x 970 columns
in the standard numeric release, ~0.58% positive rate. Requires Kaggle authentication to
fetch, so `download()` never attempts to scrape it -- it detects a missing raw file and
raises `ManualDownloadRequired` with the exact source URL and expected path (§40 of the
source brief, and CLAUDE.md's "no secrets committed" rule: this adapter never asks for or
stores a Kaggle API key).

ALL ~968 numeric feature columns are anonymized: `L3_S36_F3939` encodes line 3, station
36, feature 3939 by POSITION, never by physical meaning. `_parse_feature_name()` recovers
that structure; nothing recovers what the feature physically measures, because nothing
in the released data says. `normalize()` therefore leaves every physical-signal canonical
column (torque/temperature/vibration/pressure) as `pd.NA` for every Bosch row -- this is
the concrete instance `registry.py::BOSCH.limitations` documents, not a bug to fix later.

Loading is two-pass and chunked, never `pd.read_csv(huge_file)`:
  pass 1: read ONLY Id + Response (2 of ~970 columns) to build a stratified sample of Ids.
  pass 2: chunked read of the full file, keeping only rows whose Id was selected.
This scans the file once per pass but never holds more than a chunk plus the selected
sample in memory, and pass 1 alone is enough to compute exact class counts for stratified
sampling without guessing.

Sampled results are cached to Parquet (`processed_dir`) keyed by sample size, so a second
run with the same --sample-size never re-scans the raw CSV (§28 caching).
"""

from __future__ import annotations

import re
from typing import Iterator

import numpy as np
import pandas as pd

from benchmark.adapters.base import DatasetAdapter, DatasetMetadata, ManualDownloadRequired

_FEATURE_RE = re.compile(r"^L(\d+)_S(\d+)_F(\d+)$")

# Bosch's known standard shape (train_numeric.csv). Used only to sanity-check a raw file
# looks like what this adapter targets, never hardcoded into the parsing logic itself.
EXPECTED_ID_COL = "Id"
EXPECTED_TARGET_COL = "Response"
CHUNK_ROWS = 50_000


def parse_feature_name(col: str) -> dict[str, str] | None:
    """`L3_S36_F3939` -> {'line_id': 'L3', 'station_id': 'S36', 'feature_id': 'F3939'}.

    Structural only -- this is POSITION metadata (which line/station a sensor sits at),
    never a physical-quantity claim. Returns None for Id/Response/anything else.
    """
    m = _FEATURE_RE.match(col)
    if not m:
        return None
    line, station, feature = m.groups()
    return {"line_id": f"L{line}", "station_id": f"S{station}", "feature_id": f"F{feature}"}


class BoschAdapter(DatasetAdapter):

    def __init__(self, config, *, chunk_rows: int = CHUNK_ROWS) -> None:
        super().__init__(config)
        # Overridable so tests can force a genuinely multi-chunk read against a small
        # fixture, rather than only ever exercising the single-chunk path.
        self.chunk_rows = chunk_rows

    def download(self) -> None:
        if self.config.raw_path.exists():
            return
        raise ManualDownloadRequired(
            f"Bosch Production Line Performance requires a Kaggle account and accepted "
            f"competition rules -- this cannot be automated (CLAUDE.md: no secrets "
            f"committed). Download train_numeric.csv.zip from "
            f"{self.config.source_url}, unzip it, and place train_numeric.csv at "
            f"{self.config.raw_path}"
        )

    def validate_raw(self) -> None:
        if not self.config.raw_path.exists():
            raise FileNotFoundError(
                f"{self.config.raw_path} missing -- see download() for the manual "
                f"download instruction.")
        header = pd.read_csv(self.config.raw_path, nrows=0).columns.tolist()
        if EXPECTED_ID_COL not in header or EXPECTED_TARGET_COL not in header:
            raise ValueError(
                f"{self.config.raw_path} is missing {EXPECTED_ID_COL!r}/"
                f"{EXPECTED_TARGET_COL!r} -- this does not look like Bosch's "
                f"train_numeric.csv.")
        feature_cols = [c for c in header if parse_feature_name(c)]
        if not feature_cols:
            raise ValueError(
                f"{self.config.raw_path} has no L#_S#_F# columns -- this does not look "
                f"like Bosch's train_numeric.csv.")

    def _parquet_cache_path(self, sample_size: int | None):
        tag = "full" if sample_size is None else f"sample{sample_size}"
        return self.config.processed_dir / f"train_numeric_{tag}.parquet"

    def _stratified_ids(self, sample_size: int, *, seed: int = 42) -> set:
        """Pass 1: Id + Response only -- cheap even against the full 1.18M-row file."""
        idr = pd.read_csv(
            self.config.raw_path,
            usecols=[EXPECTED_ID_COL, EXPECTED_TARGET_COL],
            dtype={EXPECTED_ID_COL: np.int64, EXPECTED_TARGET_COL: np.int8},
        )
        pos = idr[idr[EXPECTED_TARGET_COL] == 1]
        neg = idr[idr[EXPECTED_TARGET_COL] == 0]
        total = len(idr)
        pos_n = max(1, round(sample_size * len(pos) / total)) if len(pos) else 0
        neg_n = max(1, sample_size - pos_n)
        rng = np.random.default_rng(seed)
        pos_ids = pos[EXPECTED_ID_COL].sample(
            n=min(pos_n, len(pos)), random_state=seed).tolist() if len(pos) else []
        neg_ids = neg[EXPECTED_ID_COL].sample(
            n=min(neg_n, len(neg)), random_state=seed).tolist()
        return set(pos_ids) | set(neg_ids)

    def _chunks(self) -> Iterator[pd.DataFrame]:
        return pd.read_csv(self.config.raw_path, chunksize=self.chunk_rows)

    def load(self, sample_size: int | None = None) -> pd.DataFrame:
        # Cache check FIRST. Caching (§28) means "reuse extracted features without
        # touching the source again" -- validating the raw file before the cache check
        # would defeat that: a cached sample would become unusable the moment the raw
        # CSV was archived, moved, or deleted, which is exactly the situation caching is
        # supposed to make safe. Only fall through to validate_raw() when actually about
        # to read the raw file.
        cache = self._parquet_cache_path(sample_size)
        if cache.exists():
            return pd.read_parquet(cache)

        self.validate_raw()
        if sample_size is None:
            frames = list(self._chunks())
            df = pd.concat(frames, ignore_index=True)
        else:
            wanted_ids = self._stratified_ids(sample_size)
            kept = []
            for chunk in self._chunks():
                match = chunk[chunk[EXPECTED_ID_COL].isin(wanted_ids)]
                if len(match):
                    kept.append(match)
            df = (pd.concat(kept, ignore_index=True) if kept
                  else pd.DataFrame(columns=[EXPECTED_ID_COL, EXPECTED_TARGET_COL]))

        self.config.processed_dir.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cache, index=False)
        return df

    def _feature_columns(self, df: pd.DataFrame) -> list[str]:
        return [c for c in df.columns if parse_feature_name(c)]

    def normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        feature_cols = self._feature_columns(df)
        # One row of normalized output per Bosch row, but line/station is AMBIGUOUS at
        # row granularity (a unit touches many stations, one per feature column) -- report
        # the line/station of the feature with the most non-null coverage as a
        # representative value, and say so plainly rather than picking one silently.
        parsed = [parse_feature_name(c) for c in feature_cols]
        non_null_counts = df[feature_cols].notna().sum() if feature_cols else pd.Series(dtype=int)
        rep_col = non_null_counts.idxmax() if len(non_null_counts) else None
        rep = parse_feature_name(rep_col) if rep_col else None

        n = len(df)
        out = pd.DataFrame({
            "timestamp": pd.NA,                     # no usable production-order timestamp
            "plant_id": pd.NA,
            "line_id": rep["line_id"] if rep else pd.NA,
            "station_id": rep["station_id"] if rep else pd.NA,
            "equipment_id": pd.NA,
            "vehicle_id": df.get(EXPECTED_ID_COL, pd.Series([pd.NA] * n)),
            "vehicle_model": pd.NA, "operator_id": pd.NA, "shift": pd.NA,
            "part_batch": pd.NA, "supplier": pd.NA,
            "cycle_time": pd.NA, "target_cycle_time": pd.NA,
            # Every physical-signal field NULL -- see module docstring and
            # registry.py::BOSCH.limitations. Not a gap; a fact about this dataset.
            "temperature": pd.NA, "pressure": pd.NA, "torque": pd.NA,
            "vibration": pd.NA, "current": pd.NA,
            "machine_health": pd.NA,
            "queue_length": pd.NA, "throughput": pd.NA, "downtime": pd.NA,
            "sensor_coverage": (
                df[feature_cols].notna().mean(axis=1) if feature_cols
                else pd.Series([pd.NA] * n)
            ),
            "quality_status": pd.NA, "defect_type": pd.NA,
            "failure_status": df.get(EXPECTED_TARGET_COL, pd.Series([pd.NA] * n)).astype(
                "boolean") if EXPECTED_TARGET_COL in df.columns else pd.NA,
            "failure_type": pd.NA,
            "anomaly_score": pd.NA, "bottleneck_probability": pd.NA,
            "defect_probability": pd.NA,
            "source_type": "measured",
            "confidence": 1.0,
            "dataset_origin": self.dataset_id,
        })
        return out

    def feature_matrix(self, df: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
        """Every parsed L#_S#_F# column, as-is -- Bosch's real feature space, not the
        (mostly-NULL, for this dataset) canonical schema. NaN is left in place:
        HistGradientBoostingClassifier natively handles missing values."""
        feature_cols = self._feature_columns(df)
        X = df[feature_cols].astype(float)
        y = df[self.config.target_column].to_numpy(dtype=np.int64)
        return X, y

    def station_of(self, df: pd.DataFrame) -> dict[str, str]:
        """column -> station_id, for `benchmark/masking.py`'s STATION_LEVEL strategy."""
        out = {}
        for c in self._feature_columns(df):
            parsed = parse_feature_name(c)
            if parsed:
                out[c] = parsed["station_id"]
        return out

    def get_metadata(self) -> DatasetMetadata:
        df = self.load(sample_size=None) if self.config.raw_path.exists() else pd.DataFrame()
        n_rows = len(df)
        n_features = len(self._feature_columns(df)) if n_rows else 0
        prevalence = (
            float(df[self.config.target_column].mean())
            if n_rows and self.config.target_column in df.columns else None
        )
        return DatasetMetadata(
            dataset_id=self.dataset_id, name=self.config.name, domain=self.config.domain,
            n_rows=n_rows, n_features=n_features, target_column=self.config.target_column,
            target_prevalence=prevalence, tasks=self.get_supported_tasks(),
            limitations=self.config.limitations,
            supports_time_series=self.config.supports_time_series,
            supports_missing_data=self.config.supports_missing_data,
        )

    def create_train_test_split(
        self, df: pd.DataFrame, *, strategy: str = "stratified", seed: int = 42
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """No usable production-order timestamp in the standard release (see
        DatasetConfig.limitations) -- 'chronological' is refused rather than silently
        downgraded to something that looks safer than it is."""
        if strategy != "stratified":
            raise ValueError(
                f"Bosch has no usable production-order timestamp; only "
                f"strategy='stratified' is supported (got {strategy!r}).")
        rng = np.random.default_rng(seed)
        y = df[self.config.target_column].to_numpy()
        idx = np.arange(len(df))
        tr_idx, va_idx, te_idx = [], [], []
        for cls in (0, 1):
            cls_idx = idx[y == cls]
            rng.shuffle(cls_idx)
            n = len(cls_idx)
            n_tr = int(n * 0.7)
            n_va = int(n * 0.15)
            tr_idx.extend(cls_idx[:n_tr])
            va_idx.extend(cls_idx[n_tr:n_tr + n_va])
            te_idx.extend(cls_idx[n_tr + n_va:])
        return (
            df.iloc[sorted(tr_idx)].reset_index(drop=True),
            df.iloc[sorted(va_idx)].reset_index(drop=True),
            df.iloc[sorted(te_idx)].reset_index(drop=True),
        )
