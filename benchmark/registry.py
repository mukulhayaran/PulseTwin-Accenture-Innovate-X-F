"""The dataset registry: one DatasetConfig per supported public dataset.

No dataset-specific logic lives here -- this file only declares WHAT each dataset is and
WHAT it supports (CLAUDE.md's "config-driven" rule, applied to benchmark datasets the same
way `configs/plant_a.yaml` is applied to plants). The adapter classes in
`benchmark/adapters/` do the actual work; `benchmark/run.py` looks a dataset up here and
dispatches to `REGISTRY[dataset_id].adapter_cls`.

Scope note (several-days budget, confirmed with the user): only AI4I and Bosch are
registered. Scania/SECOM/NASA Milling/MIMII DUE/FordA are documented as roadmap in
`docs/datasets.md`, not registered here -- adding one later is exactly the "swap
plant_a.yaml for plant_b.yaml" shape of change this pattern is meant to make cheap.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from benchmark.adapters.base import DatasetAdapter

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_ROOT = Path(__file__).resolve().parent
DATA_DIR = BENCHMARK_ROOT / "data"
REPORTS_DIR = BENCHMARK_ROOT / "reports"


class DatasetConfig(BaseModel):
    """Reject unknown keys -- a typo in a benchmark YAML must fail loudly, matching
    `offline/config_schema.py::StrictModel`'s convention for plant configs."""

    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    name: str
    domain: str
    tasks: list[str]
    source_url: str
    requires_manual_download: bool = False
    raw_filename: str
    target_column: str
    supports_time_series: bool = False
    supports_missing_data: bool = False
    # What this dataset CANNOT validate -- required, not optional. §34/§43 of the source
    # brief: a judge should never have to ask "does this dataset actually prove that."
    limitations: list[str] = Field(min_length=1)

    @property
    def raw_dir(self) -> Path:
        return DATA_DIR / "raw" / self.dataset_id

    @property
    def raw_path(self) -> Path:
        return self.raw_dir / self.raw_filename

    @property
    def processed_dir(self) -> Path:
        return DATA_DIR / "processed" / self.dataset_id


AI4I = DatasetConfig(
    dataset_id="ai4i",
    name="AI4I 2020 Predictive Maintenance Dataset",
    domain="manufacturing (single synthetic machine, real UCI dataset)",
    tasks=["predictive_maintenance", "pipeline_smoke_test"],
    source_url=(
        "https://archive.ics.uci.edu/static/public/601/"
        "ai4i+2020+predictive+maintenance+dataset.zip"
    ),
    requires_manual_download=False,
    raw_filename="ai4i2020.csv",
    target_column="Machine failure",
    supports_time_series=False,
    supports_missing_data=False,
    limitations=[
        "10,000 rows describing ONE simulated machine, not a production line -- there is "
        "no station, line, or multi-equipment structure to validate against.",
        "No timestamp column: rows have no production order, so this cannot validate "
        "time-safe splitting or temporal drift, only i.i.d. classification.",
        "No missing values in the released data -- cannot validate missing-data "
        "robustness on its own; sensor-coverage masking is applied synthetically here.",
        "Five failure-mode flags (TWF/HDF/PWF/OSF/RNF) are provided but are themselves "
        "rule-derived from the sensor columns, not independent ground truth -- treat "
        "sub-mode analysis as descriptive, not as a second validated label.",
    ],
)

BOSCH = DatasetConfig(
    dataset_id="bosch",
    name="Bosch Production Line Performance",
    domain="automotive parts manufacturing (real Kaggle competition dataset)",
    tasks=["defect_prediction", "station_analysis", "sensor_robustness"],
    source_url="https://www.kaggle.com/c/bosch-production-line-performance/data",
    requires_manual_download=True,
    raw_filename="train_numeric.csv",
    target_column="Response",
    supports_time_series=False,
    supports_missing_data=True,
    limitations=[
        "ALL ~968 numeric features are anonymized (e.g. L3_S36_F3939) -- there is no way "
        "to know which column is torque, temperature, or any other physical quantity. "
        "Only line_id/station_id/feature_id POSITION is recoverable from the name, never "
        "physical meaning. Every physical-signal canonical field (torque, temperature, "
        "vibration, pressure) is therefore NULL for every Bosch record -- this is a "
        "property of the dataset, not a gap in this adapter.",
        "No usable production-order timestamp in the standard release -- splits are "
        "seeded-stratified, not chronological; this cannot validate time-safe splitting.",
        "No causal ground truth for root-cause analysis -- station/feature correlations "
        "with the target are associations only. Fault-injection RCA evaluation is NOT "
        "attempted on Bosch (see docs/datasets.md); it runs against the synthetic "
        "simulator instead, where the true cause is actually known.",
        "Extreme class imbalance (~0.58% positive) -- accuracy is meaningless here; only "
        "PR-AUC/precision/recall/F1 at a stated operating point are reported.",
    ],
)

REGISTRY: dict[str, DatasetConfig] = {c.dataset_id: c for c in (AI4I, BOSCH)}


def get(dataset_id: str) -> DatasetConfig:
    if dataset_id not in REGISTRY:
        raise KeyError(
            f"unknown dataset {dataset_id!r}; registered: {sorted(REGISTRY)}")
    return REGISTRY[dataset_id]


def adapter_for(dataset_id: str) -> "DatasetAdapter":
    """Import lazily so `benchmark.registry` itself stays import-cheap (no pandas)."""
    cfg = get(dataset_id)
    if dataset_id == "ai4i":
        from benchmark.adapters.ai4i import AI4IAdapter
        return AI4IAdapter(cfg)
    if dataset_id == "bosch":
        from benchmark.adapters.bosch import BoschAdapter
        return BoschAdapter(cfg)
    raise KeyError(f"no adapter class registered for {dataset_id!r}")
