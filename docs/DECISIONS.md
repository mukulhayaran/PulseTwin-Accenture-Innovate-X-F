# Architecture Decision Records

Every deviation from `pulsetwin_final_prompt.md`, with the reasoning. If you think a
decision here is wrong, **add a superseding ADR** — do not silently revert to the original
prompt. Each entry maps to a finding in `docs/AUDIT.md`.

Status key: `ACCEPTED` (settled) · `PROVISIONAL` (revisit if time allows) · `SUPERSEDED`

---

### ADR-001 — Inspection false-reject rate drops to 0.002
**Audit:** B1 · **Status:** ACCEPTED

*Original:* `inspection_false_reject_rate: 0.02` alongside a claim of "~3% label noise".

*Problem:* At a 3% base rate this makes 41% of positive labels wrong, which is
incompatible with the stated PR-AUC ceiling of 0.75–0.88.

*Decision:* `inspection_false_reject_rate: 0.002`. The 3% independent label flip remains
as the sole stated noise source, so "label noise ~3%" becomes true.

*Cost:* Slightly less pessimistic inspection. Mitigated by keeping detection at 0.92, so
escaped defects (the thing that actually motivates the product) are unchanged.

---

### ADR-002 — Station cycle times are 45–62 s, not 45–90 s
**Audit:** B2 · **Status:** ACCEPTED

*Original:* 55 UPH target with 45–90 s cycles — mutually exclusive, since a 90 s station
caps the line at 40 UPH.

*Decision:* Nominal cycles 45–62 s. S19 base at **55 s**, so an EV unit takes 77 s and the
Tuesday bottleneck emerges from the physics rather than being asserted.

*Amended during Phase 1:* the first draft of this ADR said 58 s. That is wrong. The
constraint is decided by the *variant-weighted mean* cycle, not the EV cycle. At 58 s the
normal-day mean is `58 × (1 + 0.15 × 0.40) = 62.5 s`, which already exceeds S12's 62.1 s —
S19 would bind on every day and **the bottleneck would never shift**, deleting the Tuesday
scenario. At 55 s: normal 59.3 s < S12 62.1 s < peak 64.6 s, roughly 4.5 % margin either
side, which is crisp enough to demo while still leaving tool-wear drift room to make the
detection problem genuinely non-trivial.

*Guard:* `PlantConfig.check_bottleneck_shift_configured` fails any plant config whose
predicted constraint does not move between the base and peak variant mixes. Plant B is
held to the same rule (S16 → S31), so the mechanism is proven to generalise rather than
being tuned per plant.

*Consequence:* Every economic figure is computed from **achieved simulated throughput**,
read out of the simulator, not from the nominal 55 UPH. `economics.yaml` records the
nominal as a target and the achieved as the basis, with both sourced.

*Bonus:* This is a better demo. "The constraint moves to S19 because EV battery work makes
it an 81-second station" is a causal explanation; a hardcoded bottleneck is not.

---

### ADR-003 — The hero demo unit alerts after S18, not between S8 and S12
**Audit:** B3 · **Status:** ACCEPTED

*Original:* Hero unit crosses threshold between S8 and S12, but its alert card shows paint
buffer dwell (S14→S16) and S15 checklist time as root causes — features that do not exist
yet at S8.

*Decision:* The hero alert fires immediately after **S18**. All three demo root causes
(Mode A paint dwell, Mode B S15 rush, Mode C upstream vibration) are real and computed at
that point.

*Consequence:* Lead time S18→S30 is ~12 min of processing plus buffer dwell, which lands
at the ~22 min the demo script already quotes. **The script needs no renumbering.**

*Also:* The hero unit stays a SEDAN. It is caught by the cascade through the shared paint
buffer despite not being an EV — which is a *stronger* narrative point, since it shows the
EV-heavy schedule damaging non-EV units. Say that out loud in the demo.

---

### ADR-004 — Publish two thresholds and show that they disagree
**Audit:** B4 · **Status:** ACCEPTED

*Original:* One "cost-optimal threshold" that also respects a 6-alerts-per-shift budget.

*Problem:* The cost optimum is p\* ≈ 0.0064; the budget implies p ≈ 0.62. Two orders of
magnitude apart. No single number satisfies both.

*Decision:* Compute, store and display both.
- `above_cost_threshold` — economics-optimal, in the DB per unit.
- `alert_fired` — budget-gated (top-N by risk within each shift), what the UI shows.

The Leadership view states the gap explicitly and quantifies the value forgone by
respecting human attention limits.

*Rationale:* This is a better answer to rubric bullet 7 than a reconciled number. It says
the binding constraint is human attention, not model quality, and pre-empts the obvious
judge question instead of hiding from it.

---

### ADR-005 — Event-driven simulation, interval-based state storage
**Audit:** B5 · **Status:** ACCEPTED

*Original:* 1-second tick loop; record per-station-per-second state.

*Problem:* 36.3 M station-tick updates per plant in pure Python; multi-GB output. Cannot
meet the "< 5 minutes" acceptance check.

*Decision:* Advance the clock to the next event (completion / breakdown / repair / buffer
transition). Persist state as `(station_id, state, start_ts, end_ts)` intervals. Per-second
state is reconstructed by interval lookup wherever a chart needs it.

*Cost:* Slightly more intricate scheduler. Pays for itself immediately — it is also what
makes the what-if replays in ADR-007 affordable.

---

### ADR-006 — One trained model per plant
**Audit:** B6 · **Status:** ACCEPTED

*Original:* A single `artifacts/model.npz` serving both plants.

*Problem:* Features are positional (`s3_torque_nm`). Plant B has 42 stations, different
zones and a TRUCK variant — a different feature space entirely. One ensemble cannot score
both.

*Decision:* `artifacts/model_plant_a.npz` and `model_plant_b.npz`; the scorer selects on
`plant_id`. Feature *construction* remains fully config-driven.

*What we claim on stage:* "The same pipeline, given a different YAML, produces a different
line, a different feature space and a different trained model, with zero code changes."
That is both true and a stronger engineering claim than one universal model — real
multi-plant deployments train per site.

---

### ADR-007 — Reduced what-if grid on a short window
**Audit:** H8 · **Status:** ACCEPTED

*Original:* 30 stations × 13 buffer deltas × 9 cycle deltas = 3,510 full 14-day runs per
plant (~39 h of compute).

*Decision:* 6 candidate stations (current bottleneck ± neighbours) × 5 buffer deltas
(−4, −2, 0, +4, +8) × 3 cycle deltas (−10%, 0, +10%) = **90 runs per plant**, each on a
4-hour window.

*Also:* `new_bottleneck_station` is categorical and is resolved by **nearest-neighbour**,
never interpolated. The API response labels its `mode` honestly.

*Cost:* The console only offers stations that matter. Defensible — a manager tunes the
constraint, not station 4.

---

### ADR-008 — `station_readings` covers the replay window only
**Audit:** H7 · **Status:** ACCEPTED

*Original:* One row per `(unit, station, signal)` across all history — ~3.6 M rows, 220 MB+,
against a 50 MB cap.

*Decision:* Populate `station_readings` for the 48-hour replay window only (~230 k rows).
Full-history queries are served from hourly `station_stats` rollups, which is what the
Manager and Leadership views actually consume.

---

### ADR-009 — Async SSE with proxy-hardening headers
**Audit:** H9 · **Status:** ACCEPTED

*Original:* `time.sleep(interval / speed)` in the stream handler, `--workers 1`.

*Problem:* Blocking sleep freezes the event loop — one SSE client makes the entire API
unresponsive to every other request and tab.

*Decision:* `await asyncio.sleep()` in an async generator. Plus, on the streaming response:
`Cache-Control: no-cache`, `Connection: keep-alive`, `X-Accel-Buffering: no`, and a
keepalive comment every 15 s.

*Why the headers matter:* Hugging Face Spaces proxies buffer streamed responses. Without
`X-Accel-Buffering: no` the stream works perfectly on localhost and delivers nothing in
the deployed demo.

---

### ADR-010 — Leakage is enforced by timestamp assertion, not name scanning
**Audit:** H10 · **Status:** ACCEPTED

*Original:* `test_no_leakage.py` scans column names for station IDs > 20.

*Problem:* `lot_trailing_defect_rate` selects "the last 100 units of this lot." Selected by
index, that ingests inspection results from 25–40 minutes in the future. A name scan
cannot see this.

*Decision:*
1. The trailing window filters on `inspection_ts <= prediction_ts`. Never by index.
2. The primary leakage test asserts, per feature per unit, that the maximum source-row
   timestamp is ≤ that unit's S20 exit timestamp.
3. The name scan stays as a cheap secondary guard.

*Why this matters most:* An inflated PR-AUC from this bug would trigger the spec's own
"simulator is too clean" rule and send us rewriting the simulator to fix a feature bug.

---

### ADR-011 — Read-only artifact DB; feedback to a separate writable DB
**Audit:** H11 · **Status:** ACCEPTED

*Decision:* Open `artifacts/pulsetwin.db` with a `file:...?mode=ro` URI so any accidental
write raises immediately. Feedback goes to `/tmp/pulsetwin_feedback.db`, created at
startup. Shared connections use `check_same_thread=False` plus a lock.

`ARCHITECTURE.md` documents the production path (durable event sink) and states plainly
that feedback is ephemeral on the free tier.

---

### ADR-012 — Alert budget is computed server-side
**Audit:** H12 · **Status:** ACCEPTED

*Original:* Client-side accumulation, but `localStorage` is banned — so the counter resets
on every refresh.

*Decision:* The server counts `alert_fired` rows inside the current simulated shift window
and returns `alerts_used` / `alerts_budget` with the stream. The client renders; it does
not accumulate.

*Bonus:* Correct by construction — the budget belongs to the shift, not to a browser tab,
and it survives a judge navigating between the three views.

---

### ADR-013 — Paint zone follows the §4 naming
**Audit:** M13 · **Status:** ACCEPTED

*Original:* §1 prose (S15 = base coat, S17 = cure) contradicts §4 config
(S15 = "Paint Surface Prep", S17 = "Clear Coat Application").

*Decision:* §4 wins — the defect physics and demo script both depend on it.
Canonical paint zone:

| Station | Name |
|---|---|
| S13 | Paint Pre-Treatment |
| S14 | Primer Application |
| S15 | **Paint Surface Prep** — MANUAL tier, Mode B origin |
| S16 | Base Coat Application |
| S17 | **Clear Coat Application** |
| S18 | Cure Oven + In-Line Gate |

Paint-buffer dwell for Mode A is measured **S14 exit → S16 entry**, spanning the manual
S15, exactly as the feature spec states.

---

### ADR-014 — Drop the S18 in-line quality gate
**Audit:** M14 · **Status:** PROVISIONAL

*Original:* Optional gate at S18 catching ~40% of Mode A defects.

*Problem:* Units removed at S18 never reach S30 and never receive a label. The training
population becomes conditioned on surviving S18, so the model systematically under-observes
Mode A — the exact mode the whole narrative rests on — and Mode A recall is measured on a
censored sample.

*Decision:* **Drop the gate for the initial build.** S30 is the sole label source.

*Revisit if time allows:* Reinstate it and train on the union of both label sources; it
becomes a good "partial early signal" talking point. Not worth the censoring analysis under
a 24-hour budget. Recorded in `MODEL_CARD.md` either way.

---

### ADR-015 — Exact TreeSHAP in numpy, inside the import wall
**Audit:** M15 · **Status:** ACCEPTED

*Original:* `/api/predict` returns `top_factors` with `"explanation_mode": "approximate"`,
never defining "approximate", while `shap` is a banned import in `serve/`.

*Decision:* Implement exact TreeSHAP over the exported ensemble in pure numpy (~100 lines)
in `serve/scorer.py`. Report `"explanation_mode": "exact"`.

*Verification:* `tests/test_scorer_parity.py` extends to compare the numpy TreeSHAP output
against the offline `shap` library on the test set, asserting max abs difference < 1e-6 —
the same discipline already applied to the scores.

*Fallback if it slips:* serve precomputed per-unit SHAP from `unit_factors` for replayed
units, and label ad-hoc predictions `"global_attribution"`. **Never** invent contributions
and call them SHAP.

---

### ADR-016 — `OMP_NUM_THREADS=1` for all training
**Audit:** M16 · **Status:** ACCEPTED

Multithreaded OpenMP reductions are not bit-reproducible. Every training and export entry
point sets `OMP_NUM_THREADS=1` before importing sklearn. The Makefile exports it. Without
this the "reproduces byte-for-byte" claim is false.

---

### ADR-017 — Operator identity is bucketed at the API boundary
**Audit:** M17 · **Status:** ACCEPTED

*Original:* Containment returns `same_operator_s15`; the UI renders `Opr15`.

*Decision:* `genealogy.operator_id` stays internal (containment joins genuinely need it).
The API emits only an opaque bucket label — `operator_group_C` — derived from a salted
hash, and no view ever renders a stable operator identifier. Featurisation likewise uses
8 hashed buckets, as the spec already intended.

*Why:* Design principle 9 is not decoration — it is the union-acceptance argument in the
business proposal. Violating it in the containment table would undercut a claim we are
making in writing.

*Correction (2026-09-16), found by auditing the docs against the code:* this ADR says
"salted hash" and `configs/data_governance.yaml` said `sha256`. **Neither is what was
built.** `offline/build_artifact.py` uses `pandas.util.hash_pandas_object(operator_id) %
8` — unsalted, and not a cryptographic hash. The privacy outcome the ADR was protecting
is still delivered (the raw `operator_id` is never written to any table, and 8 buckets
make the label describe a group rather than a person), so the decision stands and no code
changed. The *description* was wrong and is now corrected in
`configs/data_governance.yaml` and `docs/DATA_GOVERNANCE.md`.

The lesson is worth keeping: a privacy claim stated more strongly than the implementation
supports is the kind of thing an auditor finds, and it costs more credibility than the
weaker true claim ever would have. If real cryptographic pseudonymisation is ever
required — it is not, at 8 buckets — that is a code change, not a wording change.

---

### ADR-018 — RSS reporting is platform-guarded; 200 MB is a target, not a gate
**Audit:** M18 · **Status:** ACCEPTED

*Decision:* `/api/health` reads `/proc/self/statm` when it exists and returns
`rss_mb: null, rss_source: "unavailable"` otherwise, so **local Windows development does
not crash**. `serve/requirements.txt` drops `uvicorn[standard]` to plain `uvicorn` (saves
uvloop + httptools; the demo does not need their throughput).

Measure real RSS in the container before optimising further. If it exceeds 200 MB, report
the true number honestly rather than weakening the architecture — a free Hugging Face Space
provides 16 GB, so the constraint is a self-imposed engineering discipline, and it is more
credible to state the measured figure than to claim a round number.

---

### ADR-019 — Corrected import allowlist
**Audit:** M19 · **Status:** ACCEPTED

The authoritative list lives in `CLAUDE.md` §2 and adds to the original: `csv` (which §0
allowed and §3 dropped), `logging`, `re`, `collections`, `functools`, `itertools`,
`hashlib`, `enum`, `sys`, `io`.

`tests/test_serve_imports.py` parses the AST of files under `serve/` and checks
**first-party import statements only**. It must not walk the transitive dependency graph —
fastapi imports starlette and anyio, and a transitive check would fail immediately and
tempt someone to weaken the test that protects the core constraint.

---

### ADR-020 — Bootstrap CIs on every headline metric; 21 simulated days
**Audit:** M20 · **Status:** ACCEPTED

A 3-day test split holds ~130 positives, giving PR-AUC a ±0.08 confidence interval — wider
than the 0.75–0.88 target band, so the spec's control rule can fire on noise.

*Decision:* Report bootstrap CIs (1,000 resamples) alongside every headline metric, in
`metrics.json`, the API, `MODEL_CARD.md` and the Leadership metrics panel. Simulate
**21 days** rather than 14 (train 1–14, validate 15–17, test 18–21), raising test positives
to ~200. Cheap under ADR-005. Treat the 0.75–0.88 band as advisory, never as a tuning target.

---

### ADR-021 — Platt scaling instead of isotonic
**Audit:** M21 · **Status:** ACCEPTED

~90 positives in the validation fold is far too few for isotonic regression, which would
fit a coarse step function to that sample's noise and then look miscalibrated on test —
discrediting the reliability diagram meant to prove calibration.

*Decision:* Platt (logistic) calibration. Exports to just two floats (`A`, `B`) instead of
a step-function pair of arrays, which also simplifies `model_<plant>.npz` and the numpy
scorer. Reliability diagram is plotted with confidence bands.

---

### ADR-022 — Environment pins
**Audit:** L22, L23 · **Status:** ACCEPTED

- `numpy>=2.1` — `1.26.4` has no cp313 wheels and local Python is 3.13.2. The container
  stays `python:3.11-slim`; the pin range covers both.
- **Next.js 15** — Next 14 targets Node 18/20 and the installed runtime is Node v24.19.0.
- `serve/requirements.txt` uses plain `uvicorn` (see ADR-018).

---

### ADR-023 — Additional schema not present in the original
**Audit:** L26, B4, H12 · **Status:** ACCEPTED

```sql
CREATE TABLE stream_index (        -- L26: /api/stream needs this to seek to from_ts
    plant_id TEXT, ts TEXT, byte_offset INTEGER,
    PRIMARY KEY (plant_id, ts)
);
```

Plus two columns on `units`, replacing the single `alert_fired` (ADR-004):
`above_cost_threshold INTEGER` and `alert_fired INTEGER` (budget-gated).

---

### ADR-024 — Economics gaps closed
**Audit:** L27, L29 · **Status:** ACCEPTED

`economics.yaml` gains:
- `discount_rate: 0.10` with a source — required by the 3-year NPV the business proposal
  promises, and currently the only unsourced figure in a project whose whole discipline is
  sourcing figures.
- An explicit `annualisation_basis` reconciling the 24/7 simulation with
  `operating_days_per_year: 250`. The simulation models continuous operation for signal
  density; annualisation uses 250 days. Stated in `ASSUMPTIONS.md`, not left implicit.

---

### ADR-025 — Defect propensity normalisation
**Audit:** L30 · **Status:** ACCEPTED

Mode weights (0.45 / 0.35 / 0.20) sum to 1.0 and are the **mode budget only**. The full
propensity is:

```
propensity = bias
           + mode_scale  * (w_a·mode_a + w_b·mode_b + w_c·mode_c)   # w sum to 1.0
           + w_lot       * incoming_lot_quality                      # unobservable
           + w_noise     * gaussian_noise
```

**Amended in Phase 2 — the additive logistic does not work.** Built form:

```
P(defect) = 1 - (1 - h_lot) * (1 - h_noise) * PROD_i (1 - h_i)
h_i       = k * w_i * intensity_i        intensity in [0,1], normalised by exposure
```

*Why the change:* the three modes fire at very different frequencies — Mode A on ~60 % of
units at low intensity, Mode C on ~2.7 % at high intensity. Under an additive logit the
single intercept needed to hold the rate at 3 % sat so low that **no individual mode could
clear it alone**; a unit only defected when several modes coincided, and Mode C produced
**4 defects out of 759** against a configured weight of 0.20. Normalising by the signal
mean instead caused the opposite failure — rare intense units normalised to ~190,
saturated the logistic at P = 1.0, and the intercept search pinned at its floor.

Competing risks matches the physics: each mode is an independent hazard and a unit
survives only if it survives all of them, so any mode can cause a defect on its own.
Intensity is divided by its population mean (zeros included) so `w_i` expresses each
mode's **share of defects** rather than its per-unit hazard; the 0.85 hazard cap keeps
rare-intense modes risky without making them deterministic.

*Measured result (plant A, 21 days, 26,883 units):* rate 2.99 %, and realised mode shares
of 44.5 / 31.8 / 17.5 % against configured weights of 45 / 35 / 20 %. 6.6 % of defects are
`unattributed` — caused by the latent lot confounder, and genuinely unpredictable from
observable data. That residual is a real ceiling on achievable PR-AUC, which is the point.

`k` is the single calibrated scalar (bisection on the target rate). Its fitted value and
the intensity normalisation constants are recorded in `ASSUMPTIONS.md`.

---

### ADR-026 — Mode B's behavioural mechanism, stated explicitly
**Audit:** L31 · **Status:** ACCEPTED

An operator at a **blocked** station gains nothing by rushing — output cannot move
downstream. The mechanism only makes sense on the **unblocking burst**: the downstream
buffer drains at once, a queue of waiting units becomes visible, and shift-quota pressure
drives checklist compression from ~45 s to 20–25 s for the next 3–5 units.

The simulator implements the trigger on the *unblock transition*, not on the blocked state.
`ASSUMPTIONS.md` states the mechanism and cites queue-pressure behaviour, because a judge
with manufacturing background will ask, and "the operator rushed while blocked" is not a
defensible answer.

---

### ADR-027 — Artifacts are committed once, at the end
**Audit:** P33 · **Status:** ACCEPTED

SQLite does not delta-compress; a ~50 MB blob per phase commit stays in history forever.
`artifacts/` is gitignored during the build and force-added in a single final commit.

*(Moot for publishing — this repo is never pushed — but it keeps the local repo workable.)*

---

### ADR-028 — Frontend reads its API base at runtime
**Audit:** L28-adjacent · **Status:** PROVISIONAL

`output: 'export'` bakes `NEXT_PUBLIC_API_BASE` in at build time, so pointing the UI at a
different backend means a full rebuild — a bad property to discover ten minutes before a
demo.

*Decision:* Ship `public/config.json` with the API base, fetched client-side on mount, and
fall back to the env var. One fetch, no rebuild, works with static export.

*Provisional:* If time is short, accept the rebuild and document it. Low risk either way.


---

### ADR-029 — What-if grid: both constraints, a full shift, at peak mix
**Audit:** H8 (extends ADR-007) · **Status:** ACCEPTED

Three corrections found by querying the finished grid rather than trusting it:

1. **Seed from both constraints.** Candidates were picked around the single most frequent
   bottleneck (S12), producing a grid over S9–S14 that omitted **S19 entirely** — the EV
   bottleneck, and the one station a manager would most want to tune. Now seeded from the
   top two constraints, interleaved so both neighbourhoods are covered.

2. **One full shift, not four hours.** A 4-hour window never reached steady state: the
   line starts empty and buffers take ~30 minutes to fill, so nothing blocks until they
   do. The run reported **57.5 UPH — above the 55 UPH target, which is impossible in
   steady state** — and named S3 as the constraint.

3. **Force the peak variant mix.** The simulator starts on a Monday, so a short run used
   the NORMAL mix, S19 never bound, and every buffer change at S19 correctly evaluated to
   zero effect. The grid now forces the peak mix, because the EV-heavy day is precisely
   the condition being tuned for. The API says so in its `assumptions` string rather than
   leaving it implicit.

4. **Buffer means the FEED buffer.** The slider originally changed the selected station's
   *output* buffer, and every delta at S19 returned an identical **52.707 UPH** — the
   control did nothing at all. That is correct physics: a bottleneck is never blocked by
   what follows it, so its downstream capacity is irrelevant. But it is not the question a
   manager is asking. Applying the delta to the buffer *feeding* the station gives the
   lever that actually exists:

   | feed buffer | throughput | constraint |
   |---|---|---|
   | −2 | 52.32 UPH | S19 |
   | 0 | 52.71 UPH | S19 |
   | +2 | 53.12 UPH | S3 |
   | **+4** | **54.08 UPH** | **S12** |
   | +8 | 53.29 UPH | S3 |

   Textbook constraint behaviour — buffering the feed stops the constraint starving on
   upstream variability, throughput climbs to an optimum, and past it the constraint
   migrates and further buffer stops paying. The UI labels the control **FEED BUFFER** and
   the API's `assumptions` string says which buffer moved.

5. **Constraint detection was fill-biased.** Scoring cumulative active time over the whole
   window favours upstream stations, which have simply seen more units while the line
   fills — plant B reported **S2** as its constraint on a 42-station line. Now scored on
   the second half of the window only.

*Cost:* ~110 s per plant instead of ~55 s. Worth it — the previous grid was fast and wrong.

---

### ADR-030 — Publish the soft-sensor failure alongside the success
**Audit:** Phase 4 · **Status:** ACCEPTED

Masked-station validation initially reported **R² = −0.304**, worse than predicting the
mean. Investigation showed this was not a bug:

| signal family | neighbour correlation | masked R² |
|---|---|---|
| ambient-driven (paint temperature / humidity / film thickness) | 0.98 | **+0.97 to +1.00** |
| tool-wear-driven (torque, vibration) | 0.06 | **−0.55 to −0.07** |

Each station's tool wear is an independent random walk, so a neighbour carries almost
nothing about it. **No model can recover a signal that is not there.**

*Decisions:*
- Add a **paint station to the masked set** so the contrast is measured rather than
  asserted. Both original masked stations happened to be from the hard family, which made
  the soft sensor look uniformly broken when it is not.
- **Reduce regressor capacity** so an absent signal reports ≈0 rather than a misleadingly
  negative number produced by overfitting noise.
- **Publish both results.** The negative one is the more useful: it tells the
  instrumentation planner which retrofits are unnecessary (the value is inferable) and
  which are unavoidable (genuinely unobservable).

The separate ablation shows unsensored-station evidence is worth **+0.060 PR-AUC**, so the
legacy stations do matter — but the value is in the operator checklist record, not in
reconstructed continuous signals. That distinction is the actionable finding.

---

### ADR-031 — A zero retrofit estimate is a result, not a gap
**Audit:** Phase 4 · **Status:** ACCEPTED

Three of four manual stations score zero estimated uplift from instrumentation, including
**S15 — the Mode B origin, and the second strongest driver in the entire model.**

That is correct, and worth stating plainly: S15's operator checklist already captures the
signal that matters there, and the defect it drives is *behavioural, not environmental*. A
temperature/humidity sensor would buy information we already get for free.

The recommendation reads `DEFER - checklist already captures this signal` rather than a
bare "no benefit", because "do not spend here, you already have the data" and "this
station does not matter" are completely different conclusions for a capital plan.

---

### ADR-032 — Served units are scoped to the replay window only
**Audit:** Phase 6 · **Status:** ACCEPTED

*Found by a test, not by inspection:* `test_unit_detail_carries_factors_and_journey`
failed on plant B with an empty journey.

The `units` table kept every alert-fired unit regardless of when it ran, while `genealogy`
and `station_readings` are window-scoped (ADR-008). An alert from outside the replay
window therefore had a row in `units`, a risk score, and explanations — but **no station
history at all.** Clicking it in the UI showed an empty Unit Tracker.

*Decision:* the replay window (±2 h) is the only filter on `units`. Every served unit is
now complete.

*Cost:* none that matters. The demo replays this window, so those were the only alerts
ever reachable; full-history figures come from the `metrics` tables, not from `units`.

---

### ADR-033 — Import wall extended for production I/O drivers, not compute
**Audit:** Finals upgrade · **Status:** ACCEPTED

The original wall (ADR-019) banned everything not needed to *serve precomputed artifacts*.
The finals upgrade adds real infrastructure — Postgres, MQTT, Redis, JWT — that the
prototype never touched. The question is whether that violates "precompute heavy, serve
thin," and it does not: none of these are compute libraries. They are I/O drivers, the
same category `fastapi` and `uvicorn` already occupy.

*Decision:* extend `ALLOWED` in `tests/test_serve_imports.py` and CLAUDE.md §2 with:

| Package | Top-level import | Why it's a driver, not compute |
|---|---|---|
| `psycopg[binary,pool]` | `psycopg`, `psycopg_pool` | Postgres wire protocol client + connection pool (separate distribution, same driver family). No query planning, no ORM, no pandas-style frames — rows come back as tuples/dicts. |
| `redis` | `redis` | Key-value cache client. Used today by `ingestion/feature_buffer.py`'s real-time feature assembly. Allowlisted here (not yet installed in `serve/requirements.txt`) for the documented-but-not-built Redis-backed degraded-read cache — see docs/SECURITY.md / docs/PRODUCTION_ARCHITECTURE.md for what "if Postgres is slow or unreachable, serve from cache" would look like; building it for every read endpoint was scoped out of this round in favour of the pieces judges actually probe (MLOps, RBAC, ingestion resilience). |
| `paho-mqtt` | `paho` | MQTT pub/sub client used only by `ingestion/`, which is a **separate deployable** from `serve/` — listed here because the import wall test globs `serve/` and `ingestion/mqtt_subscriber` never imports into `serve/`. Kept in the table for completeness; not actually added to `serve/`'s allowlist. |
| `python-jose[cryptography]` | `jose` | JWT decode/verify for the documented OIDC upgrade path (`docs/SECURITY.md`). Pure cryptographic verification, not a modeling library. |
| `prometheus-fastapi-instrumentator` / `prometheus_client` | `prometheus_client`, `prometheus_fastapi_instrumentator` | Metrics counters/histograms/gauges — bookkeeping, not computation. |

*Still banned, no exception:* `pandas`, `sklearn`, `xgboost`, `lightgbm`, `shap`, `scipy`,
`simpy`, `torch`, `tensorflow`, `psutil`, `yaml`, `offline`. Nothing that trains, frames,
or vectorises at scale crosses the wall. A Postgres row still arrives as a `dict`, exactly
as a SQLite row does — `serve/db.py` normalises both backends to the same shape
(datetimes and Decimals stringified/floated at the boundary) so no downstream code, and no
Pydantic schema, needs to know which database answered the query.

*Cost:* the allowlist is longer and needs a human to keep reading `docs/DECISIONS.md`
before adding #7. That is the point of the wall being a test, not a comment.

---

### ADR-034 — Postgres's real FK caught what SQLite silently allowed
**Audit:** Finals upgrade, Phase F1 · **Status:** ACCEPTED

*Found by a constraint, not a test:* `offline/load_to_postgres.py` failed loading plant B
with `ForeignKeyViolation: genealogy -> units, key (U027206, plant_b) is not present`.

SQLite never enforced foreign keys here (`PRAGMA foreign_keys` was never turned on in
`offline/build_artifact.py`), so `genealogy` and `units` have been silently allowed to
disagree since ADR-032. They disagree because they are windowed on **different
timestamps**: `units` keeps `prediction_ts` within the replay window +/- a 2h margin;
`genealogy` keeps `entry_ts` within the replay window with **no** margin. A unit that
enters the window early but reaches its prediction cutoff station late can fall outside
`units`'s margin while its early-station visits still fall inside `genealogy`'s window —
giving it station history with no parent row.

This is exactly the ADR-032 problem in reverse (that ADR fixed units with no journey;
this is a journey with no unit), and it only surfaced now because Postgres's FK
constraint (added per this finals brief, ADR-033's migration) actually checks it.

*Decision:* keep the FK. Filter the SOURCE query in `load_to_postgres.py` instead --
`genealogy` rows only load `WHERE unit_id IN (SELECT unit_id FROM units WHERE plant_id=?)`
-- rather than relaxing the constraint to accept orphans. A constraint that gets weakened
the first time it catches something is not a constraint.

*Cost:* a small number of genealogy rows (stations visited by units whose prediction
landed outside the served window) never reach Postgres. Nothing in the API serves them --
`/api/units/{id}` 404s for units outside `units`, so their genealogy rows were always
unreachable dead weight, in SQLite too.

---

### ADR-035 — The textbook Nelson-rule fallback is nearly useless on THIS line, and that's published
**Audit:** Finals upgrade, Phase F3 · **Status:** ACCEPTED

`mlops/fallback.py`'s shadow evaluation (`evaluate_shadow`) measures the classical
SPC/Nelson-rule battery against the full replay dataset: **35,927 of 35,939 units
flagged** -- shadow precision 3.0%, recall 100%. That is barely above the base defect
rate; as a standalone detector it is not usable.

*Why, honestly:* the 8 Nelson rules assume a stable, i.i.d.-around-its-mean process. This
line is deliberately simulated WITHOUT that property -- ADR-005's own physics are
"temporally autocorrelated signals... mean-reverting random walks for tool wear, thermal
inertia." Rule 3 ("6 consecutive points steadily trending") and rule 2 ("9 consecutive on
the same side of the mean") are near-guaranteed to fire on a slowly drifting real physical
signal, because that drift is the process behaving NORMALLY, not an assignable cause. The
rules are doing exactly what they are designed to do; the process just doesn't satisfy
their precondition.

*Decision:* publish the number rather than discard or retune the rule set toward a nicer
one (the same discipline as ADR-030/031's soft-sensor findings). Two consequences follow
directly from this finding, not around it:

1. `serve/fallback_scorer.py` (the REQUEST-TIME path `/api/predict/defect` actually
   surfaces) deliberately implements ONLY the 3-sigma rule (Nelson rule 1), never the
   full battery -- a genuine single-point 3-sigma outlier stays rare even on an
   autocorrelated signal, so the live fallback does not inherit this failure mode. That
   design choice predates this measurement (it was made for computational reasons -- no
   consecutive-unit history available per request) and turns out to be the statistically
   correct one too.
2. `GET /api/model/health` reports `shadow_precision`/`shadow_recall` as measured, with no
   floor or rounding that would hide a number this bad. A judge asking "how do you know
   the fallback works" gets the true answer: on this line, the conservative single-rule
   version is what's trustworthy, the textbook full version is not.

*Cost:* the "SPC-based fallback" story is weaker than a from-a-textbook citation would
suggest for a manufacturing line with genuine physical autocorrelation -- which is a more
useful thing for a judge to learn than a fabricated 60%/40% precision/recall pair would
have been.

---

### ADR-036 — Two more import-wall exceptions: `starlette`, `subprocess`
**Audit:** Finals upgrade, Phase F4/F6 · **Status:** ACCEPTED

*Found by the wall test itself* (`test_only_allowed_imports[rate_limit.py]` and
`[admin.py]`), which is exactly what it is for.

1. `serve/middleware/rate_limit.py` imports `starlette.middleware.base.BaseHTTPMiddleware`.
   FastAPI has no ASGI middleware base of its own -- it *is* Starlette underneath, and
   writing custom middleware without this class means hand-rolling raw ASGI
   (scope/receive/send), which is strictly worse: more code, more chance of a bug in the
   protocol handling, for a dependency that is already installed as fastapi's own
   requirement and adds nothing to the runtime image. This is a direct, single-symbol
   import, not the transitive graph the test's own docstring says it must not walk.
2. `serve/routers/admin.py` imports `subprocess` to launch `python -m mlops.retrainer` as
   a **detached process** rather than importing `mlops` (which pulls in pandas/sklearn
   transitively through `offline.ml.train`) into `serve/`. `subprocess` here is process
   management, the same category as `threading` (already allowed) -- it starts a job and
   walks away; it does not compute anything itself.

*Decision:* add both to `ALLOWED` in `tests/test_serve_imports.py` and to CLAUDE.md SS2.

*Still banned, no exception:* everything in ADR-033's and ADR-019's banned lists. Neither
addition lets a heavy library through -- `admin.py` still cannot `import mlops`.

---

### ADR-037 — The GenAI tier sits outside the import wall, and its output is gated, not trusted
**Audit:** Agentic triage · **Status:** ACCEPTED

An AI-focused audience does not recognise a calibrated HistGB ensemble as AI, so an
agentic layer was added. The brief never asked for one (`pulsetwin_final_prompt.md`
contains no mention of LLM, GenAI, agentic or natural language), so it is judged purely
on whether it strengthens the project without weakening its thesis.

That thesis — *measured, not assumed* — is what makes the design non-obvious. A language
model is the one component here that cannot be regenerated deterministically. Bolting a
chatbot onto a project whose distinguishing feature is an oracle ceiling and published
negative results would trade the strongest thing it has for the most common thing at a
hackathon.

*Decision 1 — `genai/` is a separate deployable; the wall is NOT widened.*
It joins `ingestion/` and `mlops/` as a tier `serve/` never imports. `genai` is absent
from `tests/test_serve_imports.py`'s `ALLOWED` set, so that test already fails on any
`import genai` inside `serve/` — **it needed no edit to cover this feature**, which is a
better outcome than an amendment. The backend triggers a run with `subprocess.Popen`
exactly as it triggers retraining (ADR-036): trigger heavy, don't host it.

This also preserves a security claim that a naive implementation would have broken.
`configs/deployment.yaml` sets `network.internet_egress_allowed: false` for the backend
pod and `infrastructure/kubernetes/network-policy.yaml` enforces it; a hosted LLM call
from `serve/` would contradict both. An IT-tier job with its own egress policy does not,
and the backend's allow-list stays `[postgres, redis]` verbatim.

*Decision 2 — the agent reads through the HTTP API, not the database.*
`serve/db.py::open_ops_only()` returns a *writable* handle on the Postgres path, so a
direct-DB tool layer would hand the agent write capability and "read-only" would rest on
nobody having typed an INSERT. Over HTTP its entire vocabulary is ten function names, it
carries `X-PulseTwin-Role`, and a 403 becomes a fact it must report rather than an
exception. `serve/routers/triage.py::_cap_role` caps the acting role at the caller's own
— without it, an agent is a read-around for the role gate.

*Decision 3 — the tool registry IS the data-egress allowlist.*
Ten plant-level aggregate endpoints. Unit-level surfaces (`/api/units/{id}`,
`/api/trace/containment/{id}`) and anything reading `checklists` are excluded: shipping
per-unit genealogy or operator-bucketed rows to a third-party API is the worst
data-governance step this feature could take, and the best privacy defence is that the
data never leaves. `tests/test_genai_tools.py` asserts the registry's path set *equals*
the approved list, so widening egress requires editing a test — the same trick
`test_serve_imports.py` plays on the import wall.

*Decision 4 — verification is the feature; the model is not trusted.*
The agent returns claims, each carrying the tool call and RFC 6901 pointer its number
came from. `genai/verifier.py` — no model, no network, no clock — re-checks all four
ways a number can be wrong: the citation resolves (C1), the value matches within a
tolerance *derived from the claim's own printed precision* (C2), every number in the
prose is accounted for (C3 — the claim table is the part a model tries hard on, the
summary is where it relaxes), and the three contracts hold (C4, including a cross-check
of claimed provenance against the cited row's own `provenance` column).

Failure **redacts or rejects**; it does not flag. A flagged number is still a rendered
number and a reader will read it. A rejected run serves no finding at all and says why —
the same posture as ADR-030/031's published negative results.

*Decision 5 — a deterministic path that passes the same gate.*
`genai/offline_triage.py` runs the same tools in a fixed order and assembles a finding in
code. It means the feature demos and tests with no credential, it survives a provider
outage mid-demo, and — the reason that matters — it is an honest **baseline**: whether
the agent found anything the rules did not is then a measurable question rather than an
impression.

*Cost:* one more tier to deploy, one more allowlist to review, and a provider dependency
that only `genai/requirements.txt` carries. The unverifiable surface of the whole feature
is one API call's wire format, isolated to `genai/client.py`.

*Found while building this:* `serve/db.py::open_ops_db()` opened SQLite with no WAL and
no busy timeout. The file is genuinely multi-process — the backend holds a long-lived
connection while detached children write — so a spawned job died on `database is locked`
while its row sat at `queued` forever. `mlops.retrainer` uses the identical pattern and
has the same latent flakiness; retraining is just rare enough that nobody hit it. Now
WAL + `busy_timeout`. Found by spawning the job from a running server, not by reading:
every standalone `python -m ...` run had passed, because a standalone process contends
with nothing.

---

### ADR-038 — The copilot: a new provider, a wider egress allowlist, and a request that degrades
**Audit:** AI Operations Copilot · **Status:** ACCEPTED

ADR-037 built an agentic tier that answers one question, once, about line balance. The
copilot turns that into a conversation an operator can actually use, and three things
had to change to allow it. Each is a loosening of something ADR-037 deliberately
tightened, so each is argued here rather than assumed.

*Decision 1 — the provider is Groq; the DEPENDENCY does not change.*
`genai/config.py` now points at `https://api.groq.com/openai/v1` with
`openai/gpt-oss-120b`, and the credential is `GROQ_API_KEY`. Groq speaks the OpenAI wire
protocol, so this is a base-URL change inside the existing abstraction: no second
provider SDK, which CLAUDE.md §2 and ADR-037 both ban outright. `genai/client.py` is
still the only file in the repository that imports a provider SDK, and
`genai/requirements.txt` is still the only file that declares one.

Two consequences worth stating. The rate card in `genai/store.py` was xAI's, so
`triage_runs.cost_usd` would have been a dollar figure whose stated `cost_source` named a
price list that was never used — contract #2 broken by the feature that enforces contract
#2. It now carries Groq's published rates for this model with the date they were read.
And `serve/routers/triage.py::API_KEY_ENV` is a hand-maintained mirror whose own comment
says changing one without the other is a bug; both moved in the same commit, and
`tests/test_triage_api.py` reads the name symbolically so the assertion cannot drift.

*Decision 2 — the request degrades; the guarantee does not.*
An OpenAI-compatible endpoint is not an OpenAI endpoint. `strict` on tool schemas, a
nested `json_schema` response format and `seed` are all model-dependent on Groq, and a
rejection is an HTTP 400 that `classify_error` maps to `provider_status_400` — which
**failed the entire run**. Pointing the tier at a model that dislikes strict schemas
would have turned every question into a hard error with no diagnosis.

`genai/capabilities.py` walks a five-rung ladder against the real payload (not a startup
probe — a probe costs a round trip on the happy path and can pass while the real request,
carrying eighteen tools plus a nested schema, fails on the *combination*). The order
surrenders the guarantees this package has already documented as non-load-bearing, weakest
first: `strict` goes first because `tools.py::to_wire` already says `validate_args`
re-checks locally regardless; `json_schema → json_object → absent` next because
`finding.py::parse_finding` already says it works whether or not the provider honoured
the format; `seed` last because `config.py` already disclaims that it makes a hosted model
reproducible.

It degrades on 400 and 422 only. Deliberately **not** on 401, 403, 404 or 413: a wrong
key or a wrong model id must fail loudly on the first attempt rather than walk four rungs
and then fail anyway with a misleading profile recorded. Widening that set is how this
module would start hiding real misconfiguration. The settled rung is persisted as
`triage_runs.capability_profile` and rendered — a degraded run is visibly degraded, which
is the same posture as labelling the SPC fallback.

*Decision 3 — the egress allowlist widens from ten paths to eighteen, on purpose.*
ADR-037 excluded unit-level surfaces because "shipping per-unit genealogy to a
third-party API is the single worst data-governance step this feature could take". That
reasoning holds for `/api/units/{id}`, which returns a vehicle's full station journey and
raw readings, and it is still excluded. It does not hold for the question the copilot is
actually asked — *which vehicles are affected?* — because
`/api/trace/containment/{unit_id}` answers exactly that and already emits opaque operator
GROUPS by construction (ADR-017). The narrower tool answers the question; the wider one
just exports the line.

Admitted: containment, the alert list (bounded by `limit`), deterministic alert triage,
a shift summary, data quality, sensor dropouts, the evidence graph, and the
instrumentation plan. Still absent, and still a governance decision: `/api/units/{id}`,
anything reading `checklists` (that table carries `operator_group`, and the best privacy
defence remains that the data never leaves), `/api/stream` (unbounded SSE),
`/api/audit/*` (the agent must not read the record of who did what), and `/api/admin/*`
(mutating — the registry contains no tool that can change a model, a config, or the
line). `tests/test_genai_tools.py` pins the exact path set, so this paragraph and that
test have to move together.

*Decision 4 — the new evidence is computed in the backend, not by the model.*
`serve/routers/intelligence.py` decides, counts, ranks and links; `genai/` explains.
This is not tidiness. The verifier can only check a claim against a tool result, which
means the tool result is the one thing nothing downstream checks — so a triage class, a
shift statistic or a causal edge produced by a language model would be unfalsifiable by
construction. The evidence graph is built by a deterministic join and carries
`causality: "association"` on every response, because nothing in this repository
establishes causation: the features are correlated with a label, the fingerprint is a
shared-lot-and-window join, and the simulator's true causes are deliberately partly
unobservable (`docs/AUDIT.md` B34).

*Measured against the live endpoint, 2026-09-21 — and it changed the design.* The ladder
was written before anything had talked to Groq. What the real endpoint does:

- **Every rung carrying `response_format` fails**, with one identical 400: `json mode
  cannot be combined with tool/function calling`, `param: response_format`. Not the
  strictness — the parameter, at all, whenever `tools` is present. The ladder converged
  on L4 correctly but paid four rejected round trips to learn one fact, and on a free
  tier those four count against the provider's own limit. `degrade()` now reads the
  error text and jumps past every rung that would fail for the reason just stated. The
  rung order still earns its place on an endpoint whose objection IS the strictness.
- **A 400 is not always our fault.** Groq validates tool syntax server-side and returns
  `code: tool_use_failed` carrying the text it could not parse. Ours was
  `{"name": "citaton", "arguments": The current bottleneck in **plant_a** is...` — the
  model inventing a tool name and writing prose where the arguments JSON belongs, trying
  to emit its *citation* as a tool call. That is downstream of the first finding: with
  `response_format` unavailable, nothing pins the output shape but the prompt, which had
  been written assuming the schema would. Now classified separately, retried without
  touching the ladder, and the prompt states that the final answer is an ordinary message
  and that no citation tool exists.
- **The binding limit is tokens, not requests:** 8,000 TPM against 1,000 requests/day.
  One round trip costs ~3,200 tokens before any tool result, because the system prompt
  and eighteen tool schemas are re-sent every turn. The loop's flat five-second retry
  could not recover from a budget that resets in 17.1s; it now reads the provider's own
  `retry-after` / `x-ratelimit-reset-tokens`, bounded by the run's wallclock cap.

*Cost:* eight more paths leaving the network, a rate card tied to one model id, and a
ladder that is now part measured and part still a guess about endpoints nobody here has
run. A wrong guess costs one extra round trip, once per process.

*Found while building this — four real defects, none of them in the new code.*

1. **`serve/schemas.py::MetricsResponse` silently discarded six stored blobs.**
   `serve/main.py::metrics` splats all of `metrics_json` into the model, and pydantic v2's
   default `extra="ignore"` dropped every key the model did not declare — including
   `bottleneck_report` (the forecast detail behind the headline skill number) and
   `soft_sensor_validation` (the masked-station R² that ADR-031's negative result rests
   on). Both are computed, committed, and cited in the docs, and neither was reachable
   through any endpoint.

2. **`ingestion/data_quality.py` scored every weld reading invalid.** Its
   `VALID_RANGES["weld_current_a"]` was `(0, 2000)` while
   `offline/simulator/physics.py::_station_targets` generates `8200 + (station_id % 6) *
   140` — so all 8,610 weld readings in the committed artifact sit in 7.75–9.25 kA and
   were all being excluded from feature computation in the live path. Resistance spot
   welding runs at 8–15 kA; the bound was wrong by roughly a factor of five. Now 20 kA,
   with a test asserting the simulator's own output falls inside the ranges.

3. **The first version of `/api/alerts/triage` could not rank anything.** It triaged rows
   where `alert_fired = 1`, and that flag means `calibrated_risk >= budget_threshold` *by
   construction* — 0 of 141 fired rows across both plants violate it. Severity was
   therefore a tautology and every candidate landed CRITICAL. The right population is
   everything the cost model says is worth inspecting (`above_cost_threshold`, 1,423 rows
   on plant_a against 113 fired); the gap between those two numbers is this project's
   thesis, so triage is the surface that decides which of the 1,423 get some of the 113.

4. **The stored confidence interval carries no information.** Across both plants every
   one of ~1,250 distinct `calibrated_risk` values maps to exactly one interval width, and
   there are exactly as many distinct widths as distinct risks: the interval is a
   deterministic function of the point estimate. Ranking on it would have been
   double-counting one number and presenting the second copy as corroborating evidence.
   `LOW_CONFIDENCE` now keys on input quality — dark sensors, reconstructed station
   statistics — and the interval is returned flagged `informative: false`.

   Relatedly, and published rather than tuned away: on plant_a all 113 fired alerts share
   ONE calibrated risk, the top plateau of the isotonic calibration. Severity cannot
   discriminate there at all. The endpoint reports that in `diagnostics.notes` and ranks
   on lead time and peer count instead, which is the same posture as ADR-030/031 — a
   measurement that came out flat is reported flat.

---

### ADR-039 — Citations do not cross turns
**Audit:** AI Operations Copilot · **Status:** ACCEPTED

A conversation needs memory. The verifier's entire guarantee is that every served number
resolves to a tool result *in this run*. Those two facts collide, and the collision has a
silent failure mode that is worth more than the feature.

*The failure.* `genai/loop.py` assigns `call_id` per run — `call_0001` upward, restarting
every turn. Turn 1 and turn 3 both have a `call_0003`, pointing at different tool
results. If an earlier turn's citations were replayed as usable, a claim citing
`call_0003` would be resolved against **turn 3's** transcript. Two outcomes: the pointer
fails to resolve, and the agent looks amnesiac; or it resolves to an unrelated number
that happens to fall inside `derive_tolerance`, and the claim is marked **verified** and
traced to evidence that has nothing to do with it. A green badge on a coincidence,
silently reachable. That is the worst thing this feature could produce.

*Decision 1 — the rule is categorical, and it is in the prompt in those words.*
`CITATIONS DO NOT CROSS TURNS. The call_ids above no longer exist. To make any claim
about a number established earlier, CALL THE TOOL AGAIN NOW.` Re-fetching costs one call
out of twenty-four, and it is also the correct semantics: on a live line, a number
restated twenty minutes later should be restated against current data.

*Decision 2 — `carried_values`, and its scope is one check.*
Without some memory, C3's prose sweep shreds every follow-up: "the 41.8 UPH we
established is unchanged" loses its whole sentence. So values a previous turn's verifier
already passed are admitted as a **fourth exemption in `_sweep_prose` and nowhere else**.
A *claim* can still never be verified by a carried value — `_verify_claim` is untouched
on purpose, and the next person to read it will want to "fix" that.

Two filters that are easy to get wrong and are tested: only runs with status `verified`
or `verified_with_redactions` contribute (a rejected turn served nothing, so it has
nothing to carry), and claims whose text begins `[claim removed` are skipped — they carry
`value: 0.0` as a placeholder, and admitting it would exempt every sentence containing a
zero in every later turn, which is the easiest possible way to accidentally disarm the
uncited-number sweep.

*Decision 3 — the set is derived on the server, never supplied by a client.*
The obvious cheap implementation is to have the browser post prior findings back. That is
a security defect, not an inelegance: it would let the caller control both the text
entering the model's context and the verifier's exemption list, so a manager could paste
a fabricated "$4.2M established earlier" and have it restated with the sweep disarmed.
`_cap_role` exists to stop the agent being a read-around for the role gate; client-supplied
context would make it a write-around into the verifier.

*What this costs, named.* The guarantee weakens from *traceable to a tool result in this
run* to *traceable to a tool result in this thread*. It is bounded: carried values are
server-derived, persisted per run in `triage_runs.carried_values` so
`genai/verify_cli.py` still reproduces a turn's verdict byte-identically, surfaced as
`carried_values_used` in the verdict, and the earlier turn's evidence stays in
`triage_tool_calls` permanently. A stale value can still be restated as current an hour
later; the prompt says so and the UI badges it, and recency invalidation is not built.

*Decision 4 — named actions are intent KEYS, not canned sentences.*
"Generate Shift Handover" posts `intent: "handover"`. Not an English string chosen by the
browser, because then the deterministic path would have to string-match it, the behaviour
of a button would live half in TypeScript, and the ledger could not be queried without a
`LIKE`. `genai/intents.py` holds the expansion; `serve/` mirrors only the keys and short
labels, with a test asserting the two agree. There is deliberately no NL intent
classifier: typing "generate a handover" posts `intent: "ask"` and the agent handles it as
a question. A classifier would be a second unverifiable model call to save a click on a
button already on screen. For the same reason a station id is always **structured** —
regexing "S17" out of prose is how a copilot simulates the wrong station with complete
confidence.

*Decision 5 — every named action has a deterministic plan.*
`genai/offline_triage.py` grows `run_handover`, `run_shift_intel`, `run_whatif` and
`run_incident` beside the original. A button that works only when a provider is reachable
is a button that breaks on stage, and the argument for that module was always that the
demo survives an outage *with its guarantees intact*. All four are citation-bearing by
construction and pass the identical verifier on both plants.

`run_whatif` carries the one lesson that cost a rewrite: **a refusal needs evidence too.**
The what-if grid covers a fixed set of stations and two intervention types, so an
unsupported request must say the simulation is unavailable rather than substitute a
nearby scenario. But the verifier rejects a finding with no claims, and a rejected run
serves *nothing* — so the first version's honest refusal reached the operator as a blank
panel, which is strictly worse than the refusal it was trying to deliver. The refusal now
cites the grid's own coverage, and is served.

*Decision 6 — progress is polled, and the model's text is never streamed.*
The only thing worth streaming from a language model is its text, and streaming this
model's text would render numbers on a screen *before* the verifier runs — a viewer
watching "$4.2M annual saving" type itself out and then vanish is the exact opposite of
ADR-037's "failure redacts, it does not flag". So the run is non-streaming by design, and
what the UI polls is phase status: a ~400-byte document that changes maybe fifteen times
per run, which 1 Hz polling renders indistinguishably from a push. An SSE progress
endpoint was rejected for a second reason as well: `EventSource` cannot set
`X-PulseTwin-Role`, so it would be either ungated or role-in-query-string, and `serve/`
runs a single worker with a test already protecting its event loop from long-lived
connections.

The highest-leverage part of this is six lines in `serve/routers/triage.py`: the router
writes the first heartbeat **before** spawning the worker. The longest wait in a run is
interpreter startup plus the first model round trip, all of it before the worker can
write anything — the window that used to show a bare spinner at exactly the moment an
audience decides whether the thing works.

Phase labels live on `ToolSpec.phase`, not in a React map: a UI map desynchronises the
moment a tool is added, would need duplicating for the page and the dock, and would give
the deterministic path no labels at all. And because `openai/gpt-oss-120b` returns a
`reasoning` field, the heartbeat is assembled from an explicit key allowlist with a test
asserting model prose never reaches it — structure that is not tested is a convention.

*Found while building this:* `serve/db.py` creates the ops tables with `CREATE TABLE IF
NOT EXISTS`, which is a no-op against a table that already exists — and the ops database
is not a rebuilt artifact, it is a long-lived file in the system temp directory. Every new
column would have been missing at runtime on any machine that had started the server
once, with the first symptom an `OperationalError` from a detached worker whose stderr
goes to a log file nobody is reading. `_ensure_columns` now does an additive
PRAGMA-then-ALTER pass, and the index over the new columns had to move after it — the
first version failed at startup with `no such column: thread_id` because `CREATE UNIQUE
INDEX` ran inside the same script that was supposed to create them.

Separately, two tests in `tests/test_qa_regressions.py` inserted fixed `model_id` values
into that same long-lived ops file and never deleted them, so they passed exactly once per
machine and then failed forever on `UNIQUE constraint failed`. A test that only passes on
a clean machine is a test that fails on the demo machine.

---

### ADR-040 — OpenRouter/Nemotron, and a rung the ladder could not see
**Audit:** GenAI provider swap · **Status:** ACCEPTED

The provider moved again, from Groq's `openai/gpt-oss-120b` to OpenRouter's
`nvidia/nemotron-3-ultra-550b-a55b:free`. ADR-038's central claim survives the move
intact and is the reason the move was cheap: OpenRouter speaks the OpenAI wire protocol,
so this is a base-URL and a model id, `genai/client.py` is still the only file importing
a provider SDK, and `genai/requirements.txt` still declares exactly one. That is twice
now that a provider change has cost nothing structural, which is the whole argument for
the abstraction.

What it did cost was four defects, each invisible to the test suite because each one
lives in the seam between this repository and a real endpoint. They are recorded here
because three of them are general, not OpenRouter trivia.

*1 — The ladder degrades on a status code, and this endpoint never returns one.*
ADR-038 reasoned about `response_format` alongside `tools` as an HTTP 400: Groq refuses
that combination with `json mode cannot be combined with tool/function calling`, the
ladder reads the message and jumps. OpenRouter **accepts** the same request with HTTP 200
— and the model then emits no tool call at all, answering from nothing and wrapping it in
unbalanced JSON. `is_capability_rejection` never fires, so a cold run sat on L0, the top
rung, made zero tool calls, and was rejected as `unparseable_finding`. Every new model
starts on that rung, so this was the *default* first experience of the tier.

The fix is `capabilities.suppresses_tool_calls`: a soft probe on the first round trip
only. It required two attempts to get right, and the failed one is the instructive half.
The obvious rule — "no tool call on turn 0 means `response_format` broke it" — is wrong,
and two existing tests said so before any endpoint did. A model may legitimately answer
without calling anything; that answer carries no evidence and the **verifier** rejects
it, which is the correct place for the judgement and costs no extra request. The rule
that survives needs both halves: no tool call **and** output that is not even a JSON
object, when a JSON object is the one thing `response_format` was sent to guarantee. It
is also skipped once anything else has degraded the run, because an endpoint that
answered a 400 has already named the rung it wants and inferring from silence is only
sound while there has been silence.

*2 — The output schema only ever reached the model through `response_format`.*
Which means it never reached it at L4 — the rung the ladder lands on for **both**
providers. `prompts.SYSTEM` described the citation contract in detail and never named
`claims`, `summary`, `confidence` or any other key. gpt-oss-120b guessed the shape often
enough to hide this; Nemotron emitted a well-researched finding, six correct tool calls
and a role-capped `unanswered` entry, under a top-level `citations` key it invented, and
the parser threw `missing required field: claims`. The schema is now written out in the
prompt as well as sent as `response_format`, so the contract survives every rung.
`genai/finding.py::parse_finding` being tolerant does not help when the model was never
told what to be tolerant *of*.

*3 — The rate card is per-model, and nothing enforced that.*
`store.py` billed every run at Groq's gpt-oss-120b prices with a `cost_source` naming
them. Against a model that is free this produced $0.015 a run, carrying a source string
that named a provider the run never called — precisely the contract #2 violation ADR-038
congratulated itself on fixing, reintroduced by the same mechanism one provider later.
A rate card pinned to a model id, in a module where the model id is an environment
variable, is a latent wrong number. `_cost()` now prefers the provider's own
`usage.cost`, which OpenRouter returns on every response; the rate card survives only as
a labelled fallback for endpoints that report nothing.

*4 — Credential precedence picks the first name SET, not the right one.*
`LEGACY_KEY_ENVS` is ordered `GROQ_API_KEY, OPENROUTER_API_KEY, XAI_API_KEY`. An
environment carrying both a Groq key and an OpenRouter key therefore sends the Groq key
to OpenRouter, and the run dies on a bare `provider_status_401` — which the ladder
correctly refuses to degrade on, so the diagnosis is a single status code. The
provider-neutral `PULSETWIN_GENAI_API_KEY` is checked before all of them and is now what
the documented setup sets. The ordering is left alone: changing it would break exactly
the deployments the fallback exists for.

*Also fixed, because it is what made three of these take as long as they did:*
`finding_raw` was set to NULL whenever parsing failed, so the row recorded that the
output was unparseable and discarded the output. "Invented a schema" and "was cut off
mid-sentence" want opposite fixes and were indistinguishable from the database. The raw
text is now kept, capped, and still never served — `serve/routers/triage.py` drops
`finding_raw` from every response, so it stays operator-only.

*One property that is not a defect and cannot be fixed in code.* The `:free` suffix caps
the account at 50 requests per day across all free models, and one agent run spends 7-12
of them. Four or five conversations exhaust a day, after which every run fails
`rate_limited` — correctly surfaced in the UI, but a demo that must not run dry should
drop the `:free` suffix or fund the account. `genai/offline_triage.py` remains the answer
that needs no provider at all and passes the identical verifier.

---

### ADR-041 — Startup asks the registry which model is champion; it does not assume
**Audit:** Production-readiness pass, 2026-09-21 · **Status:** ACCEPTED

*Found by tracing the seam the audit was explicitly asked to trace, not by a failing
test:* `docs/STATE.md`'s own QA section already named "the model that serves traffic is
not in the registry" as the one open finding a judge could turn into a question the repo
could not answer. Tracing `serve/main.py::lifespan` end to end showed the actual defect is
a rung worse than that sentence states.

`lifespan()` loaded `artifacts/model_<plant>.npz` unconditionally on every startup and
never queried `model_registry`. Meanwhile `admin.py::_activate_scorer` (already correct,
per the 2026-09-16 fix logged in `docs/MLOPS_LIFECYCLE.md`) hot-swaps `STATE["scorers"]`
in memory AND writes a real `status='champion'` row with the promoted artifact's path the
moment `POST /api/admin/promote-model` runs. Those two facts together mean: a promotion
was real and correctly recorded, right up until the next restart -- at which point
`lifespan()` threw the registry's answer away and reloaded the pre-promotion committed
artifact, while `model_registry` kept confidently reporting the promoted challenger as
champion. `docs/MLOPS_LIFECYCLE.md` even asserted the fix for this ("the registry row...
is the source of truth in both cases") without any code making it true -- a documentation
claim the implementation did not back up, not a hidden design gap.

**Alternatives considered:**

1. *Do nothing; document the restart caveat more loudly.* Rejected -- the caveat was
   already documented (`_activate_scorer`'s own docstring) and it was still wrong,
   because "the committed artifact" is not what the caveat's audience (a judge, or a
   production on-call engineer) would infer "reverts to the committed champion" means
   once a promotion has actually happened. The registry actively disagreeing with the
   running process is worse than the registry being merely uninformative.
2. *Redis-backed live reconciliation across replicas* (a pub/sub or short-poll signal so
   every backend pod picks up a promotion within seconds, not just at its own next
   restart). This is the right eventual design for the documented 2-6 pod production
   topology (`docs/PRODUCTION_ARCHITECTURE.md`) -- today, a promotion via one API call
   only patches the ONE pod that received that request, and the others behind the Service
   keep serving the old model with no error and no indication. Deferred: it needs Redis
   wiring, a reconciliation test, and a decision about staleness bounds, which is exactly
   the kind of ambiguous architectural change CLAUDE.md says not to blindly patch. Flagged
   here as the known, unresolved multi-replica gap; `docker-compose.prod.yml` and the demo
   both run a single backend replica (ADR-009), so it does not affect anything currently
   deployed.
3. **Chosen:** make `lifespan()` ask `model_registry` for the current champion's
   `artifact_path` per plant at startup, exactly as `_activate_scorer` already does at
   promotion time, and load that instead of the hardcoded path. Falls back to the
   committed artifact if no champion row exists yet, or its file is missing (e.g. a
   registry copied to a machine without the challenger `.npz` files) -- a plant is never
   left unscored over a registry lookup. Additionally: bootstrap-register the committed
   artifact as `status='champion'` the first time a plant is served with no registry row
   at all, using REAL values pulled from the `metrics` table (`pr_auc`, `oracle_pr_auc`,
   `precision_at_budget`, `recall_at_budget`, `bottleneck_detection_accuracy`) rather than
   placeholders, keyed on a deterministic `model_id` so it is idempotent across restarts
   against the same long-lived ops DB (the exact file STATE.md bugs #69/#70 already found
   accumulates state indefinitely).

**Consequences:** a promotion now survives a restart on the single-replica demo/production
topology that actually exists today -- `serve/main.py::_resolve_champion_artifact` and
`_bootstrap_registry_champion`, tested end to end against the real committed artifact in
`tests/test_model_registry_runtime.py` (no mocked scorer, no mocked registry). "Which
exact model is currently serving plant X" is now always answerable from `model_registry`
alone, including for a plant that has never been retrained. The multi-replica staleness
gap (alternative 2) is real, unresolved, and now named in one place instead of being
implied-fixed by a doc sentence the code did not implement.

---

### ADR-042 — A live synthetic run has one server-owned clock and an explicit source label
**Audit:** Synthetic-live implementation, 2026-09-22 · **Status:** ACCEPTED

The browser-level JSONL replay remains useful for a reproducible recorded shift, but it
cannot represent a shared plant: every SSE connection owns its own clock, alert counter,
and position. Calling that transport “live” makes two browser tabs disagree about the
factory and overstates what is connected.

**Chosen:** preserve replay and add a separate live-synthetic run manager. One background
producer emits seeded events from the committed simulator stream into durable
`simulation_runs`, `simulation_events`, `unit_current_state`,
`station_current_state`, and `active_alerts` tables. All clients join the same run by ID.
The event envelope always carries `source: live_synthetic`, and the Supervisor renders a
permanent “no real factory data connected” label. The same envelope is the future MQTT,
OPC-UA, MES, Kafka, or QMS integration boundary.

The API stays behind the import wall: it does not import the heavy offline simulator or
training stack. Seeded scenario transformations use the already committed deterministic
stream, so fixed seeds reproduce and different seeds change synthetic variation without
tuning model quality upward.

**Consequence:** start/pause/resume/stop, shared state, temporal unit reads, durable alert
acknowledgement, containment decisions, and inspection validation work without real plant
data. MQTT remains a separate ingestion deployable and now projects the same event
envelope into these shared run tables, so both transports drive the same Supervisor APIs.

---

### ADR-043 — One versioned feature contract for offline and live scoring
**Audit:** Live parity implementation, 2026-09-22 · **Status:** ACCEPTED

Offline training and MQTT scoring previously calculated rolling signals independently,
making silent training/serving skew likely. A dependency-free `feature_contract.py` now
owns signal formulas, window sizes, and checklist codes. The artifact builder and the
Redis-backed live buffer both consume it. Prediction logs record model and feature-set
versions, prediction time, available/missing counts, completeness, run ID, and source.
Golden parity tests drive the actual Redis feature assembler for both plants and compare
every registered model feature (including missing-value positions) plus the final score.

---

### ADR-044 — Alert state is an auditable lifecycle, not a UI flag
**Audit:** Closed-loop implementation, 2026-09-22 · **Status:** ACCEPTED

Live alerts transition through `OPEN → ACKNOWLEDGED → ACTIONED → VALIDATED → CLOSED`.
Human decisions preserve the recommendation, action, reason, user/role, owner, due
station/time, evidence snapshot, and later inspection outcome. A durable status endpoint
rehydrates acknowledgement/action state after refresh. Closure is rejected until
validation. Run metrics report active/correct/false alerts, alert correctness rate,
response and containment time, decision correctness, unnecessary intervention, and a
clearly sourced synthetic avoided-cost estimate.

### ADR-045 — Incident triage becomes Coordinator → Specialists → Synthesizer
**Audit:** Multi-agent orchestration implementation, 2026-09-22 · **Status:** ACCEPTED

`genai/loop.py`'s single ReAct loop already implements everything the `incident` intent's
own guidance (`genai/intents.py`) describes end to end: rule out infrastructure, establish
what changed, then trace affected units. One model, one broad tool registry, one
transcript. That is a correct answer with poor separation of authority — the agentic
surface area a reviewer can actually see is one phase trail, not three investigators
reasoning in distinct roles, and a mentor review specifically asked for genuine
multi-agent orchestration rather than a single, broadly-scoped agent.

**Chosen:** the `incident` intent now runs three bounded specialist loops (reliability,
production, risk), each a `genai/loop.py::run_loop` call against a disjoint SUBSET of the
18-tool registry with its own short guidance and its own, LOWER iteration/tool-call/
wallclock cap, followed by one synthesis call (also `run_loop`, an EMPTY tool registry)
that reconciles their evidence into the one `Finding` shape `genai/finding.py` and
`genai/verifier.py` already know. No change to `genai/verifier.py`, `genai/finding.py`, or
`genai/prompts.py`'s `SYSTEM` prompt was needed or made: the citation contract is
universal, and the verifier has never had a concept of "which loop produced this call_id"
— it checks a `call_id -> result` mapping, and a MERGED map with globally-unique ids across
four loop invocations satisfies it exactly as one loop's map always did. A dedicated test
(`tests/test_genai_orchestrator.py::test_happy_path_produces_a_globally_sequential_transcript_the_real_verifier_accepts`)
proves this by building three real citations, one per specialist, and running the actual
`genai.verifier.verify()` against the merged transcript — not asserting the claim, testing it.

**The one load-bearing mechanism:** `run_loop`'s call_id numbering (`seq`) was local to
each invocation. Four sequential invocations sharing one transcript would each start at
`call_0001` and collide. `run_loop` now takes `initial_seq` (threaded from each prior
stage's `LoopResult.final_seq`) plus independently-parametrized `max_iterations`,
`max_tool_calls`, and `wallclock_cap_s` — all four default to today's module constants, so
the existing single-agent call site and its whole test suite are byte-identical with no
call-site edits (confirmed: all 14 pre-existing `tests/test_genai_loop.py` cases pass
unmodified). The call-id counter (`LoopResult.final_seq`) and the per-invocation tool-call
budget (`calls_made`, local to each `run_loop` call) are deliberately SEPARATE counters —
coupling them would make a specialist starting at `initial_seq=8` incorrectly
budget-exhaust after `24-8` calls instead of its own intended cap.

**Coordinator is deterministic, not a model call.** `genai/intents.py` already made this
argument for intent selection ("a classifier would be a second, unverifiable model call
and a new failure mode, to save a click on a button already on screen"); the same
reasoning applies here, and every clause of `INTENTS[INCIDENT].guidance` is unconditional,
so there is no real per-question judgment to spend a request on yet.
`orchestrator.select_specialists()` always returns all three for `incident`. An
LLM-coordinator variant (one no-tool call, the deterministic table as its fallback on any
failure) is a named, deferred fast-follow — ADR-040's 50-requests/day `:free`-tier ceiling
makes every guaranteed extra request a real cost, and this decision buys the same
specialist-separation without one.

**Budget, made concrete (ADR-040 context).** Per-specialist caps: reliability
(5 iter / 3 calls / 60s), production (6 / 4 / 90s), risk (6 / 4 / 90s), synthesis
(2 / 0 / 60s) — the four wallclock allotments sum to exactly 300s, the SAME
`RUN_WALLCLOCK_CAP_S` the single-agent path already uses, just partitioned (pinned by
`tests/test_genai_orchestrator.py::test_wallclock_budgets_sum_to_the_single_agent_ceiling`).
Worst-case request count for one orchestrated run: 19. Typical (2-4 iterations per stage,
as `genai/config.py`'s own `MAX_ITERATIONS` comment already measures for the single-agent
path): ~9, comparable to the single-agent run's measured 7-12. On the `:free` tier that is
roughly 2-5 orchestrated incident conversations per day.
`PULSETWIN_GENAI_MAX_SPECIALISTS` truncates the specialist list (reliability first) as an
operator-facing degrade lever on a constrained day; `PULSETWIN_GENAI_DISABLE_ORCHESTRATION`
is a full kill switch back to the single-agent incident path.

**Graceful partial-evidence degradation.** A specialist that exhausts its own
iteration/tool-call/wallclock cap is not a run failure: `run_loop` returns whatever
records it accumulated before the cap, and the orchestrator marks that specialist
`partial` and proceeds to the next stage and eventually the synthesizer, which is told
explicitly which specialists are partial and to report gaps as `unanswered` rather than
fill them in. Only a CREDENTIAL/PERMISSION-shaped failure (401/403/404) aborts the whole
orchestration immediately, matching the single-agent path's existing behaviour for the
same codes — proceeding to a synthesizer that cannot succeed either would just spend a
request to reach the same failure.

**Attribution needed a schema change.** `triage_tool_calls` gained a nullable `agent`
column (`migrations/014_genai_orchestration.sql`, mirrored by hand in `serve/db.py` per
the existing dual-schema convention) and `triage_runs` gained `orchestrated`/
`agents_used`. Without the column, specialist attribution would exist only in the live
heartbeat and be lost the instant `serve/routers/triage.py::get_run` switches from the
heartbeat to the persisted ledger as authoritative — the same flicker-at-the-handover
failure mode its own comments already warn about for the phase trail.

**Offline parity: a documented note, not a restructuring.** `genai/offline_triage.py`'s
`run_incident` already follows the identical METHOD (infrastructure before behaviour,
then affected units) in one fixed deterministic pass, and PARITY IS A HARD REQUIREMENT
means every named action works with no provider — it does not mean the deterministic path
must imitate the agentic path's internal shape. Restructuring it into three fake
"specialist" functions with no real agent behind them would be exactly the pretence
CLAUDE.md §3's "an honest lesser thing, never a pretence" already rules out.

**Consequences:** the incident intent now demonstrates genuine multi-agent orchestration
— three separately-bounded, separately-scoped LLM investigators and a reconciling
synthesis step, all citation-checked by the SAME unmodified verifier — at a request cost
comparable to, not multiplicatively worse than, the single-agent path it replaces for
this one intent. The other four intents (`ask`, `handover`, `shift_intel`, `whatif`) are
unaffected: `use_agent and intent == intents.INCIDENT and config.ORCHESTRATION_ENABLED`
is the only new branch condition in `genai/triage.py::run`, and every other code path
through it is unchanged.

### ADR-046 — Engineering-knowledge retrieval: lexical TF-IDF, a tool not a specialist
**Audit:** RAG implementation, 2026-09-22 · **Status:** ACCEPTED

A competing prompt evaluated for this hackathon asked for a full RAG subsystem — Qdrant,
sentence-transformers, a dedicated Knowledge agent — as part of a ground-up rebuild.
Comparing it against this build surfaced one genuine gap worth keeping: this project had
no engineering-knowledge retrieval at all. Every other item on that prompt's "agentic AI"
checklist was already met or exceeded here, most notably the thing it had no equivalent
of — `genai/verifier.py`'s deterministic re-check of every claim, versus that prompt's
prompt-only trust discipline ("use language such as 'likely contributing factor'"). This
ADR adds the one real gap without importing that prompt's weaker trust model.

**Retrieval: deterministic TF-IDF, not embeddings.** `offline/build_knowledge_index.py`
tokenizes a 10-document, 26-chunk synthetic SOP/fault-code corpus
(`offline/knowledge/corpus.py`), builds a vocabulary, IDF weights and a dense TF-IDF
matrix with pure numpy, and bakes them into `artifacts/knowledge_index.npz` +
`artifacts/knowledge_corpus.json` — the same "precompute heavy, serve thin" split
`serve/scorer.py` already uses for the trained model. `serve/knowledge_index.py` projects
a query into that space and ranks by cosine similarity, all numpy/stdlib, well inside the
import wall. No embedding model, no vector database service, no network call, no new
dependency anywhere, fully reproducible, and it works with zero LLM credential — the
`:free`-tier and no-credential constraints (ADR-040, `genai/offline_triage.py`) that
already govern this tier apply here without needing a second design.

**A real precision bug, caught by running it, not assumed away.** The first version
tokenized only each chunk's literal body text with a plain word-splitting regex. The
query "weld current anomaly" then ranked the WRONG document first: the fault-code entry
titled exactly "FC-301 Weld current anomaly" writes the signal as the single compound
token `weld_current_a`, so a query using the separate English words "weld" and "current"
got zero credit for it, while an unrelated section that happened to write "weld-current"
in prose (splitting on the hyphen) won on a coincidence. Fixed two ways: `tokenize()` now
emits a compound identifier's underscore-split parts alongside the whole token
(`weld_current_a` → also `weld`, `current`), and each chunk's document title and section
heading are indexed alongside its body (title terms like "Calibration" now count for a
section whose body never repeats that exact word). Confirmed by hand against a battery of
representative incident-style queries before writing a single test — every one now
top-ranks the document actually about the query's topic — and pinned in
`tests/test_knowledge_index.py::test_a_fault_code_query_ranks_its_own_named_document_first`.

**Tool, not a 4th specialist.** `search_engineering_knowledge` joins the 18-tool registry
(`genai/tools.py`) and is available to the single-agent path and to all three `incident`
specialists (`genai/orchestrator.py`). Each specialist's `max_tool_calls` is bumped by
exactly 1 over its ADR-045 budget so an optional knowledge lookup never comes at the cost
of the domain evidence a specialist was already sized to gather. No orchestrator
structure change, no new mandatory request per run — the tool is called only if the model
chooses to, so it adds no guaranteed pressure on the `:free`-tier 50/day budget beyond
what the model actually uses. A new `PHASE_ORDER` entry ("Searching engineering
knowledge") is mirrored by hand into `serve/routers/triage.py::PHASE_ORDER`, exactly like
every existing phase, with `tests/test_genai_progress.py`'s existing agreement test
catching drift.

**Verification: a citation check with the same rigor as the other four.** A new
`KnowledgeCitation` (`genai/finding.py`, parallel to `Claim`) requires `quoted_text` plus
a `Citation` into a `search_engineering_knowledge` result. `genai/verifier.py` resolves
the pointer against the RECORDED transcript (never re-calling the endpoint, same
guarantee C1/C2 already rest on) and checks `quoted_text` is a real, whitespace-normalized
substring of the chunk's actual text. Every failure code for this check — unresolvable
call or pointer, a non-text target, a fabricated quote, an empty quote — is FATAL
(`FATAL_CODES`), rejecting the whole run, matching how `UNSOURCED_CURRENCY` and
`OPERATOR_IDENTITY` are already treated: inventing what a maintenance procedure says is a
comparable trust violation to inventing a dollar figure, and there is no meaningful
partial credit for a quote that is 70% real. Knowledge verdicts are tracked in a
SEPARATE list from claim verdicts precisely so a knowledge citation can never inflate
`claims_verified` past `claims_total` and corrupt the C1/C2 graded-redaction ratio — a
failure mode caught and pinned directly
(`test_a_knowledge_citation_never_inflates_claims_verified`). On success, `served_finding`
carries `finding.knowledge_citations` unchanged: reaching the success path already means
every citation passed, so unlike claims there is no partial-redaction case to build.

**Known, stated limitation.** This covers text cited through `knowledge_citations` only.
Free prose in `summary`/`recommendation` is still swept for numbers only (C3's existing
scope), not for uncited SOP paraphrases — a model could still describe a procedure in its
own words in prose without triggering any check. Extending C3 to detect unattributed
paraphrase (not just uncited numbers) is a materially harder, more speculative problem
(near-duplicate text detection, not exact substring matching) and is out of scope here;
the citation contract in `genai/prompts.py`'s SYSTEM prompt instructs the model not to do
this, same as every other prompt-level rule that C3's design already treats as
insufficient on its own for the things this module CAN mechanically check.

**Offline parity: unaffected by design.** `genai/offline_triage.py`'s deterministic plans
call no LLM and produce no `knowledge_citations` — `Finding.knowledge_citations` defaults
to `[]`, so the offline path and every Finding produced before this ADR parse and verify
exactly as before (`test_absent_knowledge_citations_is_unaffected`).

---

### ADR-047 — The frontend moves to BMW's corporate-automotive language, light
**Audit:** frontend design direction, 2026-09-23 · **Status:** ACCEPTED

Two references arrived together and disagreed: a set of dashboard mockups carrying a new
information architecture (a persistent left rail, a global top bar, and an Overview page
that did not exist), and a written BMW corporate design system — light `#ffffff` canvas,
BMW blue `#1c69d4` as the single action colour, 0px corners, no drop shadow on cards, and
a 700/300 weight pair with 500 deliberately absent. The mockups were dark. The system is
light. Taken literally, both cannot hold.

**Decision.** Take the STRUCTURE from the mockups and the LANGUAGE from BMW, and resolve
the palette conflict in BMW's favour. `CLAUDE.md` §7 previously pinned this frontend to a
dark control-room palette; that section is rewritten rather than quietly contradicted, and
this ADR is the record of why. The dark surface is not deleted — it is demoted from "the
theme" to **one band** (`--color-surface-dark`, warm navy `#1a2129`), which is what BMW
actually specifies, and it now carries the sidebar rail, the hero and CTA bands, the
tooltip and the chart tip. BMW's own rule that no two consecutive bands share a surface
mode is what makes a white-on-white dashboard read as a product rather than a document.

**Why the swap was cheap.** `grep` for a hex literal across `app/` and `components/`
returned two matches, both inside comments, and for `bg-black`/`text-white`, zero. Every
colour already resolved through the `@theme` block in `app/globals.css` and its hand-kept
mirror in `lib/theme.ts`. Inverting the palette therefore meant editing two files and
letting ~9,000 lines of view code inherit it. The token NAMES are unchanged, including the
four `--radius-*` steps, which now all resolve to 0 — so no call site had to be touched to
square a corner, and a future change to the radius scale still reaches everything.

The one token pair whose MEANING inverted is `-fg`: on the dark build it was the
*brightened* variant for text on black, and it is now the *darkened* variant for text on
white. `ink-faint` is pinned at `#757575` for the same reason it was pinned before — it
carries every panel title, axis label and table header, and it must clear WCAG AA (4.6:1)
rather than be chosen by eye. A new `-on-dark` set (`--color-ok-on-dark` and four
siblings) exists because the darkened `-fg` values go muddy on navy and the dark band is a
real surface with real signal text on it.

**The three contracts are untouched.** The signal hues keep their contract meanings
exactly (ok = measured, warn = inferred, danger = alert, ai = prediction); only their
values moved. `hatch-inferred` had its alpha raised from 28% to 42% because an amber hatch
tuned against `#111419` nearly vanishes against `#ffffff` — a provenance marker that
cannot be seen is a broken contract, not a styling preference.

**Structure.** `AppShell` was rebuilt as a dark rail plus a light top bar **without
changing its prop contract**, so all four persona pages mount it unedited. The top bar's
two additions are real, not decorative: the notification tray is derived from
`/api/shift/summary` and its badge counts conditions the API actually reported — a bell
that reads "3" when nothing is wrong teaches an operator to ignore it — and the search box
has exactly two destinations that exist (`^U\d{4,8}$` opens that unit on `/supervisor`
via `#unit=`, everything else becomes a DRAFT on `/copilot` via `#q=`, which the composer
seeds but does not send). There is deliberately no station branch: a station has no detail
view to land on, and a search box that silently does nothing is worse than one that hands
the query to something that can answer it.

**`/overview` is not gated by `usePersonaAccess`.** The other four views each pin
themselves to one persona; this is the page you arrive on before choosing. It keeps
whatever role `/login` left in place and degrades panel by panel, naming the role each
blocked panel needs — the same honesty rule `RoleGate` follows, applied to a whole page.
Its station heatmap bands RELATIVELY, by position within the plant's own measured
distribution, not against a fixed uph cut point: the first absolute threshold tried
painted 24 of 30 stations red, and a relative band also survives the `plant_a.yaml` →
`plant_b.yaml` swap §1 requires. Unsensored stations take no band at all and are excluded
from the ranked loss table, because `/api/stations/stats` reports them with no confidence
interval (the contract-#1 violation already noted in CLAUDE.md §8) and ranking an
unbounded estimate against measured rows is the laundering `genai/offline_triage.py`
already refuses to do.

**Deviations from the BMW document, stated.** Panel body padding is 20px, not the
specified 24px: these are dense dashboard panels — BMW's own "configurator inventory"
density tier — so this takes the step below on the same 8px scale rather than inventing a
value off it. Drop shadow survives for elements that genuinely float free of the document
(popover, tooltip, toast, dock), which BMW's card rule does not cover and its own Known
Gaps leaves unextracted. `.text-2xs` and `.text-xs` sit at weight 400 rather than Light
300, which is the same lane BMW gives `caption` and `nav-link`: below ~13px, 300 is hinted
away to almost nothing on a white ground.

**One trap worth the ADR.** Tailwind v4 emits its utilities inside `@layer utilities`, and
an UNLAYERED rule beats any layered one regardless of specificity. The first cut of the
base rules was unlayered, so `h2 { color: var(--color-ink) }` silently overrode
`text-on-dark` and rendered every heading in every dark band invisible, and
`.text-2xs { font-weight: 400 }` overrode `font-bold` on every badge, panel title and
table header in the product. Both shipped for exactly one screenshot. The base block is
now wrapped in `@layer base`, and the computed styles are checked directly in the browser
rather than by grepping the stylesheet — Next's dev server injects CSS through JS, so a
response-body grep measures nothing and cheerfully reports every class as missing.

---

### ADR-048 — Persona views become separations of duty behind a real session

**Status:** accepted, 2026-09-23.

**Context.** `/login` (ADR shipped with commit `847b594`) checked a credential
server-side and then returned a role *string*, which `lib/api.ts` echoed back in
`X-PulseTwin-Role` on every later request. Two things followed, and both were wrong in
the same way — the client was the only party asserting who it was:

1. **Signing in was optional.** `currentRole` defaulted to `"leadership"`, so a visitor
   who never opened `/login` at all reached `/manager`, `/leadership` and the leadership
   panels on `/overview`. The default was chosen when the header was a demo affordance
   and nothing gated on it; once `/login` existed it was a hole.
2. **Entry was a rank comparison.** `usePersonaAccess` granted entry via
   `roleAtLeast(entryRole, minRole)`, mirroring `serve/middleware/auth.py`'s hierarchy.
   A leadership login therefore walked into `/manager`, and `system` walked into all
   four. A hierarchy can express "leadership may call a manager endpoint"; it cannot
   express "the manager's view and leadership's view are different jobs."

**Decision.** Three changes, one per layer.

**A session the server issues.** `serve/session_store.py` holds opaque 256-bit tokens
with an 8-hour TTL; `POST /api/auth/login` mints one, `GET /api/auth/session` validates
it, `POST /api/auth/logout` revokes it. `get_role` resolves `X-PulseTwin-Token` when
present and the session's role then outranks the declared header — a declared role at or
below the session's is honoured (the deliberate privilege drop `/ops`'s "Viewing as"
selector performs), and anything above it is ignored. An unknown or expired token is a
401, never a quiet fall-back to the header, because falling back would make sign-out and
expiry advisory. `get_user` reads the session's user, so a caller holding a token cannot
relabel who acted in the audit trail.

This does **not** close the header seam `docs/SECURITY.md` documents, and is not meant
to: a caller that presents no token and sets `X-PulseTwin-Role` by hand behaves exactly
as before, which is what keeps every existing test and that document's honest description
true. What it closes is the UI's own path — the role on that path is now one the server
issued and can revoke. The store is process-local, which is correct for a single-worker
deployment and is the wrong answer for several; the fix there is the shared store or the
stateless JWT SECURITY.md already prescribes, not a bigger dict.

**An exact-match route policy, not a rank check.** `frontend/lib/routeAccess.ts` is the
one table naming which role may OPEN which view, and both the route guard and the rail's
navigation read it, so a lock icon can never appear on a link that still works.
`/supervisor` and `/overview` are public — the floor view is what the line is for, it is
where an unauthenticated visitor lands, and every endpoint it calls is already at the
API's own default role. `/manager`, `/leadership` and `/ops` each require exactly one
role. `/copilot` takes the three roles whose sessions its endpoints actually accept.
`system` is refused on `/manager` and `/leadership` despite outranking both, because
these are separations of duty and not privilege levels.

Anonymous now resolves to `"supervisor"`, not `"leadership"` — matching
`serve/middleware/auth.py`'s own default, so an unauthenticated visitor gets the least
access this API grants rather than the most. `/overview` therefore degrades for a
signed-out visitor, which is the page working as ADR-047 designed it rather than a
regression. `setRole()` survives as the per-page persona pin, but is now clamped to the
session's role: pinning can no longer be the thing that grants privilege, which is
precisely how an anonymous visitor used to reach leadership data.

**Consequences, including one rule bent on purpose.** The session is persisted in
`sessionStorage`, read and written only in `lib/api.ts`. CLAUDE.md §7 bans browser
storage in React components, and the RBAC role was explicitly named as following that
rule. That rule is about UI state that must be **derived** rather than remembered — the
alert budget and shift counters come off the replay clock — and a sign-in is not derived
state. Without persistence the module variable resets on every reload, and because this
ships as a static export, typing `/manager` into the address bar *is* a reload: every
hard navigation would sign the user out. `sessionStorage` rather than `localStorage` so
the session dies with the tab, and what is stored is a server-issued token the server
re-validates on load (`restoreSession`), not a role string the client can edit into
anything it likes. Every read and write is wrapped — blocked storage degrades to an
in-memory session for that page view rather than throwing.

`AccessRestricted` is now reached only by a signed-in role on the wrong view; signed-out
visitors are redirected to `/login?next=<path>` instead, because "sign in" is a clearer
instruction than "you need a role you have no way to acquire from here". `safeNext()`
rejects anything that is not a same-origin absolute path, which is the one genuinely
exploitable bug a `next` parameter invites.

**Measured, in a browser, against a live backend.** Signed out: `/supervisor` and
`/overview` render, the other four redirect carrying `next`. Signed in as manager from a
`/copilot` bounce: lands on `/copilot`, `/leadership` and `/ops` show the refusal,
`/manager` opens and survives a reload. Sign out returns to `/login` and `/manager`
bounces again. Signed in as leadership: `/manager` is refused. The only 4xx on a
signed-out page is `/api/shift/summary` (manager-gated), already caught and degraded by
`AppShell`. `tests/test_auth_session.py` pins the seam — 17 cases including "a declared
role cannot climb above the session" and "no token behaves exactly as before".

---

### ADR-049 — Authentication becomes a posture, not a comment

**Context.** RBAC was enforced from `X-PulseTwin-Role`, a header the caller writes. That is
the right posture for a judged demo and an unacceptable one for anything reachable by
someone else, and the only thing separating the two was a docstring promising the swap
would be small. `docs/SECURITY.md` described an OIDC upgrade path that no code implemented.
CORS was `allow_origins=["*"]`.

**Decision.** `PULSETWIN_AUTH_MODE` selects the posture explicitly, with exactly two states
and no silent third — any value other than `jwt` is `dev`.

* `dev` (default) — unchanged. The header is honoured. Local development, the docker demo
  and the whole test suite run here, which is what keeps this change from being a
  behavioural break.
* `jwt` — the header alone grants nothing. A caller presents a server-issued session token
  or a signed JWT in `Authorization: Bearer`. A bare role header is 401. A role header
  *below* the verified role is still honoured, because dropping privilege deliberately is
  what `/ops`'s "Viewing as" selector does and a verified caller must be allowed to ask for
  less than it holds — never more.

`serve/middleware/jwt_auth.py` verifies signature, issuer, audience and expiry, supports
HS256 via shared secret and RS256/ES256 via a JWKS endpoint with a 10-minute key cache,
reads a configurable role claim, and maps claim values onto PulseTwin's four roles through
`PULSETWIN_ROLE_MAP`. When several claims map, the **highest** role wins — providers do not
guarantee claim ordering, so first-match would be a coin flip. An unmapped group is ignored
rather than guessed, and a token mapping to nothing is 403, not a default role.

**Fail closed at boot.** `validate_config()` runs in `serve/main.py`'s lifespan and *raises*
if `jwt` mode cannot verify a signature, or if `python-jose` is absent, or if
`PULSETWIN_CORS_ORIGINS` is unset or wildcard. The process does not start. An auth system
that degrades to "allow" under misconfiguration is worse than none, because it is trusted.

**Why no vendor SDK.** The import wall (CLAUDE.md §2) permits `jose`, and ADR-033 already
allowlisted it for exactly this. Everything a provider needs is configuration: an issuer, an
audience, a JWKS URL, a claim name, a mapping table. Entra, Okta, Auth0, Keycloak and
Cognito all fit that shape. `tests/test_jwt_auth.py::test_no_vendor_is_hardcoded` parses the
module's AST, strips every docstring, and asserts no provider name survives into executable
code — providers are named in prose, deliberately, and nowhere else.

**`python-jose` is now a real runtime dependency**, added to `serve/requirements.txt` and to
`pyproject.toml`'s runtime group. The import is lazy so dev mode needs nothing, but the
image ships it: a production deployment must not be one `pip install` away from failing
closed at boot. `[cryptography]` is required because real providers sign with RS256.

**Consequences.** 30 tests in `tests/test_jwt_auth.py`, including the one that matters —
`test_production_mode_refuses_a_bare_role_header`. The demo is unaffected. `docs/SECURITY.md`'s
upgrade path stops being aspirational.

---

### ADR-050 — Bottleneck ground truth moves off the detector's own statistic

**Context.** `offline/ml/bottleneck.py` scored its active-share detector against a reference
built by smoothing *the same active-share matrix* over eight buckets. The module's docstring
is candid about why (an argmax scored against itself returns 100%, which invites doubt about
everything else) and the reasoning is right — but both quantities remain functions of one
statistic. If active share is a biased estimate of the constraint, detector and reference are
wrong together, and their agreement is high *because* they share the bias. 85.9% / 90.2% is
self-consistency, not accuracy.

**Decision.** Ground truth becomes **throughput sensitivity**, which owes nothing to any
detector: slow station *i*'s cycle time by 10%, rerun the line, and measure the UPH lost.
The constraint is the station that costs the most. Repeat under Common Random Numbers across
30+ seeds; bootstrap a 95% CI over the paired differences.

CRN matters because a 5-day run's throughput varies by ~1 UPH across seeds and the effect
being measured is of the same order — differencing independently seeded runs would measure
mostly noise. It is also *approximate* in a DES, because perturbing a cycle time changes
event ordering and the streams decouple over the horizon; that is the accepted limitation,
it is documented, and it is why the method still averages over many seeds.

**No simulator was duplicated.** `LineSimulator.run()` was split into
`start()` / `step()` / `finish()`, with `run()` now composed of the three. A fingerprint over
`state_seconds`, `visits` and `completed_units` across three plant/seed combinations is
byte-identical before and after. `offline/benchmarks/simrun.py` is a thin CRN wrapper; the
perturbation scales `nominal_cycle_s` on a config deep copy so variant multipliers, tool
wear and cycle CV all still apply and a perturbed config remains schema-valid.

Two of the seven benchmarked detectors needed measurements the simulator did not take, so
`LineSimulator` gained an **opt-in** `instrument=True` recording buffer occupancy and
active-period spans. Off by default; proven inert by test.

**Result.** The shipped detector scores **top-1 0.93** against the independent reference on
plant A's baseline scenario — a better-founded claim than the 85.9% it replaces, even though
the number is higher. Plain utilisation ties it exactly, which is reported rather than
buried. The 85.9% figure stays in README, **relabelled**: deleting a published number is
worse than explaining it.

**Naming discipline.** Three detectors are faithful implementations and are named for their
criterion; two are operationalised adaptations and carry `_adapted` in the key, with a test
asserting the key and the provenance string agree. We do not claim to reproduce a published
algorithm we have adapted.

### ADR-051 — Agents propose, a person decides, the twin applies

**Context.** A suggestion arrived to rebuild PulseTwin as a greenfield "agentic digital twin":
an `AutomotiveDigitalTwin` class, four rule-based agents calling mock actuator APIs to reroute
parts, shift weld load and change HVAC setpoints autonomously, and a simulation loop. It was
assessed and **not adopted as written**. It duplicated the twin and agent tier this repo
already has with a weaker version of both; its "agents" were threshold rules — automation,
not agency; its mock APIs broke the no-placeholder rule (CLAUDE.md §1); autonomous control
of plant equipment contradicts the read-only-by-construction agent (`docs/SECURITY.md`) and
the one-way OT→IT flow (`docs/PRODUCTION_ARCHITECTURE.md`); and one of its four use cases —
stamping-die damage — has no data at all, because neither plant has a press shop.

Two of its ideas were real gaps. Agents only ran when a person pressed a button, so nothing
*noticed* a risk; and a finding could recommend in prose but never *propose* an action a
person could act on in one step.

**Decision.** A closed loop, with a person at the one point that matters:

    watch fires -> agent investigates -> agent PROPOSES -> person approves
                -> twin applies -> watch confirms recovery (EFFECTIVE)

* **The watch** (`serve/watch.py`, stdlib only) runs on every live-simulation event. Three
  detectors, thresholds in `configs/plant_*.yaml` under `watch:` and baked to
  `artifacts/watch_rules_<plant>.json` by `offline/build_watch_rules.py` (serve/ cannot parse
  YAML): **starvation** (a station down/blocked with cycle time above takt), **paint
  environment** (zone humidity in *excess of ambient*, above the Mode A threshold), **tool
  degradation** (torque / weld current off its per-variant baseline). A firing is latched per
  episode, cooled down in simulated seconds, deduplicated per open proposal, and capped per
  run so a free-tier provider quota survives a demo.
* **Three agents** (`starvation`, `paint_env`, `tool_wear` in `genai/intents.py`) start
  through the same `start_triage_run` as the existing auto-investigation. Each has a
  deterministic plan (`genai/offline_triage.py`), so the loop works with no provider key.
  One new tool, `get_watch_firings`, is GET-only: the registry stays read-only.
* **Proposed actions** (`genai/finding.py::ACTION_TYPES`, a closed catalogue of six) must
  cite the claims that justify them. `genai/verifier.py` serves an action only if its type
  is catalogued, its params are present and **every cited claim verified** — an action
  resting on a redacted number is removed with it. Dropping an action is never fatal.
* **A person decides** through `POST /api/proposals/{id}/approve|reject` (manager and above,
  audited, 409 on a second decision). These endpoints are **not tools**: an agent can
  propose, only a person can approve.
* **The twin applies** an approved action by removing part of the injected scenario's
  effect (`serve/live_simulation.py::MITIGATIONS`). It never writes toward MQTT or any OT
  interface. The magnitudes are modelled assumptions, recorded in `docs/ASSUMPTIONS.md`.

**Measured, not assumed — the thresholds.** Every detector was profiled on the replay
streams before it was trusted:

* Zone humidity tracks ambient with sd 1.1 points (rolling-8 max excess 1.43), while the
  mean zone humidity (64.6%) already sits *above* plant A's 55% threshold. An absolute
  threshold would fire on every humid day, so paint keys on the excess over ambient.
* The replay stream carries only `idle`/`starved` states, so the starvation detector is
  silent on a normal run by construction.
* A single run baseline read the variant mix as 24% torque "drift"; per-variant baselines
  baked from the stream fixed it. Vibration was excluded (rolling-8 deviation 60–120% on a
  normal run). What still fires on a normal run is exactly the **Mode C** stations — S3 on
  plant A, S4/S11 on plant B — which carry the simulator's *designed* torque deficits. Those
  are true positives, and a test pins the firings to the configured Mode C stations.
* Plant B's Mode C deficit (7%) also re-fired at S25; its watch threshold is 10%.

**Measured against the live provider (OpenRouter, 2026-09-24).** The deterministic path
closed all three loops end to end (fire → propose → approve → EFFECTIVE). The model path
was run twice. The first run exposed two prompt gaps, both caught by the gate rather than
served: actions anchored on a *string* claim (`stalled_state`) and a *list*, which the
verifier dropped; and a torque claim in `Nm`, which is not a claim unit and made a finding
unparseable. `intents.ACTION_GUIDANCE` now says both in the words the model reads. On the
second run the tool-wear agent produced a verified maintenance ticket (its load-shift
proposal was dropped for resting on an unverified claim), and the starvation agent was
**rejected** for citing an inferred station row as measured — contract #1 enforced on a
model error, which is the gate doing its job.

**Consequences.** `docs/SECURITY.md` §"The agent's authority" is amended: the agent still
cannot *do* anything, and the one new write path is a human-called approval that changes
only the simulation. Approval is exact-role gated in the UI (`ProposalInbox`, on `/manager`
and `/ops`) and hierarchy-gated in the API, like every other mutation.

### ADR-052 — Demo runtime retired; production requires Postgres (PULSETWIN_REQUIRE_POSTGRES); SQLite is build/test-only

**Context.** The deployed site's health box read `database: sqlite`. No deployment set
`DATABASE_URL`, so `serve/db.py::open_database` fell back — silently — to the read-only
SQLite build artifact. Both deploy paths were SQLite by construction: `docker-compose.yml`
(the "local demo") and `render.yaml` + `serve/Dockerfile.render`. Feedback lived in a
tempdir SQLite file even on Postgres, so it reset on every restart.

**Decision.** No deployment can reach SQLite; the code path stays.

* `serve/db.py::_require_postgres_url` — with `PULSETWIN_REQUIRE_POSTGRES=1` and no
  `DATABASE_URL`, `open_database` and `open_ops_only` raise `RuntimeError` at startup.
  Every deployment sets the flag (`docker-compose.prod.yml`, `render.yaml`).
* SQLite is kept deliberately: `artifacts/pulsetwin.db` is the build output
  `offline/load_to_postgres.py` loads into Postgres, and the test suite runs on it.
* `docker-compose.yml` and `serve/Dockerfile.render` are deleted. The Makefile's `demo`,
  `smoke-demo*` and `demo-prod*` targets become `up`, `up-mqtt`, `down` over the one
  remaining stack. The image no longer carries `pulsetwin.db` (39 MB), `mlops/`, `offline/`
  or the retraining libraries; `requirements-jobs.txt` is `openai` alone.
* Feedback moves into Postgres: `migrations/016_feedback.sql`, and on Postgres
  `STATE["feedback"]` is the ops handle.
* Free-tier cloud: Neon (Postgres) + Render (one web service, `render.yaml`) + Vercel (the
  static export). Neon is reached on its DIRECT endpoint; the pool also sets
  `prepare_threshold=None` (PgBouncer transaction mode breaks psycopg's auto-prepare),
  reads its size from `PULSETWIN_PG_POOL_MAX` (Render: 5), and checks each connection on
  checkout so one killed by Neon's idle suspend is replaced rather than raised.
* Redis, MQTT and ingestion stay on the local stack (`serve/` never imports redis; the free
  tier has no workers; ingestion has no MQTT TLS/auth). `/api/health` reports each as
  `not_configured` rather than omitting it.

**Consequence.** The first end-to-end run of `docker-compose.prod.yml` (2026-09-25):
migrations 014-016 applied, the loader wrote 325,793 rows, `/api/health` reported
`postgresql`, feedback survived a backend restart, `/supervisor` and `/ops` loaded with no
page errors, and the backend sat at ~82 MB RSS — inside Render's 512 MB free tier.

### ADR-053 — Retraining removed: static synthetic data makes it a no-op

**Context.** The champion-challenger pipeline (`mlops/retrainer.py`, `mlops/evaluation.py`,
the `/api/admin/{trigger-retrain,retrain-status,promote-model,rollback-model}` endpoints,
`RetrainingConsole.tsx`) retrained on the same simulated data the deployed model was
trained on. That data is static, and the pipeline is deterministic — seed 42 and
`OMP_NUM_THREADS=1` (ADR-016) — so a retrain reproduces the deployed model. It could add
nothing, while costing a subprocess launch path, a hot-swap seam (ADR-041), a pruning
policy, pandas/scipy/scikit-learn/pyarrow in the runtime image, and the only endpoints
that needed the `system` role.

**Decision.** Remove retraining; keep training and monitoring.

* Kept: `offline/ml/train.py` (unchanged), the numpy scorer, `mlops/drift_detector.py` and
  `mlops/fallback.py` (run offline), and `/api/model/health` + `/api/model/registry` as
  READ-ONLY monitoring behind `ModelHealthPanel` and `DriftMonitor`.
* Removed: the retrainer, the comparison module, `mlops/model_registry.py` (nothing
  surviving imported it — `serve/` records the champion itself), `serve/routers/admin.py`,
  the console, the `retrain` target, and the tests that covered only those. Applied
  migrations are untouched; the unused columns are harmless.
* `serve/main.py` loads the committed `model_<plant>.npz` unconditionally. A registry row
  can describe the served model but can no longer select another artifact — a test pins
  that a stale "champion" row cannot change what is served.
* A drift verdict is now information, not a trigger. `/api/model/health` no longer
  recommends a retrain; it points at the inputs.
* `system` still tops the role hierarchy but gates no endpoint of its own; its gate is
  tested on the dependency directly.

**If the data ever becomes live** (a real MQTT feed writing `station_readings`), retraining
is meaningful again — as a new decision with a new ADR, not a revert of this one.

### ADR-054 — Non-production code removed: the external benchmark and the never-applied K8s/Terraform

**Context.** Moving to a production-only repository (ADR-052), two trees shipped nothing
and ran nowhere. `benchmark/` was the external public-dataset harness (AI4I 2020, Bosch,
an RCA evaluator): it validated the training pipeline on data PulseTwin did not generate,
needed its own dependency group, and ran only in a nightly job. `infrastructure/kubernetes/`
and `infrastructure/terraform/` described a scaled deployment that was never applied — the
real deploys are `docker-compose.prod.yml` and `render.yaml` + Neon + Vercel.

**Decision.** Delete both.

* `benchmark/` and its three tests (`test_benchmark_adapters`, `_masking`, `_rca_eval`), the
  `benchmark-external` dependency group (`uv.lock` refreshed: `requests` and its deps drop
  out), the nightly `external-benchmark` job, and the `external_benchmark` section of
  `tools/metrics_summary.py` and the committed `artifacts/metrics_summary.json`. The AI4I
  figure stays in `docs/MODEL_CARD.md`, marked historical and no longer reproducible here.
* NOT removed: `offline/benchmarks/` — the bottleneck benchmark behind ADR-050's measured
  ground truth, which produces the committed `artifacts/benchmarks/` results.
* `infrastructure/kubernetes/` and `infrastructure/terraform/`. `infrastructure/monitoring/`
  and `mosquitto.conf` stay (the local stack mounts the latter).

**The honest cost, stated.** Several documents cited those manifests as the *enforcement*
of a control — the network-level OT/IT segmentation, encryption at rest, Secret objects.
They were never applied, so they enforced nothing; the documents now say which control is
real and where. What remains enforced in code: `serve/` has no MQTT client and cannot
import one (the import wall). Encryption at rest and in transit come from Neon
(`sslmode=require`) and from Render/Vercel's HTTPS. Network segmentation on a real plant
network is a deploy-time requirement this repository does not implement, and
`docs/SECURITY.md` now says exactly that rather than pointing at a file.

Also in this change: the backend loads `.env.local` automatically (compose `env_file`,
optional; `make dev-backend` sources it) and logs `genai: agent mode` or `genai:
DETERMINISTIC mode` at startup, so a missing key is visible instead of a silent downgrade.

### ADR-055 — Every proposal is simulated before a human sees it: a paired Monte Carlo fork of the physical twin

**Context.** Until now an agent's proposed action reached the Proposals inbox with its
verified evidence but no forecast. `/api/whatif` interpolates a precomputed grid (ADR-007)
over buffer and cycle changes at a few stations; it cannot answer "what does *this* action
do to *this* condition at *this* station", and it has no do-nothing line to compare against.
An external design review named that the core gap in an agentic recommender:
nothing should become a recommendation without a simulated impact and a counterfactual.

**Decision.** Every watch firing spawns `python -m offline.counterfactual` beside the agent
(`serve/routers/proposals.py::start_counterfactual`, the ADR-036 detached-job pattern:
status file before the spawn, stdio to a per-job log, stale after `WORKER_STALE_S`).

* **The fork.** `offline.live_twin.LiveTwinEngine` is warmed once, the detected condition is
  injected, and after an onset the whole engine is `copy.deepcopy`'d (0.1 s) with every RNG
  reseeded *in place* (the simulator holds references to them). Arms: do nothing, plus every
  `MITIGATIONS` action whose scenarios include the condition. Default 8 replicas × 2 h.
* **Paired.** Replica *k* uses the same seed in every arm (common random numbers), so the
  action-minus-nothing interval measures the action, not two draws of line noise.
* **The hypothesis comes from the evidence, never from ground truth.** The detector and its
  measured evidence pick the scenario and a severity by a stated linear map
  (`offline/counterfactual.py::hypothesis`, recorded in each result's `basis`).
* **Defects are expected defects**: the sum of each inspected unit's propensity rather than
  its Bernoulli draw. Same expectation, far less variance, which matters at well under one
  true defect per hour.
* **The gate.** `/api/proposals` reports `SIMULATING` while the job runs, and approval needs
  `PROPOSED`. A job that fails or goes stale releases the gate and shows its error. A dead
  job must not hold a human decision hostage, and a decision made without a forecast says so.
* Every figure is `provenance: "inferred"` with `ci_low`/`ci_high` (contract #1). No dollar
  figure is produced (contract #2).

**The import wall is unchanged.** `serve/` spawns the job and reads JSON; it never imports
`offline/`. The *jobs image* widens instead: `serve/requirements-jobs.txt` gains pandas, scipy
and PyYAML (about 150 MB), and the Dockerfile copies `offline/` and `feature_contract.py`. This
amends "heavy libraries never ship in the runtime image" for the job subprocesses only.
The alternative, a precomputed grid over hypothesis × station × severity band, was rejected:
about 1.5 h of compute per plant, and it answers a banded severity rather than the measured one.

**Measured on plant_a (seed 42, 8 replicas, 2026-09-25). These are the first numbers, reported as they came out:**

| Firing | Hypothesis | Action | Δ throughput vs nothing (UPH, 95% CI) | Δ expected defects |
|---|---|---|---|---|
| starvation S12, DOWN | station_breakdown 0.60 | `increase_feed_buffer` | **+0.46 [0.13, 0.83]**, distinguishable | +0.015 |
| | | `maintenance_ticket` | 0.00, not distinguishable | 0.000 |
| tool_wear S3, −15% | torque_degradation 0.68 | `maintenance_ticket` | 0.00 | −0.004 [−0.011, 0.000] |
| paint_env, +14 pts | high_humidity 0.64 | `hvac_setpoint` | 0.00 | 0.000 |

Two results that look like bugs and are not:
1. **A worn tool at S3 does not move defects in this twin.** Mode C is torque deficit *at S3*
   × vibration *at S8*, with `interaction_required: true`. A full-severity incident drops S3
   torque 22% (43.3 → 34.4 Nm), but S8 vibration stays below the 92nd-percentile gate
   (p50 83rd, max 89th). So Mode C never fires, and the propensity sum over 6 h is identical
   to no incident (1.59). Humidity alone is inert for the same reason: Mode A also needs
   paint-buffer dwell over 480 s. The counterfactual says "not distinguishable" because that
   is what the twin's physics says.
2. **`maintenance_ticket` on a breakdown shows 0.00** because the station is already down and
   its repair was scheduled when it failed. Ending the incident prevents a recurrence but
   cannot shorten a repair in progress.

**A bug the first run caught.** `_mitigate` originally called `engine._apply_physical_state()`,
which also re-forces a breakdown while a breakdown incident is active. An action applied
after the station had been repaired knocked it down again: −11.7 UPH, blamed on
`increase_feed_buffer`. The mitigation now refreshes capacity and checklist state directly,
and `tests/test_counterfactual.py` pins this. Also noted: `IncidentState.breakdown_repair_scale`
is defined but nothing calls it, so a breakdown incident's severity has no physical effect.

**Found by looking at the rendered inbox, not by the tests.** The first version added slots
to the buffer FEEDING the firing station, while the agent proposed slots ahead of the
DOWNSTREAM station: the twin was forecasting a different buffer from the one a manager
would approve. Both now mean the firing station's output buffer, and every arm carries
`simulated_as`, which the UI shows beside the agent's params. The table's `+0.46` row was
measured with that original UPSTREAM placement (+2 slots). Re-run live on the aligned
action (seed 7, breakdown at S3), +4 slots on S3's output buffer gave **exactly 0.00 UPH
in all 8 paired replicas**. A down station's output buffer only drains, so extra capacity
there never binds, whereas upstream slots let S1–S2 keep working. **The twin
contradicts the agent's buffer proposal**, and the inbox shows it as "no effect". That
is the gate doing its job, not a defect to tune away.

**What this is not.** It is not a fork of the live stream-replay run (`serve/live_simulation.py`
replays the committed stream; it is not a physical model). It is the detected condition,
reproduced on the physical twin. The UI must say "simulated", not "predicted for this run".
