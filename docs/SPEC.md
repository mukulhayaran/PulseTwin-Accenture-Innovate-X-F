# PulseTwin — Canonical Spec

**This is the build target.** Where this file and `pulsetwin_final_prompt.md` disagree,
**this file wins**. Every deviation is justified in `docs/DECISIONS.md` (ADR numbers are
cited inline). The original prompt remains unmodified as the historical brief.

Sections unchanged from the original are marked *(as original)* and are not restated —
read them there. Everything else is corrected here.

---

## 0. Operating constraints

- **Runtime memory:** target < 200 MB RSS. Treated as engineering discipline, not a gate —
  report the measured figure honestly (ADR-018).
- **Serve layer:** the import wall in `CLAUDE.md` §2 is authoritative (ADR-019).
- **Precompute heavy, serve thin.** Heavy libraries exist only in `offline/`.
- **Determinism:** seed 42, `OMP_NUM_THREADS=1` everywhere (ADR-016).
- **Time budget:** under 24 hours. `docs/STATE.md`'s ladder governs what gets built.

---

## 1. The Tuesday Problem — scenario spine

### The factory

A mixed-model line builds **SEDAN** (50%), **SUV** (35%), **EV** (15% normally).
30 stations, three zones.

- **Body Construction (S1–S12)** — welding, structural fastening.
  Sensors: `weld_current_a`, `torque_nm`, `vibration_mm_s`.
  **S10 is manual tier** (no continuous sensors).
- **Paint (S13–S18)** — sensors: `humidity_pct`, `temperature_c`, `film_thickness_um`.
  **S15 is manual tier.** Canonical naming (ADR-013):

  | Station | Name | Tier |
  |---|---|---|
  | S13 | Paint Pre-Treatment | full |
  | S14 | Primer Application | full |
  | S15 | **Paint Surface Prep** | **manual** — Mode B origin |
  | S16 | Base Coat Application | full |
  | S17 | **Clear Coat Application** | full |
  | S18 | Cure Oven | full |

- **Final Assembly (S19–S30)** — sensors: `torque_nm`, `vibration_mm_s`.
  **S22 and S27 are manual tier.** S30 is the final quality gate.
  EV variant carries extra battery work at S19–S24 (1.4× cycle multiplier).

### Cycle times — CORRECTED (ADR-002)

**Nominal cycle times are 45–62 s, never up to 90 s.** A 90 s station caps the line at
40 UPH, making the 55 UPH target — and every economic figure derived from it — impossible.

**S19 "Battery/Drivetrain Mount" base cycle = 55 s.** With the EV 1.4× multiplier an EV
unit takes 77 s, so on EV-heavy days S19 becomes the binding constraint **as a consequence
of the physics**, not because a config field declares it. S12 sits at 61 s flat and is the
normal-day constraint.

> **Why 55 s and not 58 s** (corrected during Phase 1). What decides the constraint is the
> *variant-weighted mean* cycle, not the EV cycle. At base 58 s:
> `58 × (1 + 0.15 × 0.40) = 62.5 s` on a normal day, which already exceeds S12's 62.1 s —
> **S19 would bind on every day and the bottleneck would never shift.** The entire Tuesday
> scenario would be gone. At 55 s: normal 59.3 s < S12 62.1 s < peak 64.6 s, a ~4.5 % margin
> either side. Caught by `PlantConfig.check_bottleneck_shift_configured`, which fails the
> config if the constraint does not actually move with variant mix.

All economics are computed from **achieved simulated throughput** read out of the
simulator, not from the nominal 55 UPH target.

### The cascade

1. EV units bottleneck at S19–S24. The constraint shifts S12 → S19.
2. The bottleneck **blocks** upstream paint stations S15–S18. Units sit in the paint buffer
   past the optimal cure window.
3. Extended dwell → **Defect Mode A: paint micro-blistering** (time-sensitive cure chemistry).
4. **On the unblocking burst** (ADR-026), the S15 buffer drains at once, a queue becomes
   visible, and shift-quota pressure compresses the operator's checklist from ~45 s to
   20–25 s for the next 3–5 units → **Defect Mode B: adhesion failure**.
   *An operator at a blocked station gains nothing by rushing — the trigger is the
   unblock transition, not the blocked state. This mechanism is stated in `ASSUMPTIONS.md`.*
5. Neither defect is visible until **S30**, 25–40 minutes later.
6. The two modes **correlate** (shared cause) but have **different root causes**
   (physics vs. human behaviour). Finding one does not find the other.

**Mode C** (structural: S3 torque deficit × S8 vibration excess, interaction required) is
independent of the Tuesday cascade and provides a second, unrelated defect population.

### Why it is hard to learn

- Mode A depends on paint-buffer dwell (observable), humidity (observable), and a
  **latent paint-batch quality** variable (unobservable, changes every ~200 units).
- Mode B depends on operator behaviour at a station with **no continuous sensors** — only
  the checklist binary, the checklist duration, and inter-unit timestamp gaps.
- Both fire only when EV mix is high **AND** humidity > 55% **AND** the paint batch is
  weak — co-occurring in ~30% of production hours.
- **Incoming lot quality** is an unobservable confounder.
- Inspection at S30 is imperfect: **92% detection, 0.2% false-reject** (ADR-001).

### Targets — CORRECTED

| Quantity | Value | Note |
|---|---|---|
| Realised defect rate | 2.5–4.0% | Two Tuesday modes ≈ half; Mode C the rest |
| Label noise | ~3% | Independent flip. **Now actually true** given ADR-001 |
| PR-AUC | 0.75–0.88 **advisory** | Report the measured value with bootstrap CI |

> **The PR-AUC band is advisory, never a tuning target** (ADR-020). A 3-day test split
> holds ~130 positives, giving a CI near ±0.08 — wider than the band, so the original's
> "if > 0.92 make the world harder / if lower find the bug" rule can fire on pure noise.
> Report the measured value and its interval. An honest 0.71 with a defensible pipeline
> beats a tuned 0.85.

### Simulation horizon — CORRECTED (ADR-020)

**21 days**, not 14. Train days 1–14 · validate 15–17 · test 18–21. Temporal split only.
Raises test positives from ~130 to ~200 and tightens the CI. Nearly free under ADR-005.

---

## 2. Repo structure

*(as original)*, with these changes:

```
artifacts/
├── pulsetwin.db
├── model_plant_a.npz        # ADR-006 — per plant, not one shared model
├── model_plant_b.npz
├── stream_plant_a.jsonl
├── stream_plant_b.jsonl
└── metrics.json

frontend/public/config.json  # ADR-028 — runtime API base
```

`artifacts/` is **gitignored during the build** and force-added in one final commit (ADR-027).

---

## 3. Hard rules

Rules 1–9 *(as original)*. Rule 10 (the import wall) is superseded by `CLAUDE.md` §2,
which adds `csv`, `logging`, `re`, `collections`, `functools`, `itertools`, `hashlib`,
`enum`, `sys`, `io` (ADR-019).

`tests/test_serve_imports.py` parses the AST of files under `serve/` and checks
**first-party imports only** — it must not walk fastapi's transitive graph.

---

## 4. Phases

### PHASE 1 — Configs & economics

`configs/plant_a.yaml` *(as original)* with corrections:

```yaml
plant:
  units_per_hour_target: 55        # target; achieved throughput comes from the simulator
  # every economic figure uses ACHIEVED, not this (ADR-002)

stations:
  # 30 stations, nominal_cycle_s in 45..62 ONLY (ADR-002)
  # S19 "Battery/Drivetrain Mount": nominal_cycle_s: 55, ev multiplier 1.4 -> 77s per EV
  #   variant-weighted mean: normal 59.3s < S12 62.1s < peak 64.6s  -> the shift happens
  # S12 "Body Completion":          nominal_cycle_s: 61                      normal-day constraint
  # manual tier: S10, S15, S22, S27
  # paint zone naming per ADR-013

defect_physics:
  mode_a: {weight: 0.45, dwell_threshold_s: 180, humidity_threshold_pct: 55,
           paint_batch_weak_fraction: 0.3}
  mode_b: {weight: 0.35, contributing_station: 15, checklist_rush_threshold_s: 25,
           trigger: unblock_burst}          # ADR-026 — NOT the blocked state
  mode_c: {weight: 0.20, contributing_stations: [3, 8], interaction_required: true}

  # ADR-025 — explicit normalisation. Mode weights are the MODE BUDGET only.
  # propensity = bias + mode_scale*(w_a*A + w_b*B + w_c*C) + w_lot*lot + w_noise*noise
  bias: <calibrated>
  mode_scale: <calibrated>
  w_lot: <calibrated>              # unobservable confounder
  w_noise: <calibrated>
  # all four calibrated so realised defect rate lands in 2.5-4.0%; fitted values -> ASSUMPTIONS.md

  base_defect_rate: 0.03
  label_noise_rate: 0.03
  inspection_detection_rate: 0.92
  inspection_false_reject_rate: 0.002    # ADR-001 — was 0.02
  inline_gate_s18: false                 # ADR-014 — dropped, avoids selection bias

simulation:
  duration_days: 21                # ADR-020 — was 14
  seed: 42
  lot_change_interval_units: 200
```

`configs/economics.yaml` *(as original)* plus (ADR-024):

```yaml
finance:
  discount_rate:
    value: 0.10
    source: "Typical automotive manufacturing WACC, 2024-2026 range"
  annualisation_basis:
    operating_days_per_year: 250
    note: "Simulation runs continuously for signal density; annualisation uses a
           250-day 3-shift year. Stated explicitly in ASSUMPTIONS.md."
```

`configs/plant_b.yaml` — 42 stations, 8 manual, body S1–S16 / paint S17–S24 /
final S25–S42, TRUCK variant instead of EV. Same schema, same 45–62 s cycle discipline.

**Acceptance:** a Pydantic schema validates all three configs. A validator asserts
`max(nominal_cycle_s) <= 3600 / units_per_hour_target` so ADR-002 cannot silently regress.

---

### PHASE 2 — Simulator

**Event-driven, not tick-based (ADR-005).** Advance the clock to the next event —
completion, breakdown, repair, buffer transition. Never iterate seconds.

Persist state as **intervals**: `(station_id, state, start_ts, end_ts)`. Per-second state is
reconstructed by interval lookup where a chart needs it.

`line_model.py` — states `idle | working | blocked | starved | down`; finite output buffers;
blocking and starving; exponential breakdowns from MTBF with gamma repair times; **tool-wear
drift** creeping effective cycle time 0.1–0.5%/hour and resetting after repair (this is what
makes the bottleneck *shift*).

`physics.py` — signals must be temporally autocorrelated and cross-correlated. Never draw
independent values per row. Mean-reverting random walks for tool wear, thermal inertia
`T(t) = α·T(t-1) + (1-α)·(T_ambient + heat)`, EWMA-filtered vibration that spikes on
blocking→release bursts. Manual-tier stations emit **NaN for continuous sensors** and a
checklist record instead. Never forward-fill an all-NaN column.

`operators.py` — skill pools per shift; rotation within zone; **the S15 rush fires on the
unblock transition** (ADR-026), compressing the checklist for the next 3–5 units.

`causal_engine.py` — the propensity formula of ADR-025. `paint_batch_quality` and
`incoming_lot_quality` are **unobservable** and never reach the feature matrix.

`inspection.py` — S30 only (ADR-014). 92% detection, 0.2% false-reject. `true_defect` stored
separately for offline validation and never visible to the model.

**Acceptance:**
- 21 days for both plants in **< 5 minutes total**
- ~27,000–33,000 units per plant (21 days)
- realised defect rate 2.5–4.0%
- S10, S15, S22, S27: 100% NaN continuous sensors, populated checklists
- at least one bottleneck shift S12 → S19 on high-EV days
- signal autocorrelation > 0.3 for torque, vibration, temperature
- non-zero blocked **and** starved time across multiple stations
- both Tuesday defect modes present in `defect_mode`
- **achieved UPH within 10% of the 55 target** — guards ADR-002

---

### PHASE 3 — Featurisation & leakage discipline

One row per unit, using **only** data available at or before S20. Features *(as original)*.

**`lot_trailing_defect_rate` — CORRECTED (ADR-010).** The trailing window filters on
`inspection_ts <= prediction_ts`, **never by unit index**. At S20 the most recent ~30–60
units of a lot have not yet been inspected (S30 is 25–40 min downstream); selecting by index
silently ingests future labels.

**Leakage tests — the primary guard is a timestamp assertion:**
1. For every feature of every unit, `max(source_row_timestamp) <= unit.s20_exit_ts`.
2. Temporal split ordering: train 1–14 < validate 15–17 < test 18–21. Never random.
3. Column-name scan for station IDs > 20 — retained as a **secondary** guard only.

> Test 1 is the one that matters. The original's name-scan cannot see a timestamp violation,
> and an inflated PR-AUC from this bug would send us rewriting the simulator to fix a
> feature bug.

---

### PHASE 4 — Soft sensors

*(as original.)* Per-signal quantile regressors for each manual-tier station (S10, S15,
S22, S27), inputs from adjacent instrumented stations + variant + ambient + dwell +
checklist + **hashed operator bucket** (ADR-017).

**Masked-station validation is the strongest credibility moment in this project and is
not cut under time pressure.** Mask S9 and S21 (both fully instrumented), predict from
adjacent stations only, report R² and MAE against the withheld truth in `MODEL_CARD.md`.

Ablation: defect model with vs. without soft-sensed features. **Report the delta honestly,
including if it is negative or zero.**

Every inferred value carries `provenance: "inferred"`, `ci_low`, `ci_high` — through DB,
API, and UI badge.

---

### PHASE 5 — Predictive engine

**5.1 Defect model.** `HistGradientBoostingClassifier`. Class imbalance via
`scale_pos_weight`, never resampling (resampling + temporal features = leakage).
Train 1–14, validate 15–17, test 18–21.

**Calibration: Platt scaling, not isotonic (ADR-021).** ~90 validation positives is far too
few for isotonic, which would fit noise into a coarse step function and then look
miscalibrated on test — discrediting the reliability diagram meant to prove calibration.
Platt also exports to two floats, simplifying the numpy scorer.

**Metrics — every headline figure carries a bootstrap CI, 1,000 resamples (ADR-020):**
PR-AUC (primary) · precision at alarm budget · recall at that threshold · median lead time
to S30 · units-in-flight at alert · reliability diagram (10 deciles, with bands) ·
ROC-AUC (secondary).

**5.2 Bottleneck detection.** Use the **Roser active-period method** — the station with the
longest *average active period* (working + down, i.e. not blocked and not starved) in a
sliding window. Not "longest uninterrupted working state," which ties constantly at
1-second granularity.

**Ground truth must be defined, not assumed.** The simulator does not inherently "know" the
constraint. Define it as: the station whose marginal cycle-time reduction most increases
throughput, measured by short perturbation runs. Document the definition in `MODEL_CARD.md`
or the reported detection accuracy is unfalsifiable.

15-minute forecast benchmarked against a persistence baseline; report
`skill = 1 − MAE_model/MAE_baseline`. **If it does not beat persistence, say so.**

**5.3 Thresholds — TWO thresholds, and they disagree (ADR-004).**

```
cost-optimal:      p* = C_alarm / (C_miss + C_alarm) = 45 / 7,045 ≈ 0.0064
budget-constrained: p  such that alerts/shift <= 6   ≈ 0.62
```

Store both per unit: `above_cost_threshold` and `alert_fired` (budget-gated top-N by risk
per shift). The UI operates on `alert_fired`. The Leadership view states the gap and
quantifies the value knowingly forgone:

> "Pure cost optimisation says alarm at p > 0.006 — around 200 alerts per shift. Human
> factors research caps a supervisor at 6–8 (Wickens, 2008). We operate at p > 0.62 and
> quantify what that costs. **The binding constraint is human attention, not model quality.**"

Also compute the full alarm-budget curve (threshold sweep → alerts/shift vs. precision vs.
recall) with the operating point marked.

---

### PHASE 6 — Build artifact

Schema *(as original)* with these changes:

```sql
-- ADR-004: two thresholds, replacing the single alert_fired
ALTER TABLE units ADD above_cost_threshold INTEGER;  -- economics-optimal
ALTER TABLE units ADD alert_fired          INTEGER;  -- budget-gated, what the UI shows

-- ADR-023 / L26: /api/stream needs this to seek to from_ts
CREATE TABLE stream_index (
    plant_id TEXT, ts TEXT, byte_offset INTEGER,
    PRIMARY KEY (plant_id, ts)
);
```

**`station_readings` is populated for the 48-hour replay window only (ADR-008)** — full
history is ~3.6 M rows and 220 MB+, against a 50 MB cap. Full-history views read the hourly
`station_stats` rollups.

**Model export — per plant (ADR-006):** `model_plant_a.npz`, `model_plant_b.npz`. Flat
numpy arrays (`feature_indices`, `thresholds`, `left_children`, `right_children`,
`leaf_values`, `tree_offsets`) plus Platt `A`, `B`.

**`serve/scorer.py`** — vectorised numpy tree traversal, Platt calibration, and **exact
TreeSHAP in pure numpy (ADR-015)**, so `/api/predict` can report
`"explanation_mode": "exact"` rather than an undefined "approximate".

**Parity tests:** numpy scorer vs. sklearn, and numpy TreeSHAP vs. the `shap` library, both
on the full test set, both asserting max absolute difference < 1e-6.

**What-if grid (ADR-007):** 6 candidate stations × 5 buffer deltas × 3 cycle deltas =
90 runs per plant, each on a 4-hour window. `new_bottleneck_station` is resolved by
**nearest neighbour, never interpolated** — it is a categorical station ID.

**Acceptance:** `pulsetwin.db` < 50 MB · each `model_*.npz` < 5 MB · both streams exist ·
both parity tests pass · `metrics.json` carries every metric **with its CI**.

---

### PHASE 7 — FastAPI serve layer

Endpoints *(as original)* with these corrections:

**`GET /api/health`** — RSS read from `/proc/self/statm` where it exists, else
`{"rss_mb": null, "rss_source": "unavailable"}` (ADR-018). **Must not throw on Windows.**

**`GET /api/stream`** — async generator with `await asyncio.sleep()`, **never**
`time.sleep()` (ADR-009). A blocking sleep freezes the event loop and makes every other
endpoint and browser tab unresponsive under `--workers 1`.

Required response headers:
```
Cache-Control: no-cache
Connection: keep-alive
X-Accel-Buffering: no        # HF Spaces' proxy buffers streams without this
```
Keepalive comment every 15 s. Server-side downsampling at speed ≥ 25× (L25) so the client
is not asked to render ~1,375 events/sec.

Stream events additionally carry `alerts_used` / `alerts_budget` for the current simulated
shift, computed server-side (ADR-012) — the client renders the budget counter, it never
accumulates it.

**`POST /api/predict/defect`** — `"explanation_mode": "exact"` (ADR-015).

**`GET /api/trace/containment/{unit_id}`** — `shared_factors` emits **opaque group labels**
(`operator_group_C`), never a stable operator ID (ADR-017).

**`POST /api/feedback`** — writes to a **separate** `/tmp/pulsetwin_feedback.db` created at
startup. `artifacts/pulsetwin.db` is opened `file:...?mode=ro` so an accidental write
raises loudly (ADR-011). Shared connections use `check_same_thread=False` plus a lock.

**Acceptance:** import-wall test passes · every endpoint schema-checked against committed
artifacts · < 300 ms (what-if < 1 s) · **`/api/health` and `/api/metrics` still respond
while an SSE client is connected** — this is the regression test for ADR-009 · health
endpoint does not throw on Windows.

---

### PHASE 8 — Next.js dashboard

**Next.js 15** (not 14 — Node v24 is installed, ADR-022), App Router, TypeScript strict,
Tailwind, Recharts, `output: 'export'`.

Design language, palette, and the three persona views *(as original)*. Load the **`design`
skill** to mock the views before writing React, and the **`dataviz` skill** before writing
the first chart.

Corrections:
- **Alert budget counter is server-derived** (ADR-012). No `localStorage`, and no client-side
  accumulation — it survives refresh and view navigation.
- **SSE events are coalesced with `requestAnimationFrame`** before hitting React state (L25).
- **`ValueBridge` waterfall is built from a stacked bar with a transparent base series** —
  Recharts has no native waterfall (L24).
- **API base is read at runtime from `public/config.json`** with the env var as fallback
  (ADR-028), so pointing at a different backend does not require a rebuild.
- Every inferred value renders the amber `INFERRED` badge with its CI; every dollar figure
  carries a source tooltip.

---

### PHASE 9 — Containerisation

*(as original)* with:
- `serve/requirements.txt`: plain `uvicorn`, not `uvicorn[standard]` (ADR-018);
  `numpy>=2.1` (ADR-022).
- Container stays `python:3.11-slim`.
- **Docker Compose is the first thing cut** under time pressure (`STATE.md` Tier 2) —
  `uvicorn` + `next dev` demos identically.

---

### PHASE 10 — Documentation

*(as original.)* `ASSUMPTIONS.md` must additionally cover:

- The **unblock-burst rush mechanism** and why a blocked operator rushing would be
  irrational (ADR-026).
- The **annualisation basis** reconciling continuous simulation with 250 operating days
  (ADR-024).
- The **discount rate** behind the 3-year NPV (ADR-024).
- The **calibrated propensity weights** and how the defect rate was tuned (ADR-025).
- The **ground-truth bottleneck definition** used to score detection accuracy.

`ARCHITECTURE.md` must state plainly that **permissive CORS and the unauthenticated
feedback endpoint are a demo posture**, not the production design (L28) — the document
argues for read-only Purdue-level discipline, and a judge will notice the gap if we do not
name it first.

`MODEL_CARD.md` records the ADR-014 decision (no S18 gate) and its rationale, all metrics
with CIs, and known failure modes.

---

## 5. Demo data seeding — CORRECTED

**Hero unit fires after S18, not between S8 and S12 (ADR-003).**

A **SEDAN** that:
1. passes S3 with torque ~8% below target,
2. hits S8 with vibration in the 95th+ percentile,
3. sits in the paint buffer (S14 exit → S16 entry) ~195 s against a ~120 s norm, on a humid
   day with a weak paint batch,
4. passes S15 in ~22 s against a ~45 s norm, on an unblock burst,
5. **crosses the budget-gated threshold immediately after S18.**

At that point **all three** root causes on the alert card are real and computed. Lead time
S18 → S30 is ~22 min, matching the demo script unchanged.

5–8 other in-flight units must share the lot and/or the S15 operator group at that moment,
for the containment panel. The bottleneck must be at S19 during the replay window, and the
window must contain at least one S12 → S19 shift.

> **Say the sedan thing out loud in the demo.** The hero unit is not an EV, yet it is damaged
> by an EV-heavy schedule through the shared paint buffer. That is a cascade a
> single-station metric cannot possibly reveal — which is the entire thesis.

---

## 6. Anti-patterns

*(as original)*, plus:

- ❌ `time.sleep()` anywhere in `serve/` (ADR-009)
- ❌ Selecting a trailing window by index rather than by timestamp (ADR-010)
- ❌ Interpolating a categorical station ID (ADR-007)
- ❌ Writing to `artifacts/pulsetwin.db` at runtime (ADR-011)
- ❌ A stable operator ID in any API response or UI element (ADR-017)
- ❌ Tuning the simulator to land inside the PR-AUC band (ADR-020)
- ❌ Reporting a metric without its confidence interval (ADR-020)
- ❌ Calling anything "SHAP" that is not computed as SHAP (ADR-015)
- ❌ Training with `OMP_NUM_THREADS` unset while claiming reproducibility (ADR-016)
- ❌ `git push`, or any commit trailer (`CLAUDE.md` §1)
