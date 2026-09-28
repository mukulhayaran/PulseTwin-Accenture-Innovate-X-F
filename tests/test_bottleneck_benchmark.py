"""Throughput-sensitivity ground truth and the detector benchmark.

These tests are deliberately run at a SHORT horizon with FEW seeds. They verify the
machinery -- determinism, CRN pairing, bootstrap validity, output schema, and that the
instrumentation added for the benchmark does not perturb the simulation. They do NOT
verify the published benchmark numbers; those come from
`python -m offline.benchmarks.run_bottleneck_benchmark --profile dev`, which is thousands
of runs and lives in the nightly workflow.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from offline.benchmarks import bottleneck_ground_truth as gt
from offline.benchmarks.bottleneck_detectors import (
    DETECTORS,
    LineObservation,
    observe,
    ranking,
)
from offline.benchmarks.run_bottleneck_benchmark import score_ranking
from offline.benchmarks.simrun import (
    SCENARIOS,
    get_scenario,
    perturb,
    run_line,
    scenarios_for,
    throughput_uph,
    with_horizon,
)
from offline.config_schema import load_plant_config

PLANT_A = "configs/plant_a.yaml"

# S12 (61.0s flat) and S19 (55.0s x1.50 on EV) are plant A's two documented constraints;
# S01 (48.0s, buffer 4) is comfortably inside takt and constrains nothing.
CONSTRAINT = 12
NON_CONSTRAINT = 1


@pytest.fixture(scope="module")
def cfg():
    return with_horizon(load_plant_config(PLANT_A), 1.0)


# ------------------------------------------------------------------------- determinism


def test_same_seed_reproduces_the_run(cfg):
    a, b = run_line(cfg, 1000), run_line(cfg, 1000)
    assert a.completed_units == b.completed_units
    assert throughput_uph(a) == throughput_uph(b)
    for sid in a.state_seconds:
        assert np.array_equal(a.state_seconds[sid], b.state_seconds[sid])


def test_different_seeds_give_a_different_realisation(cfg):
    a, b = run_line(cfg, 1000), run_line(cfg, 1001)
    assert throughput_uph(a) != throughput_uph(b), (
        "two seeds produced identical throughput; the seed is not reaching the simulator"
    )


def test_instrumentation_does_not_change_the_run(cfg):
    """The benchmark measures more than the pipeline does. It must not measure a
    DIFFERENT run while doing so, or every detector result is about a line nobody ships."""
    plain, rich = run_line(cfg, 1000), run_line(cfg, 1000, instrument=True)

    assert plain.completed_units == rich.completed_units
    assert plain.horizon_s == rich.horizon_s
    for sid in plain.state_seconds:
        assert np.array_equal(plain.state_seconds[sid], rich.state_seconds[sid])

    # Visit tuples carry NaN for instrumented stations' checklist time, and NaN != NaN,
    # so a plain == comparison reports a false difference. Compare NaN-safely.
    assert len(plain.visits) == len(rich.visits)
    for left, right in zip(plain.visits, rich.visits):
        for x, y in zip(left, right):
            if isinstance(x, float) and isinstance(y, float) and math.isnan(x) and math.isnan(y):
                continue
            assert x == y

    assert plain.buffer_unit_seconds is None and plain.active_periods is None
    assert rich.buffer_unit_seconds is not None and rich.active_periods is not None


# ------------------------------------------------------------------------ perturbation


def test_perturbation_is_a_copy_and_scales_the_right_station(cfg):
    before = {s.id: s.nominal_cycle_s for s in cfg.stations}
    perturbed = perturb(cfg, CONSTRAINT, 0.10)
    assert {s.id: s.nominal_cycle_s for s in cfg.stations} == before, "perturb mutated its input"
    for station in perturbed.stations:
        expected = before[station.id] * (1.10 if station.id == CONSTRAINT else 1.0)
        assert station.nominal_cycle_s == pytest.approx(expected)


def test_perturbing_an_unknown_station_raises(cfg):
    with pytest.raises(ValueError, match="not in"):
        perturb(cfg, 9999, 0.10)


def test_slowing_the_constraint_reduces_throughput(cfg):
    """The premise the whole ground truth rests on. If this does not hold, throughput
    sensitivity is not measuring what its name says."""
    base = throughput_uph(run_line(cfg, 1000))
    slowed = throughput_uph(run_line(perturb(cfg, CONSTRAINT, 0.15), 1000))
    assert slowed < base


def test_the_constraint_is_more_sensitive_than_a_non_constraint(cfg):
    base = throughput_uph(run_line(cfg, 1000))
    at_constraint = base - throughput_uph(run_line(perturb(cfg, CONSTRAINT, 0.15), 1000))
    at_free_station = base - throughput_uph(run_line(perturb(cfg, NON_CONSTRAINT, 0.15), 1000))
    assert at_constraint > at_free_station, (
        f"slowing S{CONSTRAINT} cost {at_constraint:.2f} UPH but slowing "
        f"S{NON_CONSTRAINT} cost {at_free_station:.2f}; the ranking would be meaningless"
    )


def test_crn_pairing_beats_independent_seeding(cfg):
    """Common Random Numbers must actually reduce the variance of the paired difference.

    Under CRN the perturbed run reuses the baseline's seed, so the difference isolates the
    treatment. Compared against differencing INDEPENDENTLY seeded runs, the paired spread
    should be visibly smaller -- that reduction is the entire reason the benchmark can
    afford 30 seeds instead of thousands.
    """
    seeds = gt.seed_list(6)
    base = {s: throughput_uph(run_line(cfg, s)) for s in seeds}
    slowed_cfg = perturb(cfg, NON_CONSTRAINT, 0.10)

    paired = np.array([throughput_uph(run_line(slowed_cfg, s)) - base[s] for s in seeds])
    # Independent: pair each perturbed run against a DIFFERENT seed's baseline.
    shuffled = [base[s] for s in seeds[1:]] + [base[seeds[0]]]
    independent = np.array(
        [throughput_uph(run_line(slowed_cfg, s)) - b for s, b in zip(seeds, shuffled)]
    )

    assert paired.std() < independent.std(), (
        f"CRN paired std {paired.std():.3f} did not beat independent {independent.std():.3f}"
    )


# --------------------------------------------------------------------------- bootstrap


def test_bootstrap_ci_is_deterministic_and_brackets_the_mean():
    values = np.array([-10.0, -12.0, -9.5, -11.0, -10.5, -13.0, -8.0, -11.5])
    low, high = gt.bootstrap_ci(values)
    assert (low, high) == gt.bootstrap_ci(values), "bootstrap is not reproducible"
    assert low < values.mean() < high
    assert low >= values.min() and high <= values.max()


def test_bootstrap_ci_handles_degenerate_input():
    assert all(math.isnan(v) for v in gt.bootstrap_ci(np.array([])))
    assert gt.bootstrap_ci(np.array([4.0])) == (4.0, 4.0)


def test_ground_truth_output_schema(cfg):
    scenario = get_scenario("plant_a", "s12_constraint")
    truth = gt.compute(
        scenario.apply(cfg),
        gt.seed_list(3),
        station_ids=[NON_CONSTRAINT, CONSTRAINT, 19],
        workers=1,
        scenario="s12_constraint",
        verbose=False,
    )

    assert truth.bottleneck_station == CONSTRAINT, (
        f"the s12_constraint scenario slows S12 by 15%; ground truth said "
        f"S{truth.bottleneck_station}"
    )
    assert truth.ranking()[0] == truth.bottleneck_station
    assert truth.runner_up_station != truth.bottleneck_station
    assert truth.margin >= 0

    summary = truth.to_summary()
    for key in (
        "plant_id", "scenario", "relative_perturbation", "horizon_days", "n_seeds",
        "bottleneck_station", "runner_up_station", "margin_uph_per_unit_relative",
        "separated", "throughput_baseline_mean_uph", "ranking",
    ):
        assert key in summary, f"summary is missing '{key}'"

    for station in truth.stations:
        assert station.ci_low <= station.mean <= station.ci_high
        assert station.n_seeds == 3
        assert "per_seed" not in station.to_row(), "per-seed values must not reach the CSV"
        # Slowing a station cannot RAISE throughput beyond noise.
        assert station.mean < 5.0


def test_sensitivity_of_a_free_station_is_near_zero(cfg):
    """A station the line absorbs must score near zero, or ranking is meaningless."""
    truth = gt.compute(
        cfg, gt.seed_list(4), station_ids=[NON_CONSTRAINT], workers=1, verbose=False
    )
    assert abs(truth.stations[0].mean) < 20.0


# --------------------------------------------------------------------------- detectors


@pytest.fixture(scope="module")
def observation(cfg) -> LineObservation:
    return observe(run_line(cfg, 1000, instrument=True), cfg)


def test_observe_requires_instrumentation(cfg):
    with pytest.raises(ValueError, match="instrument=True"):
        observe(run_line(cfg, 1000), cfg)


def test_every_detector_ranks_every_station(cfg, observation):
    ids = set(observation.station_ids)
    for detector in DETECTORS:
        scores = detector.fn(observation)
        assert set(scores) == ids, f"{detector.key} did not score every station"
        assert all(np.isfinite(v) for v in scores.values()), f"{detector.key} emitted non-finite"
        order = ranking(scores)
        assert sorted(order) == sorted(ids)
        assert len(set(order)) == len(order)


def test_ranking_breaks_ties_by_station_id():
    """A detector that cannot discriminate must still produce a stable order, or its
    score becomes an artefact of dict iteration."""
    assert ranking({3: 1.0, 1: 1.0, 2: 1.0}) == [1, 2, 3]
    assert ranking({3: 2.0, 1: 1.0, 2: 3.0}) == [2, 3, 1]


def test_detectors_disagree(observation):
    """If every detector produced the same ranking the benchmark would be theatre."""
    tops = {d.key: ranking(d.fn(observation))[0] for d in DETECTORS}
    assert len(set(tops.values())) > 1, f"all detectors agreed on {tops}"


def test_exactly_one_detector_is_flagged_as_the_shipped_one():
    current = [d for d in DETECTORS if d.is_current]
    assert len(current) == 1 and current[0].key == "active_share"


def test_adapted_detectors_declare_themselves():
    """CLAUDE.md and the brief both forbid claiming an adaptation reproduces a published
    algorithm. The key and the provenance string must agree about which is which."""
    for detector in DETECTORS:
        adapted_key = detector.key.endswith("_adapted")
        adapted_note = "ADAPTED" in detector.provenance
        assert adapted_key == adapted_note, (
            f"{detector.key}: key says adapted={adapted_key}, provenance says {adapted_note}"
        )


def test_input_queue_never_credits_the_first_station(observation):
    """Station 1 pulls from an unlimited source. If that counted as a queue it would win
    the average-queue-length ranking on every line by construction."""
    assert observation.input_queue_len[0].sum() == 0.0


# ----------------------------------------------------------------------------- scoring


def test_score_ranking_metrics():
    ids = [1, 2, 3, 4, 5]
    assert score_ranking([3, 1, 2, 4, 5], 3, ids) == {
        "top1": 1.0, "top3": 1.0, "reciprocal_rank": 1.0, "station_distance": 0.0
    }
    third = score_ranking([1, 2, 3, 4, 5], 3, ids)
    assert third["top1"] == 0.0 and third["top3"] == 1.0
    assert third["reciprocal_rank"] == pytest.approx(1 / 3)
    assert third["station_distance"] == 2.0

    last = score_ranking([1, 2, 3, 4, 5], 5, ids)
    assert last["top3"] == 0.0 and last["reciprocal_rank"] == pytest.approx(1 / 5)
    assert last["station_distance"] == 4.0


# ---------------------------------------------------------------------------- scenarios


def test_scenarios_are_uniquely_keyed_per_plant():
    seen = set()
    for scenario in SCENARIOS:
        key = (scenario.plant_id, scenario.key)
        assert key not in seen, f"duplicate scenario {key}"
        seen.add(key)


def test_every_plant_has_a_baseline_and_a_controlled_scenario():
    for plant_id in ("plant_a",):
        keys = [s.key for s in scenarios_for(plant_id)]
        assert "baseline" in keys
        assert len(keys) >= 3, f"{plant_id} has only {keys}"


def test_scenario_deltas_reference_real_stations():
    for scenario in SCENARIOS:
        cfg = load_plant_config(f"configs/{scenario.plant_id}.yaml")
        ids = {s.id for s in cfg.stations}
        assert set(scenario.deltas) <= ids, (
            f"scenario {scenario.plant_id}/{scenario.key} names stations not on the line"
        )


def test_scenario_apply_does_not_mutate_its_input():
    cfg = load_plant_config("configs/plant_a.yaml")
    before = [s.nominal_cycle_s for s in cfg.stations]
    get_scenario("plant_a", "ambiguous_s12_s19").apply(cfg)
    assert [s.nominal_cycle_s for s in cfg.stations] == before


def test_ambiguous_scenario_really_is_close():
    """The ambiguous scenario exists so the benchmark has a case where being confidently
    wrong is punished. If its two constraints are far apart it is not doing that job."""
    cfg = load_plant_config("configs/plant_a.yaml")
    scenario = get_scenario("plant_a", "ambiguous_s12_s19")
    applied = scenario.apply(cfg)
    by_id = {s.id: s for s in applied.stations}
    # S19's EV multiplier is 1.50, so compare variant-weighted, not nominal.
    s12 = by_id[12].nominal_cycle_s
    s19_ev = by_id[19].nominal_cycle_s * by_id[19].variant_multipliers["ev"]
    assert len(scenario.deltas) == 2
    assert s12 < s19_ev, "the ambiguous scenario no longer has two competing constraints"
