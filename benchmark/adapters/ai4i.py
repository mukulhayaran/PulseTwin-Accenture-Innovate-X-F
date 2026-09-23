"""AI4I 2020 Predictive Maintenance Dataset -- the pipeline smoke test.

10,000 rows describing ONE simulated milling machine: air/process temperature,
rotational speed, torque, tool wear, and a binary `Machine failure` target plus five
rule-derived failure-mode flags. Unlike Bosch/Scania/SECOM, its columns carry real
physical semantics (`Torque [Nm]` really is torque) -- this is the proof point that the
canonical schema works AT ALL when a dataset has semantics to map, before Bosch's adapter
has to demonstrate what happens when it doesn't (see `registry.py::BOSCH.limitations`).

TWO separate outputs, deliberately not conflated:
  * `normalize()`      -> the canonical cross-dataset schema (digital-twin/report use).
                           Most physical-signal fields ARE populated here (a contrast
                           with Bosch), but line/station/shift/operator fields stay NA --
                           AI4I has no production-line structure, only one machine.
  * `feature_matrix()`  -> AI4I's own real columns (Air/Process temperature, Rotational
                           speed, Torque, Tool wear), used to train the actual defect
                           model. The canonical schema is not a feature set; forcing
                           training onto only the 5-6 canonical columns that happen to
                           exist here would throw away real signal for no reason.
"""

from __future__ import annotations

import zipfile
from io import BytesIO
from urllib.request import urlopen

import numpy as np
import pandas as pd

from benchmark.adapters.base import DatasetAdapter, DatasetMetadata, ManualDownloadRequired

FAILURE_FLAGS = ("TWF", "HDF", "PWF", "OSF", "RNF")

RAW_COLUMNS = (
    "UDI", "Product ID", "Type", "Air temperature [K]", "Process temperature [K]",
    "Rotational speed [rpm]", "Torque [Nm]", "Tool wear [min]", "Machine failure",
    *FAILURE_FLAGS,
)

FEATURE_COLUMNS = (
    "Air temperature [K]", "Process temperature [K]", "Rotational speed [rpm]",
    "Torque [Nm]", "Tool wear [min]",
)


class AI4IAdapter(DatasetAdapter):

    def download(self) -> None:
        if self.config.raw_path.exists():
            return  # caching (§28): never re-download an existing raw file
        self.config.raw_dir.mkdir(parents=True, exist_ok=True)
        try:
            with urlopen(self.config.source_url, timeout=30) as resp:  # noqa: S310
                blob = resp.read()
        except OSError as exc:
            raise ManualDownloadRequired(
                f"Could not reach {self.config.source_url} ({exc}). Download the zip "
                f"by hand and place its ai4i2020.csv at {self.config.raw_path}."
            ) from exc
        with zipfile.ZipFile(BytesIO(blob)) as zf:
            with zf.open("ai4i2020.csv") as src, open(self.config.raw_path, "wb") as dst:
                dst.write(src.read())

    def validate_raw(self) -> None:
        if not self.config.raw_path.exists():
            raise FileNotFoundError(
                f"{self.config.raw_path} missing -- run download() first, or place the "
                f"file by hand (source: {self.config.source_url}).")
        header = pd.read_csv(self.config.raw_path, nrows=0).columns.tolist()
        missing = [c for c in RAW_COLUMNS if c not in header]
        if missing:
            raise ValueError(
                f"{self.config.raw_path} is missing expected columns {missing} -- this "
                f"does not look like the AI4I 2020 release this adapter targets.")

    def load(self, sample_size: int | None = None) -> pd.DataFrame:
        self.validate_raw()
        df = pd.read_csv(self.config.raw_path)
        if sample_size is not None and sample_size < len(df):
            # Stratified, not a plain head/sample: at ~3.4% positive rate a naive random
            # sample can land on zero positives and silently make PR-AUC undefined.
            pos = df[df[self.config.target_column] == 1]
            neg = df[df[self.config.target_column] == 0]
            pos_n = max(1, round(sample_size * len(pos) / len(df)))
            neg_n = max(1, sample_size - pos_n)
            df = pd.concat([
                pos.sample(n=min(pos_n, len(pos)), random_state=42),
                neg.sample(n=min(neg_n, len(neg)), random_state=42),
            ]).sort_values("UDI").reset_index(drop=True)
        return df

    def normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        failure_type = np.where(
            df["Machine failure"] == 1,
            df[list(FAILURE_FLAGS)].idxmax(axis=1).where(
                df[list(FAILURE_FLAGS)].sum(axis=1) > 0, "unspecified"),
            pd.NA,
        )
        out = pd.DataFrame({
            "timestamp": pd.NA,                                  # no production order
            "plant_id": pd.NA, "line_id": pd.NA, "station_id": pd.NA,
            # Real, not fabricated: AI4I's whole release is one simulated machine.
            "equipment_id": "ai4i_single_machine",
            "vehicle_id": df["Product ID"],
            "vehicle_model": df["Type"],                          # L/M/H quality variant
            "operator_id": pd.NA, "shift": pd.NA, "part_batch": pd.NA, "supplier": pd.NA,
            "cycle_time": pd.NA, "target_cycle_time": pd.NA,
            "temperature": df["Process temperature [K]"],
            "pressure": pd.NA,
            "torque": df["Torque [Nm]"],
            "vibration": pd.NA, "current": pd.NA,
            "machine_health": pd.NA,
            "queue_length": pd.NA, "throughput": pd.NA, "downtime": pd.NA,
            "sensor_coverage": 1.0,                                # no missing data in the release
            "quality_status": pd.NA,
            "defect_type": pd.NA,
            "failure_status": df["Machine failure"].astype(bool),
            "failure_type": failure_type,
            "anomaly_score": pd.NA, "bottleneck_probability": pd.NA,
            "defect_probability": pd.NA,
            "source_type": "measured",   # real experimental-rig readings, nothing inferred
            "confidence": 1.0,
            "dataset_origin": self.dataset_id,
        })
        return out

    def feature_matrix(self, df: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
        """AI4I's real columns as ML features -- NOT the canonical schema (see module
        docstring). `Type` is one-hot encoded; UDI/Product ID are identifiers, not
        features."""
        X = df[list(FEATURE_COLUMNS)].astype(float).copy()
        X = pd.concat([X, pd.get_dummies(df["Type"], prefix="type", dtype=float)], axis=1)
        y = df[self.config.target_column].to_numpy(dtype=np.int64)
        return X, y

    def get_metadata(self) -> DatasetMetadata:
        df = self.load()
        return DatasetMetadata(
            dataset_id=self.dataset_id, name=self.config.name, domain=self.config.domain,
            n_rows=len(df), n_features=len(FEATURE_COLUMNS) + 1,  # +1 for Type
            target_column=self.config.target_column,
            target_prevalence=float(df[self.config.target_column].mean()),
            tasks=self.get_supported_tasks(), limitations=self.config.limitations,
            supports_time_series=self.config.supports_time_series,
            supports_missing_data=self.config.supports_missing_data,
        )

    def create_train_test_split(
        self, df: pd.DataFrame, *, strategy: str = "stratified", seed: int = 42
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """No timestamp exists (see DatasetConfig.limitations), so the only defensible
        strategy is a seeded stratified split -- 'chronological' or 'group' are refused
        rather than silently downgraded to something that looks safer than it is."""
        if strategy != "stratified":
            raise ValueError(
                f"AI4I has no timestamp or group column; only strategy='stratified' is "
                f"supported (got {strategy!r}).")
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
