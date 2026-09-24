"""The common dataset adapter interface.

Trimmed from a larger proposed interface: `create_sensor_mask` and `get_ground_truth`
are deliberately NOT methods here. Masking is one reusable engine
(`benchmark/masking.py`) applied to any normalized dataframe, not per-adapter logic --
duplicating a masking implementation per adapter is exactly the "separate preprocessing
pipelines with duplicated business logic" this interface exists to avoid. RCA ground
truth lives in the simulator path (`benchmark/rca_eval.py`, against
`offline/simulator/causal_engine.py`), not in generic public-dataset adapters -- none of
the registered datasets carry real causal ground truth (see each DatasetConfig's
`limitations`), so a method promising one here would be misleading by its mere presence.

CANONICAL_COLUMNS mirrors the schema in the brief this subsystem was scoped from. Not
every dataset populates every column -- `normalize()` must set genuinely-absent columns
to `pd.NA`, never fabricate a value to fill the shape.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

    from benchmark.registry import DatasetConfig

CANONICAL_COLUMNS: tuple[str, ...] = (
    "timestamp", "plant_id", "line_id", "station_id", "equipment_id",
    "vehicle_id", "vehicle_model", "operator_id", "shift", "part_batch", "supplier",
    "cycle_time", "target_cycle_time", "temperature", "pressure", "torque",
    "vibration", "current",
    "machine_health", "queue_length", "throughput", "downtime",
    "sensor_coverage",
    "quality_status", "defect_type", "failure_status", "failure_type",
    "anomaly_score", "bottleneck_probability", "defect_probability",
    "source_type", "confidence",
    # Not in the original field list, but required to answer "which dataset did this
    # normalized row come from" once rows from different adapters can sit side by side.
    "dataset_origin",
)


@dataclass(frozen=True)
class DatasetMetadata:
    dataset_id: str
    name: str
    domain: str
    n_rows: int
    n_features: int
    target_column: str
    target_prevalence: float | None
    tasks: list[str]
    limitations: list[str]
    supports_time_series: bool
    supports_missing_data: bool


class DatasetAdapter(ABC):
    """One adapter per registered dataset. `benchmark/run.py` never branches on
    `dataset_id` -- it only ever calls these methods, so adding a dataset later never
    means editing the runner."""

    def __init__(self, config: "DatasetConfig") -> None:
        self.config = config

    @property
    def dataset_id(self) -> str:
        return self.config.dataset_id

    @abstractmethod
    def download(self) -> None:
        """Fetch the raw dataset if possible, or raise ManualDownloadRequired with a
        clear instruction. Must be a no-op (not a re-download) if the raw file already
        exists -- caching per §28 of the source brief."""

    @abstractmethod
    def validate_raw(self) -> None:
        """Cheap structural checks (expected columns/row-count order of magnitude)
        against the raw file, before any heavier loading work."""

    @abstractmethod
    def load(self, sample_size: int | None = None) -> "pd.DataFrame":
        """The raw dataframe, optionally sampled. `sample_size=None` means everything
        the adapter judges safe to load (for AI4I that's all 10k rows; for Bosch this
        must never mean the full multi-GB file by accident)."""

    @abstractmethod
    def normalize(self, df: "pd.DataFrame") -> "pd.DataFrame":
        """Map `df` into CANONICAL_COLUMNS. Absent fields are `pd.NA`, never fabricated.
        Every row gets `dataset_origin=self.dataset_id` and a `source_type` per contract
        #1 (measured/inferred/predicted)."""

    @abstractmethod
    def get_metadata(self) -> DatasetMetadata:
        """Computed from the loaded data, not hand-typed -- `n_rows`/`target_prevalence`
        must reflect what was actually loaded, or a report showing them is fabricating
        evidence exactly as CLAUDE.md forbids."""

    def get_target(self) -> str:
        return self.config.target_column

    def get_supported_tasks(self) -> list[str]:
        return list(self.config.tasks)

    @abstractmethod
    def create_train_test_split(
        self, df: "pd.DataFrame", *, strategy: str, seed: int = 42
    ) -> tuple["pd.DataFrame", "pd.DataFrame", "pd.DataFrame"]:
        """(train, validate, test), or raise ValueError for an unsupported strategy.
        Every adapter must document which strategy it actually uses and why in its own
        module docstring -- §17's "document the splitting strategy in every benchmark
        result" is enforced by `benchmark/report.py` reading this back, not by convention
        alone."""


class ManualDownloadRequired(Exception):
    """Raised by `download()` when a dataset needs authentication PulseTwin cannot
    automate (e.g. Bosch's Kaggle competition terms). The message IS the fix -- §40 of
    the source brief: detect cleanly, never fail mysteriously."""
