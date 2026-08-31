"""Correlated sensor-signal generation.

Runs AFTER the discrete-event simulation, vectorised over each station's visit sequence.
Splitting it this way is both far faster than generating inside the event loop and the
only sane way to give signals genuine temporal structure: an AR(1) process over a
station's ordered visits is one `lfilter` call, whereas inside the DES it would be a
per-event scalar update.

ANTI-PATTERN THIS MODULE EXISTS TO AVOID: independent random draws per row. Every signal
here has temporal autocorrelation, and several have cross-correlation through shared
latent causes. A model trained on i.i.d. noise learns nothing that transfers, and a judge
can spot it in one autocorrelation plot.

THE TWO LATENT CAUSES (honest difficulty, hard rule 2) are produced here but NEVER
written to telemetry:
  * paint_batch_quality  -- changes every ~200 units, drives Mode A
  * incoming_lot_quality -- varies by lot, confounds torque AND vibration
Both are returned separately for the causal engine and for offline validation only.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.signal import lfilter

from offline.config_schema import PlantConfig
from offline.simulator.line_model import SIM_START

CONTINUOUS_SIGNALS = (
    "torque_nm",
    "vibration_mm_s",
    "temperature_c",
    "weld_current_a",
    "humidity_pct",
    "film_thickness_um",
)


def ar1(n: int, phi: float, sigma: float, rng: np.random.Generator) -> np.ndarray:
    """Stationary AR(1): x[i] = phi*x[i-1] + N(0, sigma).

    Seeded from the stationary distribution so the series does not spend its first few
    hundred samples warming up from zero.
    """
    if n == 0:
        return np.zeros(0)
    eps = rng.normal(0.0, sigma, n)
    eps[0] = rng.normal(0.0, sigma / np.sqrt(max(1e-9, 1.0 - phi * phi)))
    return lfilter([1.0], [1.0, -phi], eps)


def phi_for_halflife(units: float) -> float:
    """AR coefficient whose autocorrelation halves after `units` steps."""
    return float(0.5 ** (1.0 / max(1.0, units)))


@dataclass
class Latents:
    """Unobservable causes. Never reach the feature matrix -- that is the point."""

    paint_batch_quality: np.ndarray   # per unit_seq, ~N(0,1); negative = weaker supplier
    paint_batch_is_weak: np.ndarray   # per unit_seq, bool
    lot_quality: np.ndarray           # per unit_seq, ~N(0,1); negative = poorer material


def build_latents(cfg: PlantConfig, n_units: int, rng: np.random.Generator) -> Latents:
    dp = cfg.defect_physics

    batch_size = dp.mode_a.paint_batch_change_interval_units
    n_batches = n_units // batch_size + 2
    batch_q = rng.normal(0.0, 1.0, n_batches)
    weak_cut = np.quantile(batch_q, dp.mode_a.paint_batch_weak_fraction)
    batch_weak = batch_q <= weak_cut
    idx = np.arange(n_units) // batch_size

    lot_size = dp.lot_quality_change_interval_units
    n_lots = n_units // lot_size + 2
    lot_q = rng.normal(0.0, 1.0, n_lots)
    lidx = np.arange(n_units) // lot_size

    return Latents(
        paint_batch_quality=batch_q[idx],
        paint_batch_is_weak=batch_weak[idx],
        lot_quality=lot_q[lidx],
    )


def ambient_at(cfg: PlantConfig, ts: np.ndarray, rng: np.random.Generator) -> pd.DataFrame:
    """Ambient temperature and humidity at a set of timestamps (seconds from sim start).

    Temperature follows a daily sinusoid peaking mid-afternoon. Humidity carries the
    weekday step that co-triggers Mode A, plus correlated HVAC noise -- correlated, not
    i.i.d., because a paint shop's humidity does not resample every 50 seconds.
    """
    amb = cfg.ambient
    hours = (ts / 3600.0) % 24.0

    temp = amb.base_temperature_c + amb.daily_amplitude_c * np.sin(
        2.0 * np.pi * (hours - 9.0) / 24.0
    )

    day = (ts // 86400).astype(np.int64)
    weekday = (SIM_START.weekday() + day) % 7
    humid = amb.base_humidity_pct + np.where(
        np.isin(weekday, list(amb.humidity_high_days)), amb.humidity_high_add_pct, 0.0
    )

    order = np.argsort(ts)
    noise = np.empty(len(ts))
    noise[order] = ar1(len(ts), phi_for_halflife(40.0), amb.hvac_noise_sigma_pct, rng)
    humid = np.clip(humid + noise, 5.0, 100.0)
    temp = temp + noise * 0.15

    return pd.DataFrame({"ambient_temperature_c": temp, "ambient_humidity_pct": humid})


def _station_targets(station_id: int) -> dict[str, float]:
    """Deterministic per-station nominal setpoints.

    Derived from the station id rather than hardcoded so any plant config gets sensible
    setpoints without the YAML carrying 30 more rows of numbers no one will tune.
    """
    return {
        "torque_nm": 42.0 + (station_id * 7) % 19,
        "vibration_mm_s": 1.8 + (station_id % 5) * 0.3,
        "weld_current_a": 8200.0 + (station_id % 6) * 140.0,
        "film_thickness_um": 42.0 + (station_id % 4) * 3.0,
    }


def generate_signals(
    cfg: PlantConfig,
    visits: pd.DataFrame,
    latents: Latents,
    operator_bias: dict[str, float],
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Attach sensor readings to every station visit.

    `visits` must carry: unit_seq, station_id, entry_ts, cycle_s, prior_state,
    blocked_before_s, operator_id, variant.

    Manual-tier stations get NaN for every continuous signal -- they have no sensors, and
    forward-filling an all-NaN column is an explicit anti-pattern. Their information lives
    entirely in the checklist record, which is what the soft sensors must learn from.
    """
    out = visits.copy()
    for sig in CONTINUOUS_SIGNALS:
        out[sig] = np.nan

    amb = ambient_at(cfg, out["entry_ts"].to_numpy(), rng)
    out["ambient_temperature_c"] = amb["ambient_temperature_c"].to_numpy()
    out["ambient_humidity_pct"] = amb["ambient_humidity_pct"].to_numpy()

    bias_pct = out["operator_id"].map(operator_bias).fillna(0.0).to_numpy()
    lot_q = latents.lot_quality[out["unit_seq"].to_numpy()]

    mode_c_stations = set(cfg.defect_physics.mode_c.contributing_stations)

    for st in cfg.stations:
        mask = (out["station_id"] == st.id).to_numpy()
        n = int(mask.sum())
        if n == 0 or st.sensor_tier == "manual":
            continue

        # visits at this station, in time order
        sub_idx = np.flatnonzero(mask)
        order = np.argsort(out["entry_ts"].to_numpy()[sub_idx])
        sidx = sub_idx[order]

        targets = _station_targets(st.id)
        sensors = set(st.sensors)
        is_mode_c = st.id in mode_c_stations

        entry = out["entry_ts"].to_numpy()[sidx]
        cyc = out["cycle_s"].to_numpy()[sidx]
        prior = out["prior_state"].to_numpy()[sidx]
        b_bias = bias_pct[sidx]
        b_lot = lot_q[sidx]
        a_temp = out["ambient_temperature_c"].to_numpy()[sidx]
        a_hum = out["ambient_humidity_pct"].to_numpy()[sidx]

        # ---------------------------------------------------------------- torque
        if "torque_nm" in sensors:
            tgt = targets["torque_nm"]
            # Tool wear as a mean-reverting random walk over ~500 units. Wider at the
            # Mode C station so a genuine >8% deficit occurs often enough to learn.
            drift_sd = 0.042 if is_mode_c else 0.018
            drift = ar1(n, phi_for_halflife(500.0), drift_sd * np.sqrt(1 - 0.998**2), rng)
            drift = np.clip(drift, -0.16, 0.16)
            # Poorer incoming material (b_lot < 0) pulls achieved torque DOWN and, in
            # the vibration block below, pushes vibration UP. Same latent, same
            # direction: that shared confounder is what makes the Mode C interaction
            # co-occur often enough to be learnable, and it is never observable.
            lot_effect = 0.030 * b_lot if is_mode_c else 0.008 * b_lot
            meas = rng.normal(0.0, 0.020, n)
            out.loc[out.index[sidx], "torque_nm"] = tgt * (
                1.0 + drift + b_bias / 100.0 + lot_effect + meas
            )

        # ----------------------------------------------------------- vibration
        if "vibration_mm_s" in sensors:
            base = targets["vibration_mm_s"]
            wear_sd = 0.16 if is_mode_c else 0.09
            wear = ar1(n, phi_for_halflife(900.0), wear_sd * np.sqrt(1 - 0.9992**2), rng)
            # Compressed cycles and post-block release bursts genuinely shake the line.
            nominal_cyc = st.nominal_cycle_s
            compression = np.clip((nominal_cyc - cyc) / nominal_cyc, -0.3, 0.5)
            burst = np.where(prior == "blocked", 0.28, 0.0)
            lot_effect = -0.22 * b_lot if is_mode_c else -0.05 * b_lot
            raw = base * (1.0 + wear + 0.45 * compression + burst + lot_effect)
            raw = raw + rng.normal(0.0, base * 0.075, n)
            # EWMA: an accelerometer channel is filtered, never raw sample-to-sample.
            out.loc[out.index[sidx], "vibration_mm_s"] = np.maximum(
                0.05, lfilter([0.35], [1.0, -0.65], raw)
            )

        # --------------------------------------------------------- temperature
        if "temperature_c" in sensors:
            # Thermal inertia: the station tracks ambient plus process heat with lag.
            heat = 4.0 + 0.02 * cyc
            drive = (1.0 - 0.82) * (a_temp + heat)
            temp = lfilter([1.0], [1.0, -0.82], drive)
            out.loc[out.index[sidx], "temperature_c"] = temp + rng.normal(0.0, 0.25, n)

        # -------------------------------------------------------- weld current
        if "weld_current_a" in sensors:
            tgt = targets["weld_current_a"]
            thickness = 0.02 * b_lot  # material gauge varies by lot
            drift = ar1(n, phi_for_halflife(400.0), 0.012 * np.sqrt(1 - 0.9983**2), rng)
            out.loc[out.index[sidx], "weld_current_a"] = tgt * (
                1.0 + drift + thickness + rng.normal(0.0, 0.006, n)
            )

        # ------------------------------------------------------------ humidity
        if "humidity_pct" in sensors:
            out.loc[out.index[sidx], "humidity_pct"] = np.clip(
                a_hum + rng.normal(0.0, 1.1, n), 5.0, 100.0
            )

        # ------------------------------------------------------ film thickness
        if "film_thickness_um" in sensors:
            tgt = targets["film_thickness_um"]
            # Higher humidity thins the film -- a real spray-transfer effect, and the
            # interaction that makes humidity matter beyond Mode A's threshold.
            hum_effect = -0.006 * (a_hum - cfg.ambient.base_humidity_pct)
            drift = ar1(n, phi_for_halflife(300.0), 0.02 * np.sqrt(1 - 0.9977**2), rng)
            out.loc[out.index[sidx], "film_thickness_um"] = tgt * (
                1.0 + drift + hum_effect + rng.normal(0.0, 0.012, n)
            )

    return out


def autocorrelation(values: np.ndarray, lag: int = 1) -> float:
    """Lag-k autocorrelation, ignoring NaN. Used by the simulator acceptance checks."""
    v = values[~np.isnan(values)]
    if len(v) <= lag + 2:
        return 0.0
    a, b = v[:-lag], v[lag:]
    a = a - a.mean()
    b = b - b.mean()
    denom = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / denom) if denom > 0 else 0.0
