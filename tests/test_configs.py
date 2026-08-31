"""Config validation tests.

These guard the Phase 1 decisions that would otherwise regress silently and only
surface as nonsense numbers in Phase 5. Each test names the ADR it protects.
"""

from __future__ import annotations

import copy

import pytest
import yaml
from pydantic import ValidationError

from offline.config_schema import (
    CONFIG_DIR,
    EconomicsConfig,
    PlantConfig,
    load_economics_config,
    load_plant_config,
)

PLANTS = ["plant_a.yaml", "plant_b.yaml"]


@pytest.fixture(scope="module")
def plant_a() -> PlantConfig:
    return load_plant_config("plant_a.yaml")


@pytest.fixture(scope="module")
def plant_b() -> PlantConfig:
    return load_plant_config("plant_b.yaml")


@pytest.fixture(scope="module")
def economics() -> EconomicsConfig:
    return load_economics_config()


def _raw(name: str) -> dict:
    with (CONFIG_DIR / name).open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# --------------------------------------------------------------- the headline test


@pytest.mark.parametrize("name", PLANTS)
def test_configs_valid(name: str) -> None:
    """Every shipped plant config passes full validation."""
    cfg = load_plant_config(name)
    assert cfg.plant_id
    assert len(cfg.stations) >= 30


def test_economics_valid(economics: EconomicsConfig) -> None:
    assert economics.cost("scrap_cost_per_unit") > 0


# ------------------------------------------------------------------------- ADR-002


@pytest.mark.parametrize("name", PLANTS)
def test_no_station_exceeds_takt(name: str) -> None:
    """ADR-002. A station slower than takt caps the line below its stated target."""
    cfg = load_plant_config(name)
    slowest = max(s.nominal_cycle_s for s in cfg.stations)
    assert slowest <= cfg.takt_s, (
        f"{cfg.plant_id}: slowest station {slowest}s exceeds takt {cfg.takt_s:.2f}s"
    )


def test_takt_violation_is_rejected() -> None:
    """The guard actually fires -- a 90s station (the original spec's ceiling) fails."""
    raw = _raw("plant_a.yaml")
    raw["stations"][11]["nominal_cycle_s"] = 90.0
    with pytest.raises(ValidationError, match="ADR-002"):
        PlantConfig.model_validate(raw)


@pytest.mark.parametrize("name", PLANTS)
def test_bottleneck_shifts_with_variant_mix(name: str) -> None:
    """ADR-002. The constraint must MOVE with variant mix.

    This is the spine of the whole scenario: the bottleneck shift is a consequence of
    cycle-time physics, not a hardcoded claim. Verified for both plants, which proves
    the mechanism generalises rather than being tuned for plant A.
    """
    cfg = load_plant_config(name)
    normal = cfg.predicted_bottleneck(tuesday=False)
    peak = cfg.predicted_bottleneck(tuesday=True)
    assert normal != peak, f"{cfg.plant_id}: constraint never moves (S{normal} both days)"

    # and it moves in the right direction -- the peak constraint is genuinely slower
    assert cfg.mean_effective_cycle_s(peak, True) > cfg.mean_effective_cycle_s(normal, True)


def test_plant_a_bottleneck_is_s12_then_s19(plant_a: PlantConfig) -> None:
    """The specific Tuesday Problem shift the demo script narrates."""
    assert plant_a.predicted_bottleneck(tuesday=False) == 12
    assert plant_a.predicted_bottleneck(tuesday=True) == 19


def test_s19_base_cycle_preserves_the_shift(plant_a: PlantConfig) -> None:
    """Regression guard on the 55s-vs-58s correction (ADR-002, amended).

    At 58s the normal-day mean at S19 exceeds S12 and the bottleneck stops shifting,
    silently deleting the Tuesday scenario.
    """
    s19_normal = plant_a.mean_effective_cycle_s(19, tuesday=False)
    s12_normal = plant_a.mean_effective_cycle_s(12, tuesday=False)
    s19_peak = plant_a.mean_effective_cycle_s(19, tuesday=True)
    assert s19_normal < s12_normal < s19_peak, (
        f"S19 normal {s19_normal:.1f}s / S12 normal {s12_normal:.1f}s / "
        f"S19 peak {s19_peak:.1f}s -- the shift is broken"
    )


# ------------------------------------------------------------------------- ADR-001


@pytest.mark.parametrize("name", PLANTS)
def test_false_reject_rate_does_not_swamp_labels(name: str) -> None:
    """ADR-001. The original 0.02 made 41% of positive labels wrong, not 3%."""
    cfg = load_plant_config(name)
    dp = cfg.defect_physics
    p = dp.base_defect_rate
    true_pos = p * dp.inspection_detection_rate
    false_pos = (1 - p) * dp.inspection_false_reject_rate
    contamination = false_pos / (true_pos + false_pos)
    assert contamination < 0.10, (
        f"{cfg.plant_id}: {contamination:.1%} of positive labels are false rejects"
    )


def test_original_false_reject_rate_is_rejected() -> None:
    """The original spec's 0.02 must fail the guard, with the arithmetic in the message."""
    raw = _raw("plant_a.yaml")
    raw["defect_physics"]["inspection_false_reject_rate"] = 0.02
    with pytest.raises(ValidationError, match="ADR-001"):
        PlantConfig.model_validate(raw)


# ------------------------------------------------------------------------- ADR-025


@pytest.mark.parametrize("name", PLANTS)
def test_mode_weights_sum_to_one(name: str) -> None:
    """ADR-025. Mode weights are the mode budget; w_lot and w_noise sit outside it."""
    dp = load_plant_config(name).defect_physics
    total = dp.mode_a.weight + dp.mode_b.weight + dp.mode_c.weight
    assert abs(total - 1.0) < 1e-6


@pytest.mark.parametrize("name", PLANTS)
def test_unobservable_confounders_are_weighted(name: str) -> None:
    """Honest difficulty: the latent causes must actually contribute to propensity."""
    dp = load_plant_config(name).defect_physics
    assert dp.w_lot > 0, "incoming lot quality must be a real confounder, not decoration"
    assert dp.w_noise > 0, "irreducible noise must be present"


def test_mode_weight_imbalance_is_rejected() -> None:
    raw = _raw("plant_a.yaml")
    raw["defect_physics"]["mode_a"]["weight"] = 0.60
    with pytest.raises(ValidationError, match="ADR-025"):
        PlantConfig.model_validate(raw)


# ------------------------------------------------------------------------- ADR-026


@pytest.mark.parametrize("name", PLANTS)
def test_mode_b_triggers_on_unblock_not_blocked(name: str) -> None:
    """ADR-026. An operator at a blocked station gains nothing by rushing."""
    dp = load_plant_config(name).defect_physics
    assert dp.mode_b.trigger == "unblock_burst"


@pytest.mark.parametrize("name", PLANTS)
def test_mode_b_originates_at_a_manual_station(name: str) -> None:
    """The point of Mode B is that it happens where there are no continuous sensors."""
    cfg = load_plant_config(name)
    origin = cfg.station(cfg.defect_physics.mode_b.contributing_station)
    assert origin.sensor_tier == "manual"


# ------------------------------------------------------------------------- ADR-014


@pytest.mark.parametrize("name", PLANTS)
def test_no_inline_gate(name: str) -> None:
    """ADR-014. An S18 gate censors Mode A out of the training population."""
    assert load_plant_config(name).defect_physics.inline_gate_s18 is False


# -------------------------------------------------------------- structural invariants


@pytest.mark.parametrize("name", PLANTS)
def test_manual_stations_have_no_continuous_sensors(name: str) -> None:
    """Rubric bullet 1 -- these are the stations soft sensors must reconstruct."""
    cfg = load_plant_config(name)
    manual = cfg.manual_stations()
    assert len(manual) >= 4
    for st in manual:
        assert st.sensors == []
        assert st.checklist_nominal_s is not None


@pytest.mark.parametrize("name", PLANTS)
def test_soft_sensor_validation_targets_are_instrumented(name: str) -> None:
    """Phase 4 masked-station validation needs real readings to hide and recover."""
    targets = load_plant_config(name).soft_sensor_validation_stations()
    assert len(targets) >= 2
    assert all(t.sensor_tier == "full" for t in targets)


@pytest.mark.parametrize("name", PLANTS)
def test_zones_partition_all_stations(name: str) -> None:
    cfg = load_plant_config(name)
    covered = [sid for z in cfg.zones for sid in z.stations]
    assert sorted(covered) == sorted(cfg.station_ids)
    assert len(covered) == len(set(covered))


@pytest.mark.parametrize("name", PLANTS)
def test_inspection_is_the_last_station(name: str) -> None:
    """Defects surface at end of line -- that delay is the whole containment problem."""
    cfg = load_plant_config(name)
    assert cfg.inspection_station().id == max(cfg.station_ids)


@pytest.mark.parametrize("name", PLANTS)
def test_temporal_splits_are_ordered_and_complete(name: str) -> None:
    """ADR-010. Temporal split only -- never random."""
    sim = load_plant_config(name).simulation
    assert sim.train_days[0] == 1
    assert sim.train_days[1] < sim.validate_days[0]
    assert sim.validate_days[1] < sim.test_days[0]
    assert sim.test_days[1] == sim.duration_days


@pytest.mark.parametrize("name", PLANTS)
def test_simulation_horizon_is_whole_weeks(name: str) -> None:
    """ADR-020 amended. 28 days = 3 whole-week splits, each with one Tue/Wed peak pair.

    At 21 days the test split landed on Thu-Sun with no peak day, so it could not
    evaluate the EV-heavy scenario at all and held only 86 positives.
    """
    sim = load_plant_config(name).simulation
    assert sim.duration_days == 28
    for span in (sim.train_days, sim.validate_days, sim.test_days):
        assert (span[1] - span[0] + 1) % 7 == 0, f"split {span} is not whole weeks"


@pytest.mark.parametrize("name", PLANTS)
def test_mode_a_dwell_window_spans_a_manual_station(name: str) -> None:
    """The dwell feature must cross the unsensored station, or Mode A is trivially visible."""
    cfg = load_plant_config(name)
    w = cfg.defect_physics.mode_a.dwell_window
    spanned = [s for s in cfg.stations if w.from_station < s.id < w.to_station]
    assert any(s.sensor_tier == "manual" for s in spanned), (
        f"{cfg.plant_id}: dwell window S{w.from_station}->S{w.to_station} spans no "
        f"manual station"
    )


# ------------------------------------------------------------------------ economics


def test_every_cost_has_a_source(economics: EconomicsConfig) -> None:
    """CLAUDE.md §3. No dollar figure appears anywhere without provenance."""
    for key, sv in economics.costs.items():
        assert sv.source and len(sv.source) >= 10, f"{key} has no usable source"


def test_unsourced_value_is_rejected() -> None:
    raw = _raw("economics.yaml")
    del raw["finance"]["discount_rate"]["source"]
    with pytest.raises(ValidationError, match="unsourced"):
        EconomicsConfig.model_validate(raw)


def test_discount_rate_present(economics: EconomicsConfig) -> None:
    """ADR-024. The 3-year NPV needs a sourced rate."""
    assert 0 < economics.discount_rate < 1


def test_annualisation_basis_present(economics: EconomicsConfig) -> None:
    """ADR-024. Keeps the continuous sim and the 250-day economics from being conflated."""
    basis = economics.finance["annualisation_basis"]
    assert basis["annualisation_days"] == 250
    assert basis["simulation_mode"] == "continuous"


def test_cost_optimal_and_budget_are_irreconcilable(
    economics: EconomicsConfig, plant_a: PlantConfig
) -> None:
    """ADR-004. The economics and the alarm budget cannot both be satisfied.

    Careful with units: `p_cost` is a PROBABILITY THRESHOLD, `budget_rate` is a FRACTION
    OF UNITS ALARMED. Comparing them directly is meaningless. What is checkable from
    config alone is that they pull in opposite directions relative to the base rate:

      * p_cost sits far BELOW the base defect rate, so on any calibrated model a large
        share of units clear it -- the economics say "alarm aggressively".
      * the budget permits fewer alarms than there are actual defects, so it can only
        ever select a thin top sliver -- and is guaranteed to miss real defects.

    The actual operating threshold (and the value forgone by respecting the budget) is
    measured in Phase 5 against the real score distribution; it cannot be derived here.
    """
    p_cost = economics.cost_optimal_threshold()
    base_rate = plant_a.defect_physics.base_defect_rate
    units_per_shift = plant_a.units_per_hour_target * economics.production["hours_per_shift"]
    budget_rate = economics.max_alerts_per_shift / units_per_shift

    assert p_cost < base_rate / 3, (
        f"cost-optimal threshold {p_cost:.4f} is no longer far below the {base_rate:.1%} "
        f"base rate -- the economics no longer imply aggressive alarming"
    )
    assert budget_rate < base_rate / 2, (
        f"alarm budget allows {budget_rate:.2%} of units but the defect rate is "
        f"{base_rate:.1%} -- the budget no longer forces misses"
    )
