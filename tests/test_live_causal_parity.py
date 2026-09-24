"""The defect hazard formula exists twice. These tests stop the two from drifting.

`offline/simulator/causal_engine.py::compute_defects` evaluates competing risks VECTORISED
over a cohort. `offline/live_twin/causal.py::LiveCausal.propensity` evaluates the same
thing SCALAR over a stream, against constants frozen from a calibration cohort.

The duplication is unavoidable -- a live unit has no cohort -- and CLAUDE.md would
otherwise forbid it, so it is pinned instead: both forms are run over the same cohort with
the same constants and the same noise draws, and must agree to 1e-9. If someone edits one
and not the other, this fails rather than the live twin quietly generating defects the
trained model was never taught to predict.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from offline.benchmarks.simrun import run_line, with_horizon
from offline.config_schema import load_plant_config
from offline.live_twin.causal import FrozenCalibration, LiveCausal
from offline.simulator.causal_engine import (
    INTENSITY_SHARPENING,
    MODE_NAMES,
    build_unit_signals,
    compute_defects,
)
from offline.simulator.operators import OperatorPool
from offline.simulator.physics import build_latents, generate_signals

SEED = 4242
VISIT_COLUMNS = [
    "unit_seq", "station_id", "entry_ts", "exit_ts", "cycle_s", "prior_state",
    "blocked_before_s", "buffer_wait_s", "checklist_s", "operator_id", "shift_id",
]


@pytest.fixture(scope="module")
def cohort():
    """A real 2-day cohort with signals and mode drivers, built by the batch pipeline."""
    cfg = with_horizon(load_plant_config("configs/plant_a.yaml"), 2.0)
    root = np.random.SeedSequence(SEED)
    streams = [np.random.default_rng(s) for s in root.spawn(6)]
    pool = OperatorPool(cfg, streams[0])
    result = run_line(cfg, SEED)

    visits = pd.DataFrame(result.visits, columns=VISIT_COLUMNS)
    units = pd.DataFrame(
        [(u.seq, u.variant, u.lot_id) for u in result.units],
        columns=["unit_seq", "variant", "lot_id"],
    )
    visits = visits.merge(units, on="unit_seq")
    latents = build_latents(cfg, int(visits["unit_seq"].max()) + 1, streams[2])
    telemetry = generate_signals(cfg, visits, latents, pool.torque_bias_lookup(), streams[3])
    signals = build_unit_signals(cfg, telemetry, latents)
    return cfg, signals


def _constants_from(cfg, signals):
    """The refs/exposures LiveCausal.freeze derives, computed the same way."""
    refs, exposure = {}, {}
    for mode in MODE_NAMES:
        values = signals[f"signal_{mode}"].to_numpy(dtype=np.float64)
        firing = values[values > 1e-9]
        ref = float(np.quantile(firing, 0.995)) if len(firing) > 50 else 1.0
        scaled = np.clip(values / max(ref, 1e-9), 0.0, 1.0) ** INTENSITY_SHARPENING
        refs[mode] = ref
        exposure[mode] = max(float(scaled.mean()), 1e-9)
    return refs, exposure


def test_scalar_hazard_reproduces_the_vectorised_one(cohort):
    """The parity that matters. Same cohort, same k, same noise draws, same answer."""
    cfg, signals = cohort

    # compute_defects consumes its rng FIRST for the noise hazard: `rng.random(n)`.
    # Replaying that exact draw is what makes the two forms comparable at all.
    batch = compute_defects(cfg, signals, np.random.default_rng(99))
    noise = np.random.default_rng(99).random(len(signals))

    refs, exposure = _constants_from(cfg, signals)
    live = LiveCausal(cfg)
    live.calibration = FrozenCalibration(
        fitted_k=batch.fitted_bias,
        refs=refs,
        mean_exposure=exposure,
        vibration_reference=np.zeros(0),
        n_warmup_units=len(signals),
        realised_rate=batch.realised_rate,
    )

    lot = signals["latent_lot_quality"].to_numpy(dtype=np.float64)
    scalar = np.array([
        live.propensity(
            {mode: float(signals[f"signal_{mode}"].iloc[i]) for mode in MODE_NAMES},
            float(lot[i]),
            float(noise[i]),
        )
        for i in range(len(signals))
    ])

    assert len(scalar) == len(batch.propensity) > 100
    np.testing.assert_allclose(scalar, batch.propensity, atol=1e-9, rtol=0)


def test_freeze_produces_a_usable_calibration(cohort):
    cfg, signals = cohort
    live = LiveCausal(cfg)
    calibration = live.freeze(signals, np.random.default_rng(7))

    assert 0.0 < calibration.fitted_k < 40.0, (
        "fitted_k at the bisection ceiling means the cohort could not reach the target "
        "rate -- the calibration is degenerate (see LiveCausal.calibrate_from_batch)"
    )
    assert set(calibration.refs) == set(MODE_NAMES)
    for mode in MODE_NAMES:
        assert calibration.mean_exposure[mode] > 1e-9, f"{mode} never fired in the cohort"
    assert calibration.vibration_reference.size > 0
    assert np.all(np.diff(calibration.vibration_reference) >= 0), "reference must be sorted"
    assert 0.0 < calibration.realised_rate < 0.25


def test_propensity_requires_calibration(cohort):
    cfg, _ = cohort
    with pytest.raises(RuntimeError, match="freeze"):
        LiveCausal(cfg).propensity({"mode_a": 0.5}, 0.0, 0.5)


def test_propensity_is_monotone_in_each_mode(cohort):
    """More of any mode signal cannot reduce a unit's defect probability."""
    cfg, signals = cohort
    live = LiveCausal(cfg)
    live.freeze(signals, np.random.default_rng(7))

    for mode in MODE_NAMES:
        previous = -1.0
        for level in (0.0, 0.25, 0.5, 0.75, 1.0):
            value = live.propensity({mode: level * live.calibration.refs[mode]}, 0.0, 0.5)
            assert value >= previous - 1e-12, f"{mode} propensity fell as its signal rose"
            previous = value


def test_poorer_lot_quality_raises_the_hazard_with_no_observable_signal(cohort):
    """The confounder that puts a ceiling on achievable PR-AUC.

    A unit with zero mode signal must still be able to defect, purely from unobservable
    incoming material. If it could not, the latent would be perfectly recoverable from the
    outcome and would stop confounding anything.
    """
    cfg, signals = cohort
    live = LiveCausal(cfg)
    live.freeze(signals, np.random.default_rng(7))

    good = live.propensity({}, lot_quality=2.0, noise_draw=0.5)
    poor = live.propensity({}, lot_quality=-2.0, noise_draw=0.5)
    assert poor > good > 0.0


def test_vibration_percentile_uses_the_frozen_reference(cohort):
    cfg, signals = cohort
    live = LiveCausal(cfg)
    live.freeze(signals, np.random.default_rng(7))
    reference = live.calibration.vibration_reference

    assert live.vibration_percentile(float(reference.min()) - 1.0) == pytest.approx(0.0)
    assert live.vibration_percentile(float(reference.max()) + 1.0) == pytest.approx(100.0)
    middle = live.vibration_percentile(float(np.median(reference)))
    assert 40.0 < middle < 60.0
    assert np.isnan(live.vibration_percentile(None))
    assert np.isnan(live.vibration_percentile(float("nan")))


def test_attribution_falls_back_to_unattributed(cohort):
    """A defect no mode explains must not be labelled with a mode the model could never
    have predicted."""
    cfg, signals = cohort
    live = LiveCausal(cfg)
    live.freeze(signals, np.random.default_rng(7))

    live.propensity({}, lot_quality=-3.0, noise_draw=0.99)
    assert live.attribute() == "unattributed"

    live.propensity({"mode_a": live.calibration.refs["mode_a"]}, 0.0, 0.0)
    assert live.attribute() in MODE_NAMES
