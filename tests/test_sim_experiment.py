"""ADR-064: dynamic simulation experiments on an isolated copy of the physical twin.

Runs the REAL twin (no mocks -- CLAUDE.md §5): one warm snapshot per module, short
horizons, few seeds. What is pinned is what the feature promises: isolation, pairing,
determinism, honest refusal of unsupported interventions, and a verdict computed by rule.
"""

from __future__ import annotations

import copy
import os

import numpy as np
import pytest

os.environ.setdefault("OMP_NUM_THREADS", "1")

from offline import sim_experiment as X  # noqa: E402

PLANT, STATION = "plant_a", 12
HORIZON = 1200.0


@pytest.fixture(scope="module")
def snap():
    twin, ctx = X.prepare(PLANT, STATION, {"scenario": "station_breakdown", "severity": 0.6},
                          budget_threshold=0.406)
    return twin, ctx


def _fingerprint(twin):
    return (twin.now, twin.units_completed, twin.alerts_opened,
            [rt.capacity for rt in twin.sim.stations],
            [rt.cfg.nominal_cycle_s for rt in twin.sim.stations],
            [rt.cfg.repair_time_s for rt in twin.sim.stations])


def test_a_fork_never_mutates_the_source_snapshot(snap):
    twin, ctx = snap
    before = _fingerprint(twin)
    for item in ({"type": "feed_buffer_delta", "params": {"delta": 4}},
                 {"type": "cycle_time_pct", "params": {"pct": -15}},
                 {"type": "repair_time_pct", "params": {"pct": 40}},
                 {"type": "maintenance", "params": {"delay_min": 0}}):
        X.run_replica(twin, 1, item, STATION, ctx, HORIZON)
    assert _fingerprint(twin) == before


def test_the_same_seed_reproduces_bit_for_bit(snap):
    twin, ctx = snap
    a = X.run_replica(twin, 7, None, STATION, ctx, HORIZON)
    b = X.run_replica(twin, 7, None, STATION, ctx, HORIZON)
    a.pop("runtime_s"), b.pop("runtime_s")
    assert a == b


def test_different_seeds_are_different_futures(snap):
    twin, ctx = snap
    runs = [X.run_replica(twin, s, None, STATION, ctx, 3600.0)["throughput_uph"]
            for s in (1, 2, 3, 4)]
    assert len(set(runs)) > 1


def test_each_intervention_changes_the_parameter_it_names(snap):
    twin, ctx = snap
    f = copy.deepcopy(twin)
    rt = X._station(f, STATION)
    up = f.sim.stations[rt.index - 1]
    cap, cyc, rep = up.capacity, rt.cfg.nominal_cycle_s, rt.cfg.repair_time_s
    X._apply(f, {"type": "feed_buffer_delta", "params": {"delta": 3}}, STATION, ctx)
    X._apply(f, {"type": "cycle_time_pct", "params": {"pct": -10}}, STATION, ctx)
    X._apply(f, {"type": "repair_time_pct", "params": {"pct": 20}}, STATION, ctx)
    assert f._base_capacity[up.cfg.id] >= 1 and up.capacity >= cap
    assert rt.cfg.nominal_cycle_s == pytest.approx(cyc * 0.9)
    assert rt.cfg.repair_time_s == pytest.approx(rep * 1.2)


def test_maintenance_takes_the_station_down_through_the_simulator(snap):
    twin, ctx = snap
    now = X.run_replica(twin, 3, {"type": "maintenance", "params": {"delay_min": 0}},
                        STATION, ctx, 3600.0)
    base = X.run_replica(twin, 3, None, STATION, ctx, 3600.0)
    assert now["down_pct"] > base["down_pct"] or now["downtime_s"] > 0


def test_a_mitigation_for_a_condition_that_is_absent_is_refused(snap):
    twin, ctx = snap
    why = X.check_supported({"type": "mitigation", "params": {"action_type": "hvac_setpoint"}},
                            STATION, ctx, twin)
    assert why and "high_humidity" in why
    assert X.check_supported({"type": "mitigation",
                              "params": {"action_type": "maintenance_ticket"}},
                             STATION, ctx, twin) is None


def test_the_first_station_has_no_feed_buffer_to_change(snap):
    twin, ctx = snap
    first = twin.sim.stations[0].cfg.id
    assert X.check_supported({"type": "feed_buffer_delta", "params": {"delta": 2}},
                             first, ctx, twin)


def _rows(uph, starved=0.0):
    return [{"seed": i + 1, "status": "ok", "throughput_uph": u, "wip_mean": 10.0,
             "blocked_pct": 0.0, "starved_pct": starved, "down_pct": 0.0,
             "downtime_s": 0.0, "expected_defects": 0.1, "alerts": 0,
             "bottleneck_station": 12} for i, u in enumerate(uph)]


def test_the_verdict_needs_an_interval_clear_of_zero():
    labels = {"b": "baseline", "x": "x"}
    clear = X.summarise({"b": _rows([50, 51, 52, 50]), "x": _rows([53, 54, 55, 53])},
                        "b", labels, "throughput")
    assert clear["verdict"] == "improved" and clear["best_label"] == "x"
    noisy = X.summarise({"b": _rows([50, 51, 52, 50]), "x": _rows([55, 47, 56, 48])},
                        "b", labels, "throughput")
    assert noisy["verdict"] in ("too_uncertain", "no_improvement")
    assert noisy["best_label"] is None
    worse = X.summarise({"b": _rows([50, 51, 52, 50]), "x": _rows([45, 46, 47, 45])},
                        "b", labels, "throughput")
    assert worse["verdict"] == "no_improvement"


def test_differences_are_paired_by_seed_and_every_interval_is_inferred():
    s = X.summarise({"b": _rows([50, 60]), "x": _rows([51, 61])}, "b",
                    {"b": "baseline", "x": "x"}, "throughput")
    d = s["scenarios"]["x"]["vs_baseline"]["throughput_uph"]
    assert d["mean"] == pytest.approx(1.0) and d["ci_low"] == pytest.approx(1.0)
    assert d["provenance"] == "inferred"
    value = s["scenarios"]["x"]["expected_value_usd_per_hour"]
    assert value["source"].startswith("configs/economics.yaml")
    assert s["scenarios"]["x"]["intervention_cost_usd"] is None


def test_a_lower_is_better_objective_reads_the_right_direction():
    s = X.summarise({"b": _rows([50, 50, 50, 50], starved=20.0),
                     "x": _rows([50, 50, 50, 50], starved=5.0)},
                    "b", {"b": "baseline", "x": "x"}, "starvation")
    assert s["verdict"] == "improved"
    assert np.isclose(s["scenarios"]["x"]["vs_baseline"]["starved_pct"]["mean"], -15.0)
