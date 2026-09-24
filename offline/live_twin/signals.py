"""Incremental sensor-signal generation for the live twin.

`offline/simulator/physics.py` generates signals VECTORISED over a whole finished run --
one `lfilter` call per station per signal. That is the right design for the batch pipeline
and it is not usable live, because a live twin has one visit at a time and no future.

This module steps the identical recurrences one sample at a time. Every formula,
coefficient and setpoint is IMPORTED from physics.py rather than restated, so a change
there reaches here; only the evaluation order differs.

    torque           target * (1 + AR1 drift + operator bias + lot effect + noise)
    vibration        EWMA over base * (1 + wear + cycle compression + block burst + lot)
    temperature      one-pole thermal lag over (ambient + process heat)
    weld current     target * (1 + AR1 drift + material gauge + noise)
    humidity         ambient + measurement noise
    film thickness   target * (1 + AR1 drift + humidity effect + noise)

WHAT PARITY WITH THE BATCH PATH DOES AND DOES NOT MEAN. The recurrences are identical, so
the live series has the same means, variances and autocorrelation structure as the batch
one -- `tests/test_live_signal_parity.py` measures exactly that. The SAMPLE VALUES differ
and cannot be made to match: `physics.generate_signals` draws its innovations in blocks
(`rng.normal(0, sigma, n)` for a whole station at once), whereas stepping draws them one
at a time interleaved across stations, so the two consume the random stream in different
orders. Claiming value-level parity would be false, and the test asserts distributional
parity instead. The deterministic, noise-free components DO match exactly and are tested
that way.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from offline.config_schema import PlantConfig, Station
from offline.simulator.line_model import SIM_START
from offline.simulator.physics import (
    CONTINUOUS_SIGNALS,
    Latents,
    _station_targets,
    phi_for_halflife,
)

# Mirrors of the filter coefficients in physics.generate_signals. Named here because a
# bare 0.65 appearing in two files is exactly how the two paths silently diverge.
VIB_EWMA_B = 0.35          # lfilter([0.35], [1.0, -0.65], raw)
VIB_EWMA_A = 0.65
TEMP_LAG_A = 0.82          # lfilter([1.0], [1.0, -0.82], drive)
HVAC_NOISE_HALFLIFE = 40.0

__all__ = ["Ar1Stepper", "LiveSignalGenerator", "CONTINUOUS_SIGNALS"]


class Ar1Stepper:
    """Scalar form of `physics.ar1`.

    `ar1` seeds element 0 from the STATIONARY distribution -- sigma / sqrt(1 - phi^2) --
    so the series does not spend its first few hundred samples warming up from zero, then
    applies x[i] = phi*x[i-1] + N(0, sigma). This reproduces that exactly, one call at a
    time.
    """

    __slots__ = ("phi", "sigma", "rng", "x")

    def __init__(self, phi: float, sigma: float, rng: np.random.Generator) -> None:
        self.phi = float(phi)
        self.sigma = float(sigma)
        self.rng = rng
        self.x: float | None = None

    def step(self) -> float:
        if self.x is None:
            stationary = self.sigma / math.sqrt(max(1e-9, 1.0 - self.phi * self.phi))
            self.x = float(self.rng.normal(0.0, stationary))
        else:
            self.x = self.phi * self.x + float(self.rng.normal(0.0, self.sigma))
        return self.x


@dataclass
class _StationState:
    """Per-station filter memory. One instance per instrumented station, for the run."""

    torque_drift: Ar1Stepper | None = None
    vibration_wear: Ar1Stepper | None = None
    weld_drift: Ar1Stepper | None = None
    film_drift: Ar1Stepper | None = None
    vibration_y: float = 0.0        # EWMA memory
    temperature_y: float = 0.0      # one-pole lag memory
    seen: int = 0
    extra: dict[str, float] = field(default_factory=dict)


class LiveSignalGenerator:
    """Produces one visit's sensor readings, in arrival order, with real temporal structure.

    Usage:
        gen = LiveSignalGenerator(cfg, latents, operator_bias, rng)
        readings = gen.emit(station, unit_seq, entry_ts, cycle_s, prior_state,
                            operator_id, tool_health=1.0, ambient_override=None)

    `tool_health` and `ambient_override` are how the incident model reaches the physics.
    Nothing else may perturb a reading: an incident changes a PHYSICAL input here and the
    consequences are then whatever the same formulas produce.
    """

    def __init__(
        self,
        cfg: PlantConfig,
        latents: Latents,
        operator_bias: dict[str, float],
        rng: np.random.Generator,
    ) -> None:
        self.cfg = cfg
        self.latents = latents
        self.operator_bias = operator_bias
        self.rng = rng
        self._mode_c = set(cfg.defect_physics.mode_c.contributing_stations)
        self._state: dict[int, _StationState] = {}
        # Ambient HVAC noise is one process for the whole plant, stepped in visit order.
        self._hvac = Ar1Stepper(
            phi_for_halflife(HVAC_NOISE_HALFLIFE),
            float(cfg.ambient.hvac_noise_sigma_pct),
            rng,
        )
        self._hvac_value = 0.0

    # ------------------------------------------------------------------- ambient

    def ambient(self, ts: float, humidity_add_pct: float = 0.0) -> tuple[float, float]:
        """Ambient (temperature_c, humidity_pct) at simulated second `ts`.

        Same daily sinusoid and weekday humidity step as `physics.ambient_at`.
        `humidity_add_pct` is the high-humidity incident's physical handle: it moves the
        AMBIENT CONDITION, and every humidity-sensitive signal downstream then responds
        through its own formula rather than being edited.
        """
        amb = self.cfg.ambient
        hours = (ts / 3600.0) % 24.0
        temp = amb.base_temperature_c + amb.daily_amplitude_c * math.sin(
            2.0 * math.pi * (hours - 9.0) / 24.0
        )
        day = int(ts // 86400)
        weekday = (SIM_START.weekday() + day) % 7
        humid = amb.base_humidity_pct + (
            amb.humidity_high_add_pct if weekday in set(amb.humidity_high_days) else 0.0
        )
        noise = self._hvac.step()
        self._hvac_value = noise
        humid = min(100.0, max(5.0, humid + noise + humidity_add_pct))
        return temp + noise * 0.15, humid

    # -------------------------------------------------------------------- signals

    def _station_state(self, st: Station) -> _StationState:
        state = self._state.get(st.id)
        if state is not None:
            return state

        is_mode_c = st.id in self._mode_c
        state = _StationState()
        # Coefficients lifted from physics.generate_signals, station by station.
        state.torque_drift = Ar1Stepper(
            phi_for_halflife(500.0),
            (0.042 if is_mode_c else 0.018) * math.sqrt(1 - 0.998**2),
            self.rng,
        )
        state.vibration_wear = Ar1Stepper(
            phi_for_halflife(900.0),
            (0.16 if is_mode_c else 0.09) * math.sqrt(1 - 0.9992**2),
            self.rng,
        )
        state.weld_drift = Ar1Stepper(
            phi_for_halflife(400.0), 0.012 * math.sqrt(1 - 0.9983**2), self.rng
        )
        state.film_drift = Ar1Stepper(
            phi_for_halflife(300.0), 0.02 * math.sqrt(1 - 0.9977**2), self.rng
        )
        self._state[st.id] = state
        return state

    def emit(
        self,
        st: Station,
        unit_seq: int,
        entry_ts: float,
        cycle_s: float,
        prior_state: str,
        operator_id: str | None,
        tool_health: float = 1.0,
        humidity_add_pct: float = 0.0,
        dropped: frozenset[str] = frozenset(),
    ) -> dict[str, float]:
        """One visit's readings.

        `tool_health` in (0, 1] is the tool-wear incident's physical handle: 1.0 is a
        healthy tool, 0.85 is a tool delivering 15% less than its setpoint. It multiplies
        the ACHIEVED torque, which is what a degrading nutrunner actually does; the model
        then sees a torque deficit through the ordinary feature path.

        `dropped` names signals whose sensor is currently failed. They come back absent
        (not zero, not imputed), which is what routes the unit down the soft-sensor path.
        """
        ambient_temp, ambient_hum = self.ambient(entry_ts, humidity_add_pct)
        out: dict[str, float] = {
            "ambient_temperature_c": ambient_temp,
            "ambient_humidity_pct": ambient_hum,
        }
        if st.sensor_tier == "manual":
            # Manual stations have no sensors at all. Forward-filling an all-NaN column
            # is an explicit anti-pattern here -- their information lives in the
            # checklist, which is what the soft sensors learn from.
            return out

        state = self._station_state(st)
        state.seen += 1
        targets = _station_targets(st.id)
        sensors = set(st.sensors)
        is_mode_c = st.id in self._mode_c
        bias = float(self.operator_bias.get(operator_id or "", 0.0))
        lot_q = float(self.latents.lot_quality[unit_seq])

        if "torque_nm" in sensors and "torque_nm" not in dropped:
            drift = float(np.clip(state.torque_drift.step(), -0.16, 0.16))
            lot_effect = (0.030 if is_mode_c else 0.008) * lot_q
            meas = float(self.rng.normal(0.0, 0.020))
            out["torque_nm"] = (
                targets["torque_nm"]
                * (1.0 + drift + bias / 100.0 + lot_effect + meas)
                * tool_health
            )

        if "vibration_mm_s" in sensors and "vibration_mm_s" not in dropped:
            base = targets["vibration_mm_s"]
            wear = state.vibration_wear.step()
            nominal = st.nominal_cycle_s
            compression = float(np.clip((nominal - cycle_s) / nominal, -0.3, 0.5))
            burst = 0.28 if prior_state == "blocked" else 0.0
            lot_effect = (-0.22 if is_mode_c else -0.05) * lot_q
            # A worn tool shakes more: the same handle that drops torque raises vibration,
            # which is the Mode C co-occurrence the model is meant to find.
            wear_lift = (1.0 - tool_health) * 1.4
            raw = base * (1.0 + wear + 0.45 * compression + burst + lot_effect + wear_lift)
            raw += float(self.rng.normal(0.0, base * 0.075))
            state.vibration_y = VIB_EWMA_B * raw + VIB_EWMA_A * state.vibration_y
            out["vibration_mm_s"] = max(0.05, state.vibration_y)

        if "temperature_c" in sensors and "temperature_c" not in dropped:
            heat = 4.0 + 0.02 * cycle_s
            drive = (1.0 - TEMP_LAG_A) * (ambient_temp + heat)
            state.temperature_y = drive + TEMP_LAG_A * state.temperature_y
            out["temperature_c"] = state.temperature_y + float(self.rng.normal(0.0, 0.25))

        if "weld_current_a" in sensors and "weld_current_a" not in dropped:
            drift = state.weld_drift.step()
            out["weld_current_a"] = targets["weld_current_a"] * (
                1.0 + drift + 0.02 * lot_q + float(self.rng.normal(0.0, 0.006))
            )

        if "humidity_pct" in sensors and "humidity_pct" not in dropped:
            out["humidity_pct"] = min(
                100.0, max(5.0, ambient_hum + float(self.rng.normal(0.0, 1.1)))
            )

        if "film_thickness_um" in sensors and "film_thickness_um" not in dropped:
            drift = state.film_drift.step()
            hum_effect = -0.006 * (ambient_hum - self.cfg.ambient.base_humidity_pct)
            out["film_thickness_um"] = targets["film_thickness_um"] * (
                1.0 + drift + hum_effect + float(self.rng.normal(0.0, 0.012))
            )

        return out

    def warm(self, st: Station, samples: int = 200) -> None:
        """Advance a station's filters without emitting, so a live run does not start
        every station from a cold filter. Cheap: it is only the AR(1) recursions."""
        state = self._station_state(st)
        for _ in range(samples):
            state.torque_drift.step()
            state.vibration_wear.step()
            state.weld_drift.step()
            state.film_drift.step()
