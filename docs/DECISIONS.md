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
