"""The multi-causal defect model -- the Tuesday Problem's actual mechanism.

Three modes with genuinely different structure, which is what stops a single feature or a
single station metric from explaining defects:

  Mode A (paint micro-blistering)  MULTIPLICATIVE co-occurrence. Fires only when extended
      paint-buffer dwell AND high humidity AND a weak paint batch all coincide. The batch
      quality is LATENT -- the model can never observe it, so Mode A is irreducibly
      partially-explained.

  Mode B (surface prep adhesion)   HUMAN. Driven by checklist compression at a station
      with no continuous sensors at all. The only evidence is the checklist duration and
      the timing gap -- which is exactly why the soft sensors matter.

  Mode C (structural fastening)    INTERACTION. Requires torque deficit at one station
      AND vibration excess at another. Either alone is inert. A model that looks at
      marginal effects will miss it entirely.

Modes A and B share a cause (the bottleneck) so they CORRELATE, but their root causes are
different (cure chemistry vs. operator behaviour). Finding one does not find the other.

ADR-025 normalisation, as built (amended -- see compute_defects for the full reasoning):

    P(defect) = 1 - (1 - h_base) * (1 - h_lot) * PROD_i (1 - h_i)
    h_i       = k * w_i * intensity_i          intensity in [0,1], w_i sum to 1.0

A COMPETING-RISKS form, not the additive logistic the ADR first specified. Under an
additive logit the single intercept needed to hold the overall rate at 3% sat so low that
no individual mode could clear it alone, and Mode C -- which fires rarely but intensely --
produced 4 defects out of 759 against a configured weight of 0.20. Here every mode is an
independent hazard, so each can cause a defect on its own at a rate set by its weight and
its firing frequency.

`k` is the single scalar calibrated by bisection to hit the configured base rate. It is
returned and recorded in ASSUMPTIONS.md -- a stated calibration step, not a hidden fudge.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from offline.config_schema import PlantConfig
from offline.simulator.physics import Latents, _station_targets

MODE_NAMES = ("mode_a", "mode_b", "mode_c")

# Exponent applied to normalised mode intensity. Concentrates hazard onto units that
# genuinely meet the adverse conditions instead of smearing it thinly across production.
# At 1.0 the achievable PR-AUC collapsed to ~2.6x the base rate: even a perfect model
# cannot rank what the physics did not separate.
INTENSITY_SHARPENING = 3.0


@dataclass
class CausalResult:
    signals: pd.DataFrame        # per-unit mode signals + the observable drivers
    true_defect: np.ndarray      # simulator ground truth, never shown to the model
    defect_mode: np.ndarray      # 'mode_a' | 'mode_b' | 'mode_c' | ''
    propensity: np.ndarray
    fitted_bias: float           # the calibrated hazard scalar k
    realised_rate: float
    signal_scales: dict[str, float] | None = None  # normalisation constants -> ASSUMPTIONS.md


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30, 30)))


def _pct_rank(values: np.ndarray) -> np.ndarray:
    """Empirical percentile rank in [0, 100], NaN-safe."""
    out = np.full(len(values), np.nan)
    ok = ~np.isnan(values)
    if ok.sum() == 0:
        return out
    v = values[ok]
    order = v.argsort(kind="stable").argsort(kind="stable")
    out[ok] = 100.0 * order / max(1, len(v) - 1)
    return out


def build_unit_signals(
    cfg: PlantConfig, telemetry: pd.DataFrame, latents: Latents
) -> pd.DataFrame:
    """Collapse station-level telemetry into the per-unit drivers of each defect mode."""
    dp = cfg.defect_physics
    units = np.sort(telemetry["unit_seq"].unique())
    df = pd.DataFrame({"unit_seq": units}).set_index("unit_seq")

    # ---------------------------------------------------------------- Mode A drivers
    w = dp.mode_a.dwell_window
    from_col = "exit_ts" if w.from_edge == "exit" else "entry_ts"
    to_col = "entry_ts" if w.to_edge == "entry" else "exit_ts"

    a_from = telemetry[telemetry["station_id"] == w.from_station].set_index("unit_seq")[from_col]
    a_to = telemetry[telemetry["station_id"] == w.to_station].set_index("unit_seq")[to_col]
    df["paint_buffer_dwell_s"] = (a_to - a_from).reindex(df.index)

    paint_ids = dp.mode_a.contributing_stations
    hum = (
        telemetry[telemetry["station_id"].isin(paint_ids)]
        .groupby("unit_seq")["ambient_humidity_pct"]
        .mean()
    )
    df["paint_humidity_pct"] = hum.reindex(df.index)

    # ---------------------------------------------------------------- Mode B drivers
    b_st = dp.mode_b.contributing_station
    ck = telemetry[telemetry["station_id"] == b_st].set_index("unit_seq")["checklist_s"]
    df["prep_checklist_s"] = ck.reindex(df.index)

    # ---------------------------------------------------------------- Mode C drivers
    tq_st, vb_st = dp.mode_c.contributing_stations
    tq_target = _station_targets(tq_st)["torque_nm"]
    tq = telemetry[telemetry["station_id"] == tq_st].set_index("unit_seq")["torque_nm"]
    df["mode_c_torque_nm"] = tq.reindex(df.index)
    df["mode_c_torque_deficit_pct"] = 100.0 * (tq_target - df["mode_c_torque_nm"]) / tq_target

    vb_rows = telemetry[telemetry["station_id"] == vb_st].set_index("unit_seq")
    vb = vb_rows["vibration_mm_s"]
    df["mode_c_vibration_mm_s"] = vb.reindex(df.index)
    pct = pd.Series(_pct_rank(vb.to_numpy()), index=vb.index)
    df["mode_c_vibration_pctile"] = pct.reindex(df.index)

    # ------------------------------------------------------------------- mode signals
    # HARD GATE. Mode A requires all three conditions to co-occur, so the signal is
    # exactly zero unless dwell AND humidity are both past threshold. A soft ramp let
    # every unit carry a little risk, which diluted the hazard across 60% of production
    # and drove the achievable PR-AUC down to the base rate (see compute_defects).
    dwell = df["paint_buffer_dwell_s"].fillna(0.0)
    humid = df["paint_humidity_pct"].fillna(0.0)
    gate = (dwell > dp.mode_a.dwell_threshold_s) & (humid > dp.mode_a.humidity_threshold_pct)

    dwell_excess = np.clip(
        (dwell - dp.mode_a.dwell_threshold_s) / dp.mode_a.dwell_nominal_s, 0.0, 2.0
    )
    hum_factor = _sigmoid((humid - dp.mode_a.humidity_threshold_pct) / 2.5)
    weak = latents.paint_batch_is_weak[df.index.to_numpy()]
    # A weak batch is far worse, but a strong batch is not immune -- otherwise the latent
    # would be perfectly recoverable from the outcome, and the confounder would stop
    # confounding anything.
    weak_factor = np.where(weak, 1.0, dp.mode_a.strong_batch_factor)
    df["signal_mode_a"] = np.where(gate, dwell_excess * hum_factor * weak_factor, 0.0)
    # The same signal minus the latent batch term: what an observer of the line could know.
    df["obs_mode_a"] = np.where(gate, dwell_excess * hum_factor, 0.0)

    # Mode B likewise fires only on an actual rush, not on any checklist faster than
    # nominal. A slightly brisk operator is not an adhesion failure.
    nominal_ck = dp.mode_b.checklist_nominal_s
    rush_ck = dp.mode_b.checklist_rush_threshold_s
    ck_time = df["prep_checklist_s"].fillna(nominal_ck)
    df["signal_mode_b"] = np.where(
        ck_time < rush_ck,
        np.clip((nominal_ck - ck_time) / (nominal_ck - rush_ck), 0.0, 1.4),
        0.0,
    )

    t_sig = np.clip(
        (df["mode_c_torque_deficit_pct"].fillna(0.0) - dp.mode_c.torque_deficit_threshold_pct)
        / dp.mode_c.torque_deficit_threshold_pct,
        0.0,
        1.5,
    )
    v_sig = np.clip(
        (df["mode_c_vibration_pctile"].fillna(0.0) - dp.mode_c.vibration_excess_percentile)
        / (100.0 - dp.mode_c.vibration_excess_percentile),
        0.0,
        1.0,
    )
    # interaction_required: the product, so either alone contributes nothing
    df["signal_mode_c"] = (t_sig * v_sig) if dp.mode_c.interaction_required else (t_sig + v_sig) / 2

    df["latent_lot_quality"] = latents.lot_quality[df.index.to_numpy()]
    df["latent_paint_batch_weak"] = weak.astype(int)
    return df.reset_index()


def compute_defects(
    cfg: PlantConfig, signals: pd.DataFrame, rng: np.random.Generator
) -> CausalResult:
    dp = cfg.defect_physics
    n = len(signals)

    raw = {
        "mode_a": signals["signal_mode_a"].to_numpy(),
        "mode_b": signals["signal_mode_b"].to_numpy(),
        "mode_c": signals["signal_mode_c"].to_numpy(),
    }

    # ------------------------------------------------------------- COMPETING RISKS
    #
    # An additive logistic over the three mode signals does not work here, and it is
    # worth recording why rather than rediscovering it later.
    #
    # The modes fire at very different frequencies: Mode A on ~60% of units at low
    # intensity, Mode C on ~2.7% at high intensity. Under an additive logit with a
    # single intercept, the intercept has to sit low enough to hold the overall rate at
    # 3% -- which pushes it so negative that NO single mode can clear the bar alone. A
    # unit only defects when several modes fire together, so Mode C produced 4 defects
    # out of 759 despite a configured weight of 0.20. Normalising by the mean instead
    # made the opposite failure: rare intense units normalised to ~190, saturated the
    # logistic, and the intercept search pinned at its floor.
    #
    # Competing risks is the right shape for the physics. Each mode is an independent
    # hazard; a unit survives only if it survives all of them:
    #
    #     P(defect) = 1 - (1 - h_base) * (1 - h_lot) * PROD_i (1 - h_i)
    #     h_i       = k * w_i * intensity_i
    #
    # Every mode can now cause a defect on its own, at a rate proportional to its
    # configured weight and how often it fires -- which is what the weights were always
    # meant to express. `k` is the single calibrated scalar (bisection, below).
    intensity = {}
    for key, v in raw.items():
        pos = v[v > 1e-9]
        # Scale by a robust high quantile of the FIRING population, not the mean, so a
        # mode that fires rarely is not penalised for its many zeros.
        # Reference at the top of the firing distribution, then a cubic sharpening.
        # Clipping at the 95th percentile flattened the top 5% into a single value and
        # destroyed the ordering that PR-AUC depends on.
        ref = float(np.quantile(pos, 0.995)) if len(pos) > 50 else 1.0
        scaled = np.clip(v / max(ref, 1e-9), 0.0, 1.0) ** dp.intensity_sharpening
        # Divide by mean EXPOSURE (population mean, zeros included) so w_i expresses each
        # mode's share of defects rather than its per-unit hazard. Without this a mode
        # that fires on 60% of units swamps one that fires on 2.7%, whatever the weights
        # say. The 0.85 hazard cap below keeps the rare-intense modes from becoming
        # deterministic -- which is exactly what the logit form could not do.
        intensity[key] = scaled / max(float(scaled.mean()), 1e-9)

    weights = {
        "mode_a": dp.mode_a.weight,
        "mode_b": dp.mode_b.weight,
        "mode_c": dp.mode_c.weight,
    }
    # The unobservable confounder is its own hazard, so poor incoming material can spoil
    # a unit with no mode signal at all. This is what keeps a ceiling on achievable
    # PR-AUC: some defects are genuinely unexplainable from observable data.
    lot_hazard = dp.w_lot * 0.008 * _sigmoid(-2.0 * signals["latent_lot_quality"].to_numpy())
    noise_hazard = dp.w_noise * 0.004 * rng.random(n)

    def _prob(k: float) -> np.ndarray:
        survive = (1.0 - lot_hazard) * (1.0 - noise_hazard)
        for key, w in weights.items():
            survive = survive * (1.0 - np.clip(k * w * intensity[key], 0.0, dp.hazard_cap))
        return 1.0 - survive

    lo, hi = 0.0, 40.0
    target = dp.base_defect_rate
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if _prob(mid).mean() > target:
            hi = mid
        else:
            lo = mid
    fitted_k = 0.5 * (lo + hi)

    p = _prob(fitted_k)
    true_defect = (rng.random(n) < p).astype(np.int8)

    hazards = {
        key: np.clip(fitted_k * w * intensity[key], 0.0, dp.hazard_cap)
        for key, w in weights.items()
    }

    # Attribute each defect to the mode whose hazard was largest for that unit.
    contrib = np.stack([hazards[m] for m in MODE_NAMES], axis=1)
    best = contrib.argmax(axis=1)
    strongest = contrib.max(axis=1)
    mode = np.array(MODE_NAMES, dtype=object)[best]
    # No meaningful mode hazard -> the lot confounder or noise did it. Labelling such a
    # defect with a mode would be a lie the model could never learn to predict.
    mode = np.where(strongest > np.maximum(lot_hazard, noise_hazard), mode, "unattributed")
    defect_mode = np.where(true_defect == 1, mode, "")

    return CausalResult(
        signals=signals,
        true_defect=true_defect,
        defect_mode=defect_mode.astype(object),
        propensity=p,
        fitted_bias=float(fitted_k),
        realised_rate=float(true_defect.mean()),
        signal_scales={k: float(np.mean(v)) for k, v in intensity.items()},
    )
