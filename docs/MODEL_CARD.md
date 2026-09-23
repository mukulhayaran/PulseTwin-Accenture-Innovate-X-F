# Model Card — PulseTwin Defect Risk

## Intended use

Rank in-flight vehicle bodies by the probability that end-of-line inspection will reject
them, early enough for a supervisor to act. Output is **advisory**: it recommends a hold
or an inspection, and a human decides.

## Out of scope

- **Not a replacement for inspection.** Recall at the operating threshold is 28%.
  Most defects are still caught by S30, not by this model.
- **Not a process controller.** The twin never writes to a production system.
- **Not an operator performance measure.** Operator identity is bucketed at the API
  boundary and never surfaced individually. Using this model to evaluate individuals
  would be a misuse.
- **Not transferable between plants.** Each plant has its own feature space and its own
  trained model.

## Training data

Simulated, 28 days per plant, seed 42, `OMP_NUM_THREADS=1` for reproducibility.

| | plant_a | plant_b |
|---|---|---|
| Stations | 30 (4 manual) | 42 (8 manual) |
| Units | 35,939 | 30,238 |
| Features | 253 | 320 |
| Achieved throughput | 53.7 UPH (97.7% of a 55 UPH target) | 45.3 UPH (94.4% of a 48 UPH target) |

**Temporal split, never random.** Days 1–14 train / 15–21 validate / 22–28 test — three
whole weeks, each containing exactly one Tuesday/Wednesday peak pair. An earlier 21-day
horizon put the test split on Thursday–Sunday with **no peak day at all**, so it could not
evaluate the scenario the model exists for.

## Metrics — plant A test split

| Metric | Value |
|---|---|
| PR-AUC | **0.256**  [95% CI 0.207–0.308] |
| **Oracle PR-AUC** | **0.481** |
| Share of ceiling captured | **53%** |
| ROC-AUC | 0.883  [0.855–0.909] |
| Brier | 0.0206 |
| Test positives | 228 (base rate 2.52%) |
| Precision @ operating threshold | 35% |
| Recall @ operating threshold | 28% |
| Bottleneck detection accuracy | 85.9% |

Plant B: PR-AUC 0.052 [0.039–0.076],
oracle 0.075 (70% captured),
recall 6%, bottleneck accuracy 90.2%.

### The oracle, and why we report it

Every training run also scores the **simulator's own latent propensity** against the same
labels. That is the true data-generating probability, including the two causes the model
can never observe, so no model can exceed it.

Reporting PR-AUC as a fraction of a measured ceiling is a stronger and more checkable
claim than any absolute number. It also gives a better leakage tripwire than an absolute
threshold: exceeding 98% of the oracle raises a warning, because that would mean the model
knows something the physics did not leave in the observable data.

The brief's target band of 0.75–0.88 is arithmetically incompatible with its own
requirement of unobservable causes — see `docs/AUDIT.md` §B34.

## Calibration

**Platt scaling**, fitted on the validation fold only. Isotonic was rejected: with a few
hundred validation positives it fits sample noise into a coarse step function and then
looks miscalibrated on test, discrediting the reliability diagram it is meant to support.
Platt also exports to two floats, which keeps the numpy scorer simple.

Reliability is reported as a 10-decile diagram with Wilson intervals (`/api/metrics`).

## Explanations

`explanation_mode: "decision_path"` — exact additive per-feature contributions from
walking each tree and crediting every split to the feature that made it, weighted by
training sample counts (the Saabas decomposition). Contributions sum to
`raw_score − expected_value` exactly, asserted in `tests/test_scorer_parity.py`.

**This is not SHAP** and is never labelled as such. It does not average over feature
orderings, so correlated features share credit by tree structure rather than symmetrically.
Naming it accurately matters more than sounding sophisticated.

## Soft sensors — the measured contrast

Masked-station validation on plant A. Stations that ARE instrumented have their sensors
hidden, are reconstructed from neighbours and checklist proxies alone, and scored against
the withheld truth.

| Station | Signal | R² | Family |
|---|---|---|---|
| S17 | humidity_pct | **+0.996** | ambient-driven |
| S17 | temperature_c | **+0.989** | ambient-driven |
| S17 | film_thickness_um | **+0.965** | ambient-driven |
| S21 | torque_nm | −0.067 | tool-wear-driven |
| S9 | vibration_mm_s | −0.223 | tool-wear-driven |
| S21 | vibration_mm_s | −0.260 | tool-wear-driven |
| S9 | torque_nm | −0.549 | tool-wear-driven |

**The answer depends entirely on the physics driving the signal, and this is the useful
finding.** Ambient-driven signals are shared across neighbouring stations — S17's
temperature correlates 0.98 with S18's — so they reconstruct almost perfectly. Tool wear
is an independent random walk per station, so a neighbour carries almost nothing: S21's
torque correlates 0.06 with S19's. **No model can recover a signal that is not there**,
and a negative R² is the honest report of that.

This directly drives the instrumentation planner: it says which retrofits are unnecessary
(the value is inferable) and which are unavoidable (genuinely unobservable).

**Ablation.** Removing all evidence from the unsensored stations costs **+0.060 PR-AUC**
on plant A (0.256 → 0.196), a 23% relative drop. So the legacy stations do matter — but
the value is in the operator checklist record, not in reconstructed continuous signals.

**Caveat, recorded by the artifact itself.** The two confidence intervals overlap
(0.207–0.308 with the manual-station data, 0.155–0.247 without), and
`metrics_json.soft_sensor_ablation` stores the conclusion verbatim: *"improvement is
within confidence intervals - reported as measured."* The point estimate is a real +0.060
and the direction is consistent, but this is not a statistically separated result at
n=9,046 with 228 positives, and it should not be quoted as one.

**Prediction intervals under-cover.** The soft sensors emit quantile intervals, and
measured coverage is **0.80–0.85 on ambient signals and 0.55–0.66 on tool-wear signals**
against a nominal 90%. The tool-wear intervals are not trustworthy — which is consistent
with the R² finding above rather than a separate defect: the model has nothing to
condition on there, so neither its point estimate nor its interval means much.

## Bottleneck forecasting

15-minute-ahead forecast of each station's active share, ridge regression on lagged
values, temporal split. **Skill vs persistence: +0.136 (plant A), +0.205 (plant B)** —
the model beats "assume nothing changes", modestly. Reported either way; a forecast that
cannot beat persistence has no business shipping.

## Known failure modes

- **Latent paint-batch quality is unobservable.** Mode A can only ever be partially
  predicted; roughly 30% of the at-risk population is gated by a variable the model cannot
  see. This is deliberate and is the main reason the oracle sits at 0.48.
- **Incoming lot quality is unobservable** and confounds torque and vibration together.
  About 7% of defects are attributed to it and are genuinely unpredictable.
- **Low-humidity weeks contain few Mode A examples**, so the model is less reliable in
  dry periods. Drift monitoring should segment by ambient humidity.
- **Plant B underperforms plant A** (recall 6% vs 28%).
  Its Mode B fires on 13.6% of visits rather than 4.5%, so the budget threshold lands
  differently. Reported as measured, not tuned to match.
- **The alarm budget forces misses by construction.** Six alerts per shift is fewer
  alerts than there are defects. That is a deliberate human-factors constraint, and its
  cost is quantified rather than hidden.
- **Every alert renders the same risk (41% on plant A).** All 648 alerting units share
  `risk_raw` = 0.880117 exactly. All 32 trees split on `paint_buffer_dwell_s` at the root;
  once a unit is past the dwell threshold and under the checklist threshold, no later
  split discriminates, so every such unit lands in the same leaf in every tree. The model
  identifies the at-risk set but **cannot rank within it**. Measured, reproduced with the
  numpy scorer, not a rendering fault, and not something retraining should chase (§1: no
  tuning toward a nicer number). The supervisor alert feed (`frontend/app/supervisor/page.tsx`)
  now renders this as a qualitative band ("At risk" / "High risk") rather than a
  false-precision point percentage; the exact number still appears, de-emphasized, in the
  card body. `AlertTriageTable` already avoided the problem by ranking on the deterministic
  cost-model dimensions (severity × threshold, lead time, peers) instead of the flat score.
- **The explanation collapses along with the score.** Verified against the committed
  plant A artifact: all 113 units with `alert_fired = 1` share not only the identical
  `calibrated_risk`, but the identical rank-0 decision-path factor, `s15_checklist_time_s`
  — 113/113, no exceptions. The flat leaf isn't just a flat number; every unit that lands
  in it gets the identical explanation too, because it is mechanically the same decision
  path. `/api/alerts/triage`'s "Station" column reflects this correctly (it is reading
  each unit's real top-ranked factor, not falling back to a constant) — the uniformity in
  the column is the finding, not a bug in how the column is computed. The broader
  cost-worthy population that has NOT yet fired (1,310 of the 1,423 plant A candidates)
  does not share this problem: 1,293 distinct risk values exist among them. A query that
  orders by `calibrated_risk DESC` with a small `limit` will therefore surface the
  degenerate already-fired subset first, before ever reaching the more varied tail.
- **The budget threshold overshoots its own budget on test data.** The operating
  threshold (p ≥ 0.406) is fitted to produce 6 alerts/shift, and on the held-out test
  split it realises **8.5**. That is a ~40% overshoot, and it is a generalisation gap, not
  a rounding artifact: the defect-bearing population in the test week is denser than in
  the fitting window. A production deployment should re-fit the threshold on a rolling
  window rather than freezing it at build time — the alarm budget is a human constraint,
  so exceeding it by 40% is a real cost, not a metric footnote. Plant B, by contrast,
  lands exactly on 6.0.

## Fallback model

A Statistical Process Control scorer runs alongside the primary model and is surfaced only
when the primary fails to score (a corrupt or incompatible `.npz`), labelled `FALLBACK
MODE` in the response so a fallback number is never presented as the real model speaking.

**Measured shadow performance: precision 3.0%, recall 100%** (plant A; 2.9% / 100% on
plant B). That is barely above the base rate — as a standalone detector it is not usable,
and the number is published rather than softened. `docs/DECISIONS.md` ADR-035 has the
reason: the textbook 8-rule Nelson battery assumes a stable i.i.d. process, and this line
is deliberately simulated with autocorrelated physics, so the trend rules fire constantly
on normal behaviour. The live request-time path (`serve/fallback_scorer.py`) therefore
implements **only** the single 3-sigma rule, which does not inherit that failure mode.

## Drift monitoring — built, and what is still plan

Built and running (`mlops/drift_detector.py`, surfaced at `GET /api/model/health` and in
the Ops view):

1. **PSI per feature**, top 20 by permutation importance. Warn > 0.1, alert > 0.2.
2. **Calibration drift**: predictions binned into fixed deciles, alert if the largest gap
   between mean predicted probability and observed defect rate exceeds 15pp.
3. **Sensor-coverage drift**: alert if the fraction of fully-instrumented units drops more
   than 5pp below the training period — the model receiving worse data than it was fitted on.

Stated honestly: "current" is the model's own held-out test split, ordered by prediction
timestamp, because this build replays a fixed dataset rather than consuming a live feed.
`docs/MLOPS_LIFECYCLE.md` describes exactly what changes when a real feed exists (the
window is sourced from `station_readings` instead; nothing downstream changes).

Still plan, not built:

4. Weekly PR-AUC on the trailing 7 days with bootstrap CI, alarmed against the
   training-period interval.
5. Feedback precision: supervisor 👍/👎 as a live precision estimate against modelled
   precision at the operating threshold. The feedback is collected and audited today, but
   nothing yet computes a rolling precision from it.
6. Segmentation by ambient humidity and variant mix — the known weak regimes. The drift
   detector currently measures the population as a whole.

## Reproducibility

Seed 42 (plant A) / 43 (plant B). `OMP_NUM_THREADS=1` is exported by the Makefile before
any sklearn import: OpenMP reduction order is not deterministic and without it the
byte-for-byte claim would be false.
