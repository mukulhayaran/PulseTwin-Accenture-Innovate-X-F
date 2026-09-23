"""Pydantic v2 response models. Typed responses make the frontend work trivial."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    rss_mb: float | None = None
    # "unavailable" on Windows: /proc/self/statm does not exist and psutil is a banned
    # import, so local development must degrade rather than throw (AUDIT M18).
    rss_source: str
    uptime_s: float
    plants: list[str]
    station_counts: dict[str, int]
    artifact_built_at: str | None = None
    database: str = "sqlite"


class StationConfigResponse(BaseModel):
    stations: list[dict[str, Any]]
    zones: list[dict[str, Any]]


class PredictRequest(BaseModel):
    plant_id: str = "plant_a"
    features: dict[str, float | None] = Field(default_factory=dict)
    source: Literal["replay", "live_synthetic", "real", "manual"] = "manual"


class Factor(BaseModel):
    feature: str
    direction: Literal["high", "low"]
    contribution: float
    value: float | None = None
    plain_language: str


class PredictResponse(BaseModel):
    probability: float
    calibrated: bool
    confidence_interval: list[float]
    # "decision_path", never "shap": the scorer computes exact additive decision-path
    # contributions, which is not Shapley. Naming it accurately is the whole point
    # (AUDIT M15).
    explanation_mode: str
    provenance: dict[str, list[str]]
    top_factors: list[Factor]
    recommended_action: str
    model_id: str
    model_version: int
    feature_set_version: str
    prediction_ts: str
    features_available: int
    features_missing: int
    data_completeness_pct: float
    source: Literal["replay", "live_synthetic", "real", "manual"]


class AtRiskUnit(BaseModel):
    unit_id: str
    current_station: int | None = None
    risk: float
    shared_factors: list[str]
    explanation: str
    action: Literal["HOLD", "INSPECT", "MONITOR"]
    manual_action: Literal["HOLD", "INSPECT", "MONITOR"] | None = None
    decision_recorded_at: str | None = None
    decision_status: str | None = None
    actual_outcome: int | None = None
    outcome_at: str | None = None
    validation_result: str | None = None


class ContainmentResponse(BaseModel):
    flagged_unit: str
    flagged_risk: float
    flagged_factors: list[str]
    at_risk_units: list[AtRiskUnit]
    containment_window_min: int
    total_at_risk: int


class ContainmentDecisionRequest(BaseModel):
    """A supervisor's decision, kept separate from the model recommendation."""

    plant_id: str = "plant_a"
    flagged_unit_id: str
    target_unit_id: str
    selected_action: Literal["HOLD", "INSPECT", "MONITOR"]
    message: str = Field(min_length=4, max_length=500)
    run_id: str | None = None
    alert_id: str | None = None
    owner: str | None = Field(default=None, max_length=100)
    due_station: int | None = Field(default=None, ge=1)
    due_at: str | None = None
    at_ts: str | None = None


class BottleneckResponse(BaseModel):
    current_station: int
    current_station_name: str
    confidence: float
    since: str
    previous_station: int | None = None
    shifted_at: str | None = None
    detection_accuracy: float | None = None


class MetricsResponse(BaseModel):
    """Everything stored in `metrics` and `metrics_json` for a plant.

    The four fields below `permutation_importance` were STORED BY THE ARTIFACT BUILDER
    AND SILENTLY DISCARDED HERE until 2026-09-20: `serve/main.py::metrics` splats all of
    `metrics_json` into this model, and pydantic v2's default `extra="ignore"` dropped
    every key the model did not declare. `bottleneck_report` (the forecast detail behind
    the headline skill number) and `soft_sensor_validation` (the masked-station R^2 that
    ADR-031's negative result rests on) were therefore unreachable through any endpoint,
    even though both are computed, committed and cited in the docs.

    Declare a field here for every key the builder writes, or it vanishes without error.
    Two keys are still deliberately absent because they have dedicated endpoints that
    serve them with computation attached rather than raw: `whatif_baseline` (POST
    /api/whatif) and `instrumentation_plan` (GET /api/economics/instrumentation-plan,
    which is leadership-gated -- surfacing it here would route around that gate).
    """

    plant_id: str
    values: dict[str, float]
    reliability: list[dict[str, Any]] = Field(default_factory=list)
    alarm_budget_curve: list[dict[str, Any]] = Field(default_factory=list)
    permutation_importance: list[dict[str, Any]] = Field(default_factory=list)
    bottleneck_report: dict[str, Any] = Field(default_factory=dict)
    soft_sensor_validation: dict[str, Any] = Field(default_factory=dict)
    soft_sensor_ablation: dict[str, Any] = Field(default_factory=dict)
    station_value: list[dict[str, Any]] = Field(default_factory=list)


class WhatIfRequest(BaseModel):
    plant_id: str = "plant_a"
    station_id: int
    buffer_delta: int = Field(default=0, ge=-4, le=8)
    cycle_delta_pct: float = Field(default=0.0, ge=-20.0, le=20.0)


class WhatIfResponse(BaseModel):
    mode: str
    # Throughput and WIP interpolate; the bottleneck station is categorical and is
    # resolved by nearest neighbour. Saying so is the honest part (ADR-007).
    bottleneck_mode: str
    grid_resolution: dict[str, Any]
    baseline: dict[str, Any]
    projected: dict[str, Any]
    delta: dict[str, float]
    annual_value: float
    assumptions: str


class FeedbackRequest(BaseModel):
    plant_id: str = "plant_a"
    unit_id: str
    verdict: Literal["useful", "false_alarm", "missed"]
