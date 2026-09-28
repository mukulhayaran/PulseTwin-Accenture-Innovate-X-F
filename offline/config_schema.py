"""Pydantic schemas for PulseTwin plant and economics configuration.

Offline only -- this module imports yaml, which is outside the serve/ import wall.
The serving layer reads station configuration from the `station_config` SQLite table,
never from YAML.

The validators here are not decoration. Several encode decisions from docs/DECISIONS.md
that would otherwise regress silently and only surface as nonsense numbers in Phase 5:

  * check_takt_feasible   -- ADR-002. A station slower than takt caps the line below the
                             target and invalidates every economic figure derived from it.
  * check_mode_weights    -- ADR-025. Mode weights are the mode budget and must sum to 1.0;
                             w_lot and w_noise sit outside that budget.
  * check_manual_tier     -- manual stations must have no continuous sensors and must
                             carry a checklist duration, or soft sensors have no target.
  * check_false_reject    -- ADR-001. Guards the 41%-label-noise trap.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SensorTier = Literal["full", "manual"]
BufferEdge = Literal["entry", "exit"]

# ADR-001: above this, false rejects dominate the positive class and "3% label noise"
# becomes false by an order of magnitude.
MAX_FALSE_REJECT_RATE = 0.01


class StrictModel(BaseModel):
    """Reject unknown keys everywhere -- a typo in YAML must fail loudly, not be ignored."""

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- plant


class Zone(StrictModel):
    id: str
    name: str
    stations: list[int] = Field(min_length=1)
    zone_sensors: list[str] = Field(min_length=1)


class DwellWindow(StrictModel):
    from_station: int
    from_edge: BufferEdge
    to_station: int
    to_edge: BufferEdge

    @model_validator(mode="after")
    def check_ordering(self) -> "DwellWindow":
        if self.to_station <= self.from_station:
            raise ValueError(
                f"dwell window must run downstream: from_station={self.from_station} "
                f"is not before to_station={self.to_station}"
            )
        return self


class Station(StrictModel):
    id: int = Field(ge=1)
    name: str
    zone: str
    nominal_cycle_s: float = Field(gt=0)
    cycle_cv: float = Field(ge=0, le=1)
    buffer_capacity_after: int = Field(ge=0)
    sensor_tier: SensorTier
    sensors: list[str]
    mtbf_hours: float = Field(gt=0)
    repair_time_s: float = Field(gt=0)
    variant_multipliers: dict[str, float]

    checklist_nominal_s: float | None = Field(default=None, gt=0)
    defect_role: str | None = None
    soft_sensor_validation: bool = False
    is_inspection_station: bool = False

    @model_validator(mode="after")
    def check_tier_consistency(self) -> "Station":
        if self.sensor_tier == "manual":
            if self.sensors:
                raise ValueError(
                    f"S{self.id} '{self.name}' is manual tier but declares continuous "
                    f"sensors {self.sensors}. Manual stations emit NaN + a checklist."
                )
            if self.checklist_nominal_s is None:
                raise ValueError(
                    f"S{self.id} '{self.name}' is manual tier but has no "
                    f"checklist_nominal_s. Soft sensors would have no proxy to learn from."
                )
        else:
            if not self.sensors:
                raise ValueError(
                    f"S{self.id} '{self.name}' is full tier but declares no sensors."
                )
        for variant, mult in self.variant_multipliers.items():
            if mult <= 0:
                raise ValueError(
                    f"S{self.id} variant_multipliers[{variant}] must be > 0, got {mult}"
                )
        return self

    def effective_cycle_s(self, variant: str) -> float:
        """Nominal cycle scaled by this variant's multiplier."""
        return self.nominal_cycle_s * self.variant_multipliers.get(variant, 1.0)


class Variant(StrictModel):
    id: str
    name: str
    base_mix_pct: float = Field(ge=0, le=100)
    tuesday_mix_pct: float = Field(ge=0, le=100)


class Shift(StrictModel):
    id: str
    name: str
    start: str
    end: str
    operator_count: int = Field(gt=0)
    skill_distribution: dict[str, float]

    @field_validator("skill_distribution")
    @classmethod
    def check_sums_to_one(cls, v: dict[str, float]) -> dict[str, float]:
        total = sum(v.values())
        if not math.isclose(total, 1.0, abs_tol=1e-6):
            raise ValueError(f"skill_distribution must sum to 1.0, got {total}")
        return v


class Ambient(StrictModel):
    base_temperature_c: float
    daily_amplitude_c: float = Field(ge=0)
    base_humidity_pct: float = Field(ge=0, le=100)
    humidity_high_days: list[int]
    humidity_high_add_pct: float
    hvac_noise_sigma_pct: float = Field(ge=0)

    @field_validator("humidity_high_days")
    @classmethod
    def check_weekdays(cls, v: list[int]) -> list[int]:
        bad = [d for d in v if not 0 <= d <= 6]
        if bad:
            raise ValueError(f"humidity_high_days must be 0..6 (0=Monday), got {bad}")
        return v


class Operators(StrictModel):
    skill_torque_bias_pct: dict[str, float]
    skill_checklist_multiplier: dict[str, float]
    rotation_interval_units: int = Field(gt=0)
    operator_bucket_count: int = Field(gt=0)


class ModeA(StrictModel):
    name: str
    weight: float = Field(ge=0, le=1)
    contributing_stations: list[int]
    dwell_window: DwellWindow
    dwell_threshold_s: float = Field(gt=0)
    dwell_nominal_s: float = Field(gt=0)
    humidity_threshold_pct: float = Field(ge=0, le=100)
    paint_batch_weak_fraction: float = Field(ge=0, le=1)
    paint_batch_change_interval_units: int = Field(gt=0)
    # Mode A intensity multiplier for a STRONG (non-weak) paint batch. 0.06 keeps the
    # latent batch a real confounder; 1.0 makes batch quality irrelevant. Experiment knob
    # (offline/experiments/p75_prototype.py) -- the default is the shipped physics.
    strong_batch_factor: float = Field(default=0.06, ge=0, le=1)

    @model_validator(mode="after")
    def check_threshold_above_nominal(self) -> "ModeA":
        if self.dwell_threshold_s <= self.dwell_nominal_s:
            raise ValueError(
                f"dwell_threshold_s ({self.dwell_threshold_s}) must exceed "
                f"dwell_nominal_s ({self.dwell_nominal_s}) or Mode A fires constantly"
            )
        return self


class ModeB(StrictModel):
    name: str
    weight: float = Field(ge=0, le=1)
    contributing_station: int
    trigger: Literal["unblock_burst"]  # ADR-026: never the blocked state itself
    unblock_min_blocked_s: float = Field(gt=0)
    rush_units_after_unblock: list[int] = Field(min_length=2, max_length=2)
    checklist_rush_threshold_s: float = Field(gt=0)
    checklist_nominal_s: float = Field(gt=0)

    @model_validator(mode="after")
    def check_rush_below_nominal(self) -> "ModeB":
        if self.checklist_rush_threshold_s >= self.checklist_nominal_s:
            raise ValueError(
                f"checklist_rush_threshold_s ({self.checklist_rush_threshold_s}) must be "
                f"below checklist_nominal_s ({self.checklist_nominal_s}) -- a rush is a "
                f"compression, not an extension"
            )
        lo, hi = self.rush_units_after_unblock
        if lo > hi or lo < 1:
            raise ValueError("rush_units_after_unblock must be [lo, hi] with 1 <= lo <= hi")
        return self


class ModeC(StrictModel):
    name: str
    weight: float = Field(ge=0, le=1)
    contributing_stations: list[int] = Field(min_length=2, max_length=2)
    torque_deficit_threshold_pct: float = Field(gt=0)
    vibration_excess_percentile: float = Field(gt=0, lt=100)
    interaction_required: bool


class DefectPhysics(StrictModel):
    mode_a: ModeA
    mode_b: ModeB
    mode_c: ModeC

    # ADR-025 -- propensity normalisation. Mode weights are the mode budget ONLY.
    bias: float
    mode_scale: float = Field(gt=0)
    w_lot: float = Field(ge=0)
    w_noise: float = Field(ge=0)
    # Per-mode hazard ceiling and intensity exponent (causal_engine.compute_defects).
    # Defaults are the shipped physics; raising them makes defects more deterministic.
    hazard_cap: float = Field(default=0.85, gt=0, lt=1)
    intensity_sharpening: float = Field(default=3.0, gt=0)

    base_defect_rate: float = Field(gt=0, lt=1)
    lot_quality_change_interval_units: int = Field(gt=0)

    label_noise_rate: float = Field(ge=0, lt=1)
    inspection_detection_rate: float = Field(gt=0, le=1)
    inspection_false_reject_rate: float = Field(ge=0, lt=1)
    inline_gate_s18: bool

    @model_validator(mode="after")
    def check_mode_weights(self) -> "DefectPhysics":
        total = self.mode_a.weight + self.mode_b.weight + self.mode_c.weight
        if not math.isclose(total, 1.0, abs_tol=1e-6):
            raise ValueError(
                f"ADR-025: mode weights are the mode budget and must sum to 1.0, got "
                f"{total}. w_lot and w_noise sit OUTSIDE this budget."
            )
        return self

    @model_validator(mode="after")
    def check_false_reject(self) -> "DefectPhysics":
        """ADR-001 -- the 41%-label-noise trap.

        At a 3% base rate a 2% false-reject rate produces 194 false positives per 276
        true catches, making 41% of positive labels wrong -- not the 3% the spec claims,
        and incompatible with a PR-AUC ceiling of 0.75-0.88.
        """
        if self.inspection_false_reject_rate > MAX_FALSE_REJECT_RATE:
            p = self.base_defect_rate
            tp = p * self.inspection_detection_rate
            fp = (1 - p) * self.inspection_false_reject_rate
            contamination = fp / (tp + fp) if (tp + fp) > 0 else 0.0
            raise ValueError(
                f"ADR-001: inspection_false_reject_rate={self.inspection_false_reject_rate} "
                f"contaminates {contamination:.1%} of positive labels at a "
                f"{p:.1%} base rate. Max allowed is {MAX_FALSE_REJECT_RATE}."
            )
        return self

    @model_validator(mode="after")
    def check_bias_matches_base_rate(self) -> "DefectPhysics":
        """bias should anchor the no-signal case at the stated base defect rate."""
        expected = math.log(self.base_defect_rate / (1 - self.base_defect_rate))
        if not math.isclose(self.bias, expected, abs_tol=0.15):
            raise ValueError(
                f"bias={self.bias} does not anchor base_defect_rate="
                f"{self.base_defect_rate}; logit gives {expected:.3f}"
            )
        return self


class WatchStarvation(StrictModel):
    # Share of a station's rolling window spent down or blocked. The replay stream only
    # ever carries idle/starved, so this is silent on a normal run by construction.
    stalled_share: float = Field(gt=0, le=1)
    # A station that emits NO events for this many takts has stopped (ADR-057). A
    # deadlocked line goes silent, and a window over events never fills with evidence of
    # that. Set above the longest gap the replay stream shows on a normal run.
    silence_takts: float = Field(gt=0)


class WatchPaintEnv(StrictModel):
    # Zone humidity in EXCESS of ambient, in percentage points. Measured on the replay
    # stream: zone tracks ambient with sd 1.1 and a rolling-8 max excess of 1.43, so a
    # rise past the absolute threshold on a humid day never fires -- only moisture the
    # ambient does not explain (an HVAC that is not holding the zone).
    humidity_excess_pct: float = Field(gt=0)


class WatchToolWear(StrictModel):
    # Rolling-mean deviation from the station's per-variant baseline. Vibration is
    # deliberately absent: its rolling-8 deviation reaches 60-120% on a normal run.
    signals: list[str] = Field(min_length=1)
    deviation_pct: float = Field(gt=0)


class Watch(StrictModel):
    """Proactive detection rules (ADR-051), baked to artifacts/watch_rules_<plant>.json
    by offline/build_watch_rules.py because serve/ cannot parse YAML."""

    window_events: int = Field(ge=2)
    cooldown_s: float = Field(ge=0)
    max_open_investigations: int = Field(ge=1)
    starvation: WatchStarvation
    paint_env: WatchPaintEnv
    tool_wear: WatchToolWear


class Simulation(StrictModel):
    duration_days: int = Field(gt=0)
    seed: int
    lot_change_interval_units: int = Field(gt=0)
    warmup_units: int = Field(ge=0)
    train_days: list[int] = Field(min_length=2, max_length=2)
    validate_days: list[int] = Field(min_length=2, max_length=2)
    test_days: list[int] = Field(min_length=2, max_length=2)
    replay_window_hours: int = Field(gt=0)
    tool_wear_pct_per_hour: list[float] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def check_temporal_splits(self) -> "Simulation":
        """Splits must be contiguous, ordered, and cover the full horizon.

        Temporal split only -- never random (leakage). See ADR-010.
        """
        tr, va, te = self.train_days, self.validate_days, self.test_days
        for label, span in (("train", tr), ("validate", va), ("test", te)):
            if span[0] > span[1]:
                raise ValueError(f"{label}_days must be [start, end] with start <= end")
        if tr[1] >= va[0]:
            raise ValueError(f"train_days {tr} must end before validate_days {va} begins")
        if va[1] >= te[0]:
            raise ValueError(f"validate_days {va} must end before test_days {te} begins")
        if te[1] != self.duration_days:
            raise ValueError(
                f"test_days ends at day {te[1]} but duration_days is "
                f"{self.duration_days} -- the horizon is not fully covered"
            )
        if tr[0] != 1:
            raise ValueError(f"train_days must start at day 1, got {tr[0]}")
        return self


class PlantConfig(StrictModel):
    """A complete plant definition. Everything the simulator needs, nothing hardcoded."""

    plant: dict[str, Any]
    zones: list[Zone] = Field(min_length=1)
    stations: list[Station] = Field(min_length=1)
    variants: list[Variant] = Field(min_length=1)
    shifts: list[Shift] = Field(min_length=1)
    ambient: Ambient
    operators: Operators
    defect_physics: DefectPhysics
    simulation: Simulation
    watch: Watch

    # ------------------------------------------------------------------ accessors

    @property
    def plant_id(self) -> str:
        return str(self.plant["id"])

    @property
    def units_per_hour_target(self) -> float:
        return float(self.plant["units_per_hour_target"])

    @property
    def takt_s(self) -> float:
        return 3600.0 / self.units_per_hour_target

    @property
    def station_ids(self) -> list[int]:
        return [s.id for s in self.stations]

    @property
    def variant_ids(self) -> list[str]:
        return [v.id for v in self.variants]

    def station(self, station_id: int) -> Station:
        for s in self.stations:
            if s.id == station_id:
                return s
        raise KeyError(f"no station {station_id} in {self.plant_id}")

    def prediction_cutoff_station(self) -> int:
        """Last station whose data may be used for prediction.

        Config-derived rather than the hardcoded "S20" of the original spec, so a longer
        line gets a proportionate cutoff with no code change. Two thirds
        along the line: late enough that the paint-zone evidence exists, early enough to
        leave real lead time before the end-of-line gate.
        """
        return self.station_ids[int(round(0.667 * len(self.stations))) - 1]

    def manual_stations(self) -> list[Station]:
        return [s for s in self.stations if s.sensor_tier == "manual"]

    def inspection_station(self) -> Station:
        for s in self.stations:
            if s.is_inspection_station:
                return s
        raise KeyError(f"no inspection station in {self.plant_id}")

    def soft_sensor_validation_stations(self) -> list[Station]:
        """Fully-instrumented stations we deliberately mask to prove the soft sensor."""
        return [s for s in self.stations if s.soft_sensor_validation]

    def mix_pct(self, tuesday: bool = False) -> dict[str, float]:
        key = "tuesday_mix_pct" if tuesday else "base_mix_pct"
        return {v.id: getattr(v, key) for v in self.variants}

    def mean_effective_cycle_s(self, station_id: int, tuesday: bool = False) -> float:
        """Variant-weighted mean cycle time -- what actually determines the constraint."""
        st = self.station(station_id)
        mix = self.mix_pct(tuesday)
        return sum(st.effective_cycle_s(v) * pct / 100.0 for v, pct in mix.items())

    def predicted_bottleneck(self, tuesday: bool = False) -> int:
        """Station with the highest variant-weighted mean cycle.

        A design-time prediction from the config alone. The simulator's detected
        bottleneck is validated against ground truth separately in Phase 5 -- this is
        only here so the config tests can assert the shift is actually configured.
        """
        return max(
            self.station_ids,
            key=lambda sid: self.mean_effective_cycle_s(sid, tuesday),
        )

    # ------------------------------------------------------------------ validators

    @model_validator(mode="after")
    def check_station_ids_unique(self) -> "PlantConfig":
        ids = [s.id for s in self.stations]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate station ids: {sorted(dupes)}")
        if ids != sorted(ids):
            raise ValueError("stations must be listed in ascending id order")
        return self

    @model_validator(mode="after")
    def check_zone_partition(self) -> "PlantConfig":
        """Every station in exactly one zone; every zone station real."""
        zone_ids = {z.id for z in self.zones}
        station_ids = set(self.station_ids)

        seen: dict[int, str] = {}
        for zone in self.zones:
            for sid in zone.stations:
                if sid not in station_ids:
                    raise ValueError(f"zone '{zone.id}' lists unknown station {sid}")
                if sid in seen:
                    raise ValueError(
                        f"station {sid} is in both zone '{seen[sid]}' and '{zone.id}'"
                    )
                seen[sid] = zone.id

        missing = station_ids - set(seen)
        if missing:
            raise ValueError(f"stations not assigned to any zone: {sorted(missing)}")

        for st in self.stations:
            if st.zone not in zone_ids:
                raise ValueError(f"S{st.id} declares unknown zone '{st.zone}'")
            if seen[st.id] != st.zone:
                raise ValueError(
                    f"S{st.id} declares zone '{st.zone}' but is listed under "
                    f"zone '{seen[st.id]}'"
                )
        return self

    @model_validator(mode="after")
    def check_takt_feasible(self) -> "PlantConfig":
        """ADR-002 -- the flaw that made the original spec's 55 UPH unachievable.

        55 UPH is a 65.45s takt. The original prescribed cycles up to 90s, which caps
        the line at 40 UPH while every dollar figure bills at 55.
        """
        takt = self.takt_s
        violations = [
            f"S{s.id} '{s.name}' = {s.nominal_cycle_s}s"
            for s in self.stations
            if s.nominal_cycle_s > takt
        ]
        if violations:
            raise ValueError(
                f"ADR-002: takt at {self.units_per_hour_target} UPH is {takt:.2f}s, but "
                f"these stations exceed it: {', '.join(violations)}. The line cannot "
                f"reach its target and every economic figure derived from it is invalid."
            )
        return self

    @model_validator(mode="after")
    def check_variant_mix(self) -> "PlantConfig":
        for label in ("base_mix_pct", "tuesday_mix_pct"):
            total = sum(getattr(v, label) for v in self.variants)
            if not math.isclose(total, 100.0, abs_tol=1e-6):
                raise ValueError(f"{label} across variants must sum to 100, got {total}")
        return self

    @model_validator(mode="after")
    def check_variant_multipliers_complete(self) -> "PlantConfig":
        want = set(self.variant_ids)
        for st in self.stations:
            have = set(st.variant_multipliers)
            if have != want:
                raise ValueError(
                    f"S{st.id} variant_multipliers has {sorted(have)}, expected "
                    f"{sorted(want)}"
                )
        return self

    @model_validator(mode="after")
    def check_defect_physics_stations_exist(self) -> "PlantConfig":
        ids = set(self.station_ids)
        dp = self.defect_physics
        refs: list[tuple[str, int]] = []
        refs += [("mode_a.contributing_stations", s) for s in dp.mode_a.contributing_stations]
        refs += [
            ("mode_a.dwell_window.from_station", dp.mode_a.dwell_window.from_station),
            ("mode_a.dwell_window.to_station", dp.mode_a.dwell_window.to_station),
            ("mode_b.contributing_station", dp.mode_b.contributing_station),
        ]
        refs += [("mode_c.contributing_stations", s) for s in dp.mode_c.contributing_stations]
        for where, sid in refs:
            if sid not in ids:
                raise ValueError(f"defect_physics.{where} references unknown station {sid}")

        rush_station = self.station(dp.mode_b.contributing_station)
        if rush_station.sensor_tier != "manual":
            raise ValueError(
                f"Mode B originates at S{rush_station.id} but that station is "
                f"'{rush_station.sensor_tier}' tier. The whole point is that the rush "
                f"happens where there are no continuous sensors."
            )
        return self

    @model_validator(mode="after")
    def check_exactly_one_inspection_station(self) -> "PlantConfig":
        gates = [s for s in self.stations if s.is_inspection_station]
        if len(gates) != 1:
            raise ValueError(
                f"expected exactly 1 inspection station, found {len(gates)}: "
                f"{[s.id for s in gates]}"
            )
        if gates[0].id != max(self.station_ids):
            raise ValueError(
                f"inspection station S{gates[0].id} must be the last station "
                f"(S{max(self.station_ids)}) -- defects surface at end of line"
            )
        return self

    @model_validator(mode="after")
    def check_soft_sensor_targets(self) -> "PlantConfig":
        """Masked-station validation needs fully-instrumented volunteers (Phase 4)."""
        targets = self.soft_sensor_validation_stations()
        if len(targets) < 2:
            raise ValueError(
                f"need >= 2 soft_sensor_validation stations for masked-station "
                f"validation, found {len(targets)}"
            )
        for st in targets:
            if st.sensor_tier != "full":
                raise ValueError(
                    f"S{st.id} is a soft-sensor validation target but is "
                    f"'{st.sensor_tier}' tier -- there would be nothing to mask"
                )
        return self

    @model_validator(mode="after")
    def check_bottleneck_shift_configured(self) -> "PlantConfig":
        """The scenario's spine: the constraint must actually MOVE with variant mix.

        This is what makes the bottleneck shift a consequence of the physics rather
        than a hardcoded claim (ADR-002). If the two bottlenecks are the same station,
        the Tuesday Problem has no bottleneck-migration story to tell.
        """
        normal = self.predicted_bottleneck(tuesday=False)
        peak = self.predicted_bottleneck(tuesday=True)
        if normal == peak:
            raise ValueError(
                f"variant mix does not shift the constraint -- S{normal} binds on both "
                f"normal and peak days. The bottleneck-migration scenario cannot occur."
            )
        return self


# ----------------------------------------------------------------------- economics


class SourcedValue(StrictModel):
    """Every dollar figure carries its provenance. Non-negotiable (CLAUDE.md §3)."""

    value: float
    source: str = Field(min_length=10)
    currency: str | None = None
    note: str | None = None


class EconomicsConfig(StrictModel):
    costs: dict[str, SourcedValue]
    production: dict[str, Any]
    finance: dict[str, Any]
    instrumentation: dict[str, Any]
    alarm_budget: dict[str, Any]
    system: dict[str, SourcedValue]
    sensitivity: dict[str, Any]

    @model_validator(mode="after")
    def check_sources_present(self) -> "EconomicsConfig":
        """Walk every nested mapping and reject a bare `value` with no `source`."""
        problems: list[str] = []

        def walk(node: Any, path: str) -> None:
            if isinstance(node, dict):
                if "value" in node and "source" not in node:
                    problems.append(path)
                for k, v in node.items():
                    walk(v, f"{path}.{k}" if path else str(k))

        for field in ("production", "finance", "instrumentation", "alarm_budget", "sensitivity"):
            walk(getattr(self, field), field)

        if problems:
            raise ValueError(
                f"unsourced economic values (every $ figure needs a `source`): {problems}"
            )
        return self

    @model_validator(mode="after")
    def check_required_keys(self) -> "EconomicsConfig":
        required_costs = {
            "scrap_cost_per_unit",
            "rework_cost_per_unit",
            "warranty_escape_cost_per_unit",
            "downtime_cost_per_hour",
            "supervisor_response_cost_per_alarm",
        }
        missing = required_costs - set(self.costs)
        if missing:
            raise ValueError(f"economics.costs missing required keys: {sorted(missing)}")

        # ADR-024 -- the 3-year NPV in BUSINESS_PROPOSAL.md needs a sourced rate.
        if "discount_rate" not in self.finance:
            raise ValueError("ADR-024: finance.discount_rate is required for the NPV")
        if "annualisation_basis" not in self.finance:
            raise ValueError(
                "ADR-024: finance.annualisation_basis is required so the continuous "
                "simulation and the 250-day economics are never conflated"
            )
        return self

    # ------------------------------------------------------------------ accessors

    def cost(self, key: str) -> float:
        return self.costs[key].value

    def cost_optimal_threshold(self, warranty_escape_rate: float = 0.30) -> float:
        """ADR-004 -- the economics-optimal alarm threshold.

            p* = C_alarm / (C_miss + C_alarm)

        Comes out around 0.0064, roughly 100x below the alarm-budget threshold. Both
        are computed and both are published; the system operates on the budget one.
        """
        c_miss = (
            self.cost("scrap_cost_per_unit")
            + self.cost("rework_cost_per_unit")
            + self.cost("warranty_escape_cost_per_unit") * warranty_escape_rate
        )
        c_alarm = self.cost("supervisor_response_cost_per_alarm")
        return c_alarm / (c_miss + c_alarm)

    @property
    def max_alerts_per_shift(self) -> int:
        return int(self.alarm_budget["max_alerts_per_shift"])

    @property
    def discount_rate(self) -> float:
        return float(self.finance["discount_rate"]["value"])


# -------------------------------------------------------------------------- loaders

CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs"


def load_plant_config(path: str | Path) -> PlantConfig:
    """Load and fully validate a plant YAML. Raises ValidationError on any problem."""
    p = Path(path)
    if not p.is_absolute() and not p.exists():
        p = CONFIG_DIR / p
    with p.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return PlantConfig.model_validate(raw)


def load_economics_config(path: str | Path = "economics.yaml") -> EconomicsConfig:
    p = Path(path)
    if not p.is_absolute() and not p.exists():
        p = CONFIG_DIR / p
    with p.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return EconomicsConfig.model_validate(raw)
