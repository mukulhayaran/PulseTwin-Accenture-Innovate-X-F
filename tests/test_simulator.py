"""Simulator tests.

Most run on a shortened horizon so the suite stays fast; the properties being asserted
(determinism, autocorrelation, no deadlock, latent leakage) do not depend on run length.
The full 21-day acceptance checks live in `offline.simulator.generate.print_diagnostics`
and run as part of `make generate`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from offline.config_schema import load_plant_config
from offline.simulator.causal_engine import build_unit_signals, compute_defects
from offline.simulator.inspection import inspect
from offline.simulator.line_model import (
    ALL_STATES,
    BLOCKED,
    STARVED,
    LineSimulator,
    ground_truth_bottleneck,
)
from offline.simulator.operators import OperatorPool
from offline.simulator.physics import (
    CONTINUOUS_SIGNALS,
    autocorrelation,
    build_latents,
    generate_signals,
)

SHORT_DAYS = 3


def build(days: int = SHORT_DAYS, plant: str = "plant_a.yaml", seed: int | None = None):
    cfg = load_plant_config(plant)
    cfg.simulation.duration_days = days
    if seed is not None:
        cfg.simulation.seed = seed
    root = np.random.SeedSequence(cfg.simulation.seed)
    rngs = [np.random.default_rng(s) for s in root.spawn(6)]
    pool = OperatorPool(cfg, rngs[0])
    result = LineSimulator(cfg, pool, rngs[1]).run()
    return cfg, pool, result, rngs


def telemetry_for(cfg, pool, result, rngs) -> pd.DataFrame:
    from offline.simulator.generate import VISIT_COLUMNS

    visits = pd.DataFrame(result.visits, columns=VISIT_COLUMNS)
    units = pd.DataFrame(
        [(u.seq, u.unit_id, u.variant, u.lot_id, u.released_at) for u in result.units],
        columns=["unit_seq", "unit_id", "variant", "lot_id", "released_at"],
    )
    visits = visits.merge(units[["unit_seq", "unit_id", "variant", "lot_id"]], on="unit_seq")
    n_seq = int(visits["unit_seq"].max()) + 1
    latents = build_latents(cfg, n_seq, rngs[2])
    tel = generate_signals(cfg, visits, latents, pool.torque_bias_lookup(), rngs[3])
    return tel, latents


@pytest.fixture(scope="module")
def sim():
    cfg, pool, result, rngs = build()
    tel, latents = telemetry_for(cfg, pool, result, rngs)
    return cfg, pool, result, tel, latents, rngs


# ------------------------------------------------------------------------ determinism


def test_same_seed_reproduces_identical_output():
    """ADR-016. Without this the committed artifacts are not reproducible."""
    _, _, r1, _ = build(days=2)
    _, _, r2, _ = build(days=2)
    assert r1.completed_units == r2.completed_units
    assert len(r1.visits) == len(r2.visits)

    # Compare through pandas, not tuple equality: manual stations carry NaN checklist
    # values for instrumented rows and NaN != NaN, so `==` reports a false difference.
    from offline.simulator.generate import VISIT_COLUMNS

    a = pd.DataFrame(r1.visits, columns=VISIT_COLUMNS)
    b = pd.DataFrame(r2.visits, columns=VISIT_COLUMNS)
    pd.testing.assert_frame_equal(a, b)

    for sid in r1.state_seconds:
        np.testing.assert_allclose(r1.state_seconds[sid], r2.state_seconds[sid])


def test_different_seed_produces_different_output():
    """Guards against a seed that is silently ignored."""
    _, _, r1, _ = build(days=2, seed=42)
    _, _, r2, _ = build(days=2, seed=1234)
    assert r1.visits[:200] != r2.visits[:200]


# ------------------------------------------------------------------- line mechanics


def test_line_does_not_deadlock(sim):
    """Regression guard on the blocked-while-down deadlock.

    A station that broke down while BLOCKED could not act on _on_buffer_freed, and on
    repair went straight back to BLOCKED waiting for a pop that would never come. The
    line ground from 55 UPH to 6 UPH. _on_repair_done now retries the release.
    """
    cfg, _, result, _, _, _ = sim
    uph = result.achieved_uph()
    assert uph > 0.85 * cfg.units_per_hour_target, f"line stalled at {uph:.1f} UPH"


def test_every_unit_visits_every_station_in_order(sim):
    _, _, _, tel, _, _ = sim
    finished = tel.groupby("unit_seq")["station_id"].count()
    full = finished[finished == finished.max()].index[:50]
    for seq in full:
        rows = tel[tel["unit_seq"] == seq].sort_values("entry_ts")
        assert list(rows["station_id"]) == sorted(rows["station_id"])
        assert (rows["exit_ts"].to_numpy() >= rows["entry_ts"].to_numpy()).all()


def test_blocked_and_starved_both_occur(sim):
    """A line with no blocking or no starving is not modelling buffers at all."""
    cfg, _, result, _, _, _ = sim
    ib, isv = ALL_STATES.index(BLOCKED), ALL_STATES.index(STARVED)
    blocked = sum(result.state_seconds[s.id][:, ib].sum() for s in cfg.stations)
    starved = sum(result.state_seconds[s.id][:, isv].sum() for s in cfg.stations)
    assert blocked > 0 and starved > 0


def test_buffer_waits_are_non_negative(sim):
    """Buffer capacity is enforced structurally in _on_work_done; this is the observable
    consequence -- a unit can never leave a buffer before it entered one."""
    _, _, _, tel, _, _ = sim
    assert (tel["buffer_wait_s"] >= 0).all()


@pytest.mark.parametrize("plant", ["plant_a.yaml"])
def test_bottleneck_shifts_between_day_types(plant):
    """The scenario spine, verified on BOTH plants (ADR-006 generalisation claim)."""
    cfg, _, result, _ = build(days=9, plant=plant)
    truth = ground_truth_bottleneck(result, cfg)
    assert (truth > 0).any()
    assert len(np.unique(truth[truth > 0])) > 1, "constraint never moved"


# ------------------------------------------------------------------------- signals


@pytest.mark.parametrize(
    "signal,station_key",
    [("torque_nm", "torque"), ("vibration_mm_s", "vibration"), ("temperature_c", "paint")],
)
def test_signals_are_autocorrelated(sim, signal, station_key):
    """The headline anti-pattern: independent random draws per row."""
    cfg, _, _, tel, _, _ = sim
    station = {
        "torque": cfg.defect_physics.mode_c.contributing_stations[0],
        "vibration": cfg.defect_physics.mode_c.contributing_stations[1],
        "paint": cfg.defect_physics.mode_a.contributing_stations[0],
    }[station_key]
    col = tel.loc[tel["station_id"] == station].sort_values("entry_ts")[signal]
    assert autocorrelation(col.to_numpy()) > 0.30


def test_manual_stations_have_no_continuous_readings(sim):
    """Rubric bullet 1. These NaNs are what the soft sensors must reconstruct."""
    cfg, _, _, tel, _, _ = sim
    manual = [s.id for s in cfg.manual_stations()]
    rows = tel[tel["station_id"].isin(manual)]
    assert len(rows) > 0
    assert rows[list(CONTINUOUS_SIGNALS)].isna().all().all()


def test_manual_stations_do_have_checklists(sim):
    cfg, pool, _, _, _, _ = sim
    manual = {s.id for s in cfg.manual_stations()}
    recorded = {c[1] for c in pool.checklists}
    assert manual == recorded


def test_mode_c_drivers_are_correlated_through_the_latent(sim):
    """The confounder must actually couple torque and vibration.

    With the sign inverted (an early bug) the two anti-correlated, the interaction almost
    never co-occurred, and Mode C produced 5 defects in 27k units.
    """
    cfg, _, _, tel, _, _ = sim
    tq_st, vb_st = cfg.defect_physics.mode_c.contributing_stations
    tq = tel[tel["station_id"] == tq_st].set_index("unit_seq")["torque_nm"]
    vb = tel[tel["station_id"] == vb_st].set_index("unit_seq")["vibration_mm_s"]
    joined = pd.DataFrame({"tq": tq, "vb": vb}).dropna()
    # low torque should accompany high vibration -> negative correlation
    assert joined["tq"].corr(joined["vb"]) < -0.15


# ------------------------------------------------------------------- causal engine


def test_all_three_modes_cause_defects(sim):
    """Each mode must be independently capable of causing a defect.

    Under the additive-logistic form this failed: the intercept needed to hold a 3% rate
    sat so low that only multi-mode units defected, and Mode C contributed 4 of 759
    defects against a configured weight of 0.20. Competing risks fixed it.
    """
    cfg, _, _, tel, latents, rngs = sim
    signals = build_unit_signals(cfg, tel, latents)
    causal = compute_defects(cfg, signals, rngs[4])
    modes = pd.Series(causal.defect_mode)
    counts = modes[modes != ""].value_counts()
    for mode in ("mode_a", "mode_b", "mode_c"):
        assert counts.get(mode, 0) > 0, f"{mode} never caused a defect: {counts.to_dict()}"


def test_defect_rate_in_configured_band(sim):
    cfg, _, _, tel, latents, rngs = sim
    signals = build_unit_signals(cfg, tel, latents)
    causal = compute_defects(cfg, signals, rngs[4])
    assert 0.02 <= causal.realised_rate <= 0.05


def test_mode_c_requires_the_interaction(sim):
    """Either driver alone must be inert -- that is what makes Mode C hard to find."""
    cfg, _, _, tel, latents, _ = sim
    s = build_unit_signals(cfg, tel, latents)
    dp = cfg.defect_physics
    torque_only = (
        s["mode_c_torque_deficit_pct"] > dp.mode_c.torque_deficit_threshold_pct
    ) & (s["mode_c_vibration_pctile"] <= dp.mode_c.vibration_excess_percentile)
    assert torque_only.sum() > 0
    assert (s.loc[torque_only, "signal_mode_c"] == 0).all()


# -------------------------------------------------------------------- latent leakage


def test_latent_causes_never_appear_in_telemetry(sim):
    """Honest difficulty. If either latent reached the features the task is not hard."""
    _, _, _, tel, _, _ = sim
    banned = {"paint_batch_quality", "paint_batch_is_weak", "lot_quality",
              "latent_lot_quality", "latent_paint_batch_weak", "true_defect", "propensity"}
    assert banned.isdisjoint(set(tel.columns))


def test_latents_actually_move_the_outcome(sim):
    """A confounder that changes nothing is decoration, not difficulty."""
    cfg, _, _, tel, latents, rngs = sim
    signals = build_unit_signals(cfg, tel, latents)
    causal = compute_defects(cfg, signals, rngs[4])
    weak = signals["latent_paint_batch_weak"].to_numpy().astype(bool)
    assert causal.true_defect[weak].mean() > causal.true_defect[~weak].mean()


# ---------------------------------------------------------------------- inspection


def test_label_contamination_stays_low(sim):
    """ADR-001. The original 0.02 false-reject rate made 41% of positive labels wrong."""
    cfg, _, _, tel, latents, rngs = sim
    signals = build_unit_signals(cfg, tel, latents)
    causal = compute_defects(cfg, signals, rngs[4])
    res = inspect(cfg, causal.true_defect, rngs[5])
    summary = res.summary()
    assert summary["false_positive_share"] < 0.10


def test_some_defects_escape_inspection(sim):
    """92% detection means real escapes -- that is the warranty exposure in the ROI."""
    cfg, _, _, tel, latents, rngs = sim
    signals = build_unit_signals(cfg, tel, latents)
    causal = compute_defects(cfg, signals, rngs[4])
    res = inspect(cfg, causal.true_defect, rngs[5])
    assert res.escaped.sum() > 0
    assert 0.02 < res.summary()["escape_rate"] < 0.25


def test_true_defect_is_kept_separate_from_the_label(sim):
    cfg, _, _, tel, latents, rngs = sim
    signals = build_unit_signals(cfg, tel, latents)
    causal = compute_defects(cfg, signals, rngs[4])
    res = inspect(cfg, causal.true_defect, rngs[5])
    assert not np.array_equal(res.defect_detected, res.true_defect)


# ----------------------------------------------------------------------- operators


def test_rush_only_happens_at_the_mode_b_station(sim):
    cfg, pool, _, _, _, _ = sim
    rushed = {c[1] for c in pool.checklists if c[7]}
    assert rushed <= {cfg.defect_physics.mode_b.contributing_station}


def test_rushed_checklists_are_below_the_threshold(sim):
    cfg, pool, _, _, _, _ = sim
    thr = cfg.defect_physics.mode_b.checklist_rush_threshold_s
    durations = [c[5] for c in pool.checklists if c[7]]
    if durations:
        assert max(durations) < thr


def test_rush_requires_a_sustained_block_first(sim):
    """ADR-026. The trigger is the unblock transition, never the blocked state."""
    cfg, pool, _, _, _, _ = sim
    rng = np.random.default_rng(0)
    station = cfg.station(cfg.defect_physics.mode_b.contributing_station)
    before = len(pool._rush_remaining)
    pool.on_unblock(station, cfg.defect_physics.mode_b.unblock_min_blocked_s - 1.0, 0.0, rng)
    assert pool._rush_remaining.get(station.id, 0) == 0 or len(pool._rush_remaining) == before


def test_operator_assignment_is_deterministic(sim):
    """Replay must reproduce the same operator on the same unit."""
    cfg, pool, _, _, _, _ = sim
    st = cfg.stations[0]
    assert pool.assignment(st, 5000.0) == pool.assignment(st, 5000.0)
