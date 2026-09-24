"""Live application of the calibrated defect hazard.

`offline/simulator/causal_engine.py::compute_defects` is inherently a BATCH computation:
it normalises each mode's intensity by a high quantile of that mode's firing population
and by its mean exposure, then bisects a single hazard scalar `k` until the realised
defect rate matches the configured base rate. None of those three quantities exists for a
unit considered on its own.

So the live twin does what a real plant would do with a calibrated instrument: it
CALIBRATES ONCE, on a warm-up cohort, and then applies the frozen constants. `freeze()`
runs the real `compute_defects` over the warm-up cohort and keeps `k`, the per-mode
references, and the vibration percentile reference; `propensity()` then evaluates the same
competing-risks formula for one unit against those frozen constants.

THE DUPLICATION HERE IS DELIBERATE AND CHECKED. The hazard formula appears in two places,
which CLAUDE.md would normally forbid. It cannot be avoided -- one form is vectorised over
a cohort, the other is scalar over a stream -- so instead it is pinned:
`tests/test_live_causal_parity.py` runs both over the same cohort with the same constants
and asserts the propensities agree to 1e-9. If someone edits one and not the other, that
test fails rather than the live twin silently generating defects the trained model was
never taught to predict.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from offline.config_schema import PlantConfig
from offline.simulator.causal_engine import (
    INTENSITY_SHARPENING,
    MODE_NAMES,
    _sigmoid,
    compute_defects,
)


@dataclass
class FrozenCalibration:
    """Constants fitted once on the warm-up cohort and held for the run."""

    fitted_k: float
    refs: dict[str, float]            # per mode: 0.995 quantile of the firing population
    mean_exposure: dict[str, float]   # per mode: mean of the sharpened, scaled intensity
    vibration_reference: np.ndarray   # sorted warm-up vibration, for percentile lookup
    n_warmup_units: int
    realised_rate: float

    def to_dict(self) -> dict:
        return {
            "fitted_k": self.fitted_k,
            "refs": self.refs,
            "mean_exposure": self.mean_exposure,
            "n_warmup_units": self.n_warmup_units,
            "warmup_realised_defect_rate": self.realised_rate,
            "vibration_reference_n": int(self.vibration_reference.size),
        }


@dataclass
class LiveCausal:
    """Freeze the hazard calibration on a warm-up cohort, then score units one at a time."""

    cfg: PlantConfig
    calibration: FrozenCalibration | None = field(default=None)

    # ------------------------------------------------------------------- calibration

    def calibrate_from_batch(
        self, days: float, seed: int, rng: np.random.Generator
    ) -> FrozenCalibration:
        """Fit the constants from a SHORT BATCH RUN of the real offline pipeline.

        WHY NOT CALIBRATE ON THE LIVE WARM-UP. The first attempt did, and it produced a
        degenerate calibration every time: `fitted_k` pinned at its bisection ceiling of
        40.0 with zero mode exposure. Two structural reasons, both worth recording.

        First, Mode A is gated on dwell AND humidity, and the humidity step only occurs on
        the configured high-humidity weekdays (Tue/Wed). A warm-up long enough to define a
        z-score is about five simulated hours -- all of it Monday -- so Mode A cannot fire
        at all, and calibrating the hazard on a cohort where the dominant mode is
        structurally absent is calibrating on the wrong population.

        Second, Mode C is gated on a vibration PERCENTILE, and during warm-up the
        reference distribution being built is the very thing the percentile needs. It is
        circular: mode C is identically zero until the calibration it feeds exists.

        Running the genuine batch pipeline over a few days fixes both: it spans the
        weekday cycle, and `build_unit_signals` computes the percentile over a real
        cohort. It costs a few seconds at start-up and uses the SAME functions the
        offline pipeline uses, so the live twin's hazard is the trained model's hazard.
        """
        # Imported here, not at module scope: these pull pandas and the full simulator,
        # and this module is also imported by lighter code paths.
        from offline.simulator.line_model import LineSimulator
        from offline.simulator.operators import OperatorPool
        from offline.simulator.physics import build_latents, generate_signals
        from offline.simulator.causal_engine import build_unit_signals
        import copy

        cfg = copy.deepcopy(self.cfg)
        cfg.simulation.duration_days = days

        root = np.random.SeedSequence(seed)
        streams = [np.random.default_rng(s) for s in root.spawn(6)]
        pool = OperatorPool(cfg, streams[0])
        result = LineSimulator(cfg, pool, streams[1]).run()

        columns = [
            "unit_seq", "station_id", "entry_ts", "exit_ts", "cycle_s", "prior_state",
            "blocked_before_s", "buffer_wait_s", "checklist_s", "operator_id", "shift_id",
        ]
        visits = pd.DataFrame(result.visits, columns=columns)
        units = pd.DataFrame(
            [(u.seq, u.variant, u.lot_id) for u in result.units],
            columns=["unit_seq", "variant", "lot_id"],
        )
        visits = visits.merge(units, on="unit_seq")

        latents = build_latents(cfg, int(visits["unit_seq"].max()) + 1, streams[2])
        telemetry = generate_signals(
            cfg, visits, latents, pool.torque_bias_lookup(), streams[3]
        )
        signals = build_unit_signals(cfg, telemetry, latents)
        return self.freeze(signals, streams[4])

    def freeze(self, signals: pd.DataFrame, rng: np.random.Generator) -> FrozenCalibration:
        """Fit the constants by running the REAL batch engine over a cohort."""
        result = compute_defects(self.cfg, signals, rng)

        refs, exposure = {}, {}
        for mode in MODE_NAMES:
            values = signals[f"signal_{mode}"].to_numpy(dtype=np.float64)
            firing = values[values > 1e-9]
            ref = float(np.quantile(firing, 0.995)) if len(firing) > 50 else 1.0
            scaled = np.clip(values / max(ref, 1e-9), 0.0, 1.0) ** INTENSITY_SHARPENING
            refs[mode] = ref
            exposure[mode] = max(float(scaled.mean()), 1e-9)

        vib = signals.get("mode_c_vibration_mm_s")
        reference = (
            np.sort(vib.dropna().to_numpy(dtype=np.float64))
            if vib is not None
            else np.zeros(0)
        )

        self.calibration = FrozenCalibration(
            fitted_k=float(result.fitted_bias),
            refs=refs,
            mean_exposure=exposure,
            vibration_reference=reference,
            n_warmup_units=int(len(signals)),
            realised_rate=float(result.realised_rate),
        )
        return self.calibration

    # ------------------------------------------------------------------- evaluation

    def vibration_percentile(self, value: float | None) -> float:
        """Percentile of a vibration reading against the frozen warm-up distribution.

        The batch path ranks a unit against its whole cohort, which a live unit cannot
        see. Ranking against a fixed reference is the standard live substitute and is
        what a deployed system would do with a stored reference distribution.
        """
        reference = self.calibration.vibration_reference if self.calibration else None
        if value is None or reference is None or reference.size == 0 or not np.isfinite(value):
            return float("nan")
        # `searchsorted` returns `size` for anything above the reference maximum, and
        # dividing that by (size - 1) yields a percentile just over 100 -- which is not a
        # percentile. The batch path's `_pct_rank` ranks 0..n-1 over the same divisor, so
        # clamping the position to n-1 is what makes the two agree at the top of the
        # distribution instead of only in the middle.
        position = min(int(np.searchsorted(reference, float(value), side="left")),
                       reference.size - 1)
        return 100.0 * position / max(1, reference.size - 1)

    def _intensity(self, mode: str, signal: float) -> float:
        cal = self.calibration
        scaled = min(1.0, max(0.0, signal / max(cal.refs[mode], 1e-9))) ** INTENSITY_SHARPENING
        return scaled / cal.mean_exposure[mode]

    def propensity(self, mode_signals: dict[str, float], lot_quality: float,
                   noise_draw: float) -> float:
        """Competing-risks defect probability for one unit.

        Mirrors `compute_defects._prob` exactly, with `k`, the references and the mean
        exposures frozen. `noise_draw` is the caller's U(0,1) for the irreducible-noise
        hazard, passed in so the caller owns the random stream.
        """
        if self.calibration is None:
            raise RuntimeError("LiveCausal.freeze() must run before propensity()")
        dp = self.cfg.defect_physics
        weights = {
            "mode_a": dp.mode_a.weight,
            "mode_b": dp.mode_b.weight,
            "mode_c": dp.mode_c.weight,
        }
        lot_hazard = dp.w_lot * 0.008 * float(_sigmoid(np.array(-2.0 * lot_quality)))
        noise_hazard = dp.w_noise * 0.004 * noise_draw

        survive = (1.0 - lot_hazard) * (1.0 - noise_hazard)
        hazards = {}
        for mode, weight in weights.items():
            hazard = min(
                0.85,
                max(0.0, self.calibration.fitted_k * weight
                    * self._intensity(mode, float(mode_signals.get(mode, 0.0)))),
            )
            hazards[mode] = hazard
            survive *= (1.0 - hazard)
        self._last_hazards = hazards
        self._last_background = max(lot_hazard, noise_hazard)
        return 1.0 - survive

    def attribute(self) -> str:
        """Which mode most plausibly caused the last unit's defect.

        'unattributed' when no mode hazard beat the lot/noise background -- labelling
        such a defect with a mode would be a lie the model could never learn.
        """
        hazards = getattr(self, "_last_hazards", None)
        if not hazards:
            return "unattributed"
        mode = max(hazards, key=hazards.get)
        return mode if hazards[mode] > getattr(self, "_last_background", 0.0) else "unattributed"
