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


class StationConfigResponse(BaseModel):
    stations: list[dict[str, Any]]
    zones: list[dict[str, Any]]


class PredictRequest(BaseModel):
    plant_id: str = "plant_a"
    features: dict[str, float | None] = Field(default_factory=dict)


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


class AtRiskUnit(BaseModel):
    unit_id: str
    current_station: int | None = None
    risk: float
    shared_factors: list[str]
    action: Literal["HOLD", "INSPECT", "MONITOR"]


class ContainmentResponse(BaseModel):
    flagged_unit: str
    flagged_risk: float
    flagged_factors: list[str]
    at_risk_units: list[AtRiskUnit]
    containment_window_min: int
    total_at_risk: int


class BottleneckResponse(BaseModel):
    current_station: int
    current_station_name: str
    confidence: float
    since: str
    previous_station: int | None = None
    shifted_at: str | None = None
    detection_accuracy: float | None = None


class MetricsResponse(BaseModel):
    plant_id: str
    values: dict[str, float]
    reliability: list[dict[str, Any]] = Field(default_factory=list)
    alarm_budget_curve: list[dict[str, Any]] = Field(default_factory=list)
    permutation_importance: list[dict[str, Any]] = Field(default_factory=list)


class FeedbackRequest(BaseModel):
    plant_id: str = "plant_a"
    unit_id: str
    verdict: Literal["useful", "false_alarm", "missed"]
