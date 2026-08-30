# PulseTwin — Spec Audit

**Subject:** `pulsetwin_final_prompt.md` (1,479 lines)
**Date:** 2026-08-31
**Findings:** 31 — 6 blockers, 6 high, 9 medium, 10 low/process

The spec is genuinely good in its bones: the Tuesday Problem is a well-constructed causal scenario, the precompute-heavy/serve-thin split is the right architecture, and the "honest difficulty" instinct (cap PR-AUC, admit negative ablations) is exactly what wins a credibility-driven rubric.

What follows are the places where it contradicts itself, asserts things that are physically impossible, or specifies an approach that cannot execute in the stated budget. Several would not surface until Phase 6+, when there is no time to recover.

Every fix listed here is applied in `docs/SPEC.md` and justified in `docs/DECISIONS.md`.

---

## BLOCKERS — cannot be built as written

### B1. Label noise is ~41%, not the claimed 3%

The spec sets `inspection_detection_rate: 0.92`, `inspection_false_reject_rate: 0.02`, `base_defect_rate: 0.03`, and separately claims **"Label noise: ~3%"**.

Work the arithmetic on 10,000 units:

| | count |
|---|---|
| True defects | 300 |
| ...detected at 92% | **276** |
| ...escaped | 24 |
| Good units | 9,700 |
| ...falsely rejected at 2% | **194** |
| **Total flagged (the positive label)** | **470** |
| **Of which are actually good** | **194 → 41.3%** |

**41% of positive labels are wrong.** The stated "3% label noise" is off by more than an order of magnitude. This is not a rounding disagreement — it changes what is learnable. Against 41% positive-label noise, the stated target ceiling of PR-AUC 0.75–0.88 is unreachable; a correct model would score around 0.45–0.60 and the build would read as broken when it is in fact behaving properly.

The trap is that the spec then instructs: *"If lower, check for bugs."* A team following that instruction would hunt a bug that does not exist, and could "fix" it by weakening the very realism that makes the project credible.

**Fix:** `inspection_false_reject_rate: 0.002`. Still realistic for automotive final inspection — a 2% false-reject rate would have a real plant scrapping 194 good bodies per 10,000, and nobody runs that line. Positive-label precision becomes ~93%, and the independent 3% label-flip stays as the stated noise source. The 0.75–0.88 ceiling then becomes achievable and honest.

---

### B2. 55 UPH is unachievable with 45–90 s station cycles

`units_per_hour: 55` implies a takt of 3600/55 = **65.5 s**. The spec then instructs station cycle times of **45–90 s**.

A serial line's throughput is capped by its slowest station. One 90 s station caps the line at 3600/90 = **40 UPH** — 27% below the stated figure. And every economic number in the project derives from 55 UPH, including `downtime_cost_per_hour: 5000`, which the spec itself derives as "55 units/hr × ~$90 marginal contribution."

So the headline ROI is computed from a throughput the simulated line cannot reach. The simulator would faithfully report ~40 UPH while `economics.yaml` bills at 55.

**Fix:** nominal cycle times **45–62 s**. Set S19 base around 58 s so the EV 1.4× multiplier lands it at 81 s — which makes the Tuesday bottleneck **emerge from the physics** rather than being asserted by the config. That is a materially better demo: the constraint shift becomes a consequence, not a hardcode. All economics recomputed from *achieved* simulated throughput, not the nominal target.

---

### B3. The seeded demo unit is physically impossible

§5 (Demo Data Seeding) specifies the hero unit:

> Unit U04412 must be a SEDAN that passes **S3** with torque ~8% below target, then hits **S8** with vibration in the 95th+ percentile... Its risk should cross the cost-optimal threshold **between S8 and S12**.

But §8's AlertCard mockup for that same moment lists its root causes as:

```
● Paint buffer dwell: 195s (▲ 62%)      ← S14 → S16
● S15 prep time: 22s (▼ 51%)            ← S15
● S8 vibration: 96th pctl (▲)           ← S8
```

**A unit between S8 and S12 has not entered the paint zone.** Stations 14, 15 and 16 are in its future. Two of the three root causes on the hero alert card cannot exist at the moment the alert fires.

This is the centrepiece of the 5-minute demo (minutes 1:15–1:45 of `DEMO_SCRIPT.md`). As specified it cannot be produced, and the failure would only be discovered while seeding demo data in Phase 6 — very late.

There is a second-order problem: the unit is a **SEDAN**, but the Tuesday cascade is driven by EV units bottlenecking at S19–S24. A sedan is affected only indirectly, through the shared paint buffer. Defensible, but it means the hero unit actually demonstrates Mode C (structural) while the narration describes Modes A and B (paint and human).

**Fix:** move the hero alert to **immediately after S18** (end of paint, before final assembly). At that point Mode A features (paint buffer dwell), Mode B features (S15 checklist time) and the upstream Mode C features (S3 torque, S8 vibration) **all exist**, so the alert card as drawn becomes truthful. Lead time from S18 to S30 across 12 stations at ~58 s is about 12 minutes of pure processing plus buffer dwell — comfortably the ~22 min the demo script quotes. **The demo script's numbers survive unchanged.**

---

### B4. The cost-optimal threshold and the alarm budget are ~100× apart

The spec presents these as complementary. They are in direct conflict.

Cost-optimal threshold, using the spec's own formula and `economics.yaml` values:

```
C_miss  ≈ scrap 4,800 + rework 1,200 + warranty 8,500 × escape_rate  ≈ $7,000
C_alarm  = supervisor_response_cost_per_alarm                        =    $45

p* = C_alarm / (C_miss + C_alarm) = 45 / 7,045 = 0.0064
```

**The cost-optimal threshold is 0.64%.** A miss costs roughly 155× what a false alarm costs, so the economics say: *alarm on almost everything.* At a 3% base rate that flags a very large share of units.

Meanwhile `alarm_budget.max_alerts_per_shift: 6`. A shift is 8 h × 55 UPH = 440 units. 6/440 = **1.4% of units**, which requires a threshold up around 0.6 — roughly **100× higher** than the cost optimum.

The spec asks for both, computed from the same data, presented as one operating point. They cannot be.

**Fix — and this is a *better* answer than the original intended:** compute and display **both**, and be explicit that they disagree.

> "Pure cost optimisation says alarm at p > 0.006 — that would fire on ~200 units per shift. Human factors research caps a supervisor at 6–8 actionable alerts per shift (Wickens, 2008). We operate at p > 0.62 to respect that ceiling, and we quantify the expected value we knowingly leave on the table: $X per year. **The binding constraint on this system is human attention, not model quality.**"

That directly answers rubric bullet 7 (false alarms erode trust) with far more sophistication than a single reconciled number, and it pre-empts the obvious judge question. It requires splitting the schema: `above_cost_threshold` (economics) versus `alert_fired` (budget-gated, top-N by risk per shift).

---

### B5. The 1-second tick loop cannot meet the "< 5 minutes" acceptance check

The spec is explicit: *"Each tick = 1 second of simulated time"* and *"Record per-station-per-second state."*

```
14 days × 86,400 s/day × 30 stations = 36,288,000 station-tick updates
```

In pure Python (the spec forbids SimPy), a loop body doing buffer checks, state transitions and signal generation runs at best ~1–3 M simple iterations/sec — and this body is not simple. Realistic estimate: **several minutes to tens of minutes per plant**, against a stated budget of under 5 minutes for both.

Worse is the storage claim. 36 M per-second state records is a multi-GB parquet file per plant, for data whose information content is a few hundred thousand state *changes*.

**Fix:** event-driven next-event stepping — advance the clock to the next station completion, breakdown, or repair, not to the next second. Persist state as `(station, state, start_ts, end_ts)` **intervals** rather than per-second rows; per-second state is trivially reconstructed by interval lookup when a chart needs it.

Same physics, same fidelity, roughly three orders of magnitude fewer operations. This also makes the ~90 what-if replays in H8 affordable.

---

### B6. One `model.npz` cannot score two plants

The feature namespace is positional: `s3_torque_nm`, `s8_vibration_mm_s`, `s15_checklist_time_s`. Plant B is specified as **42 stations**, zones split differently (body S1–S16, paint S17–S24, final S25–S42), and a **TRUCK variant instead of EV**.

Plant B therefore has a different number of features, different feature names, and a different categorical vocabulary. A tree ensemble trained on plant A's feature matrix **cannot be evaluated on plant B's** — the column space does not exist.

But the spec ships exactly one `artifacts/model.npz`, and `/api/predict/defect` takes a `plant_id` as though one scorer serves both. And "swap the config, zero code changes" is billed in the compliance table as the answer to rubric bullet 6 (multi-site scaling) — it is a *graded* claim, and the one demo moment (4:15) most likely to be attempted live by a judge.

**Fix:** per-plant model artifacts — `model_plant_a.npz`, `model_plant_b.npz` — with the scorer selecting on `plant_id` at request time. Feature *construction* stays entirely config-driven, which is the claim that actually matters and is still fully demonstrable: the same code, given a different YAML, produces a different line, a different feature space, and a different trained model, with zero code edits.

Say precisely that, rather than implying one universal model — which would be a stronger claim than any real multi-plant system makes anyway, since plants genuinely do need per-site models.

---

## HIGH SEVERITY

### H7. `station_readings` blows the 50 MB database cap

Acceptance requires `artifacts/pulsetwin.db` < 50 MB. The `station_readings` schema is one row per `(unit_id, station_id, signal)`:

```
2 plants × ~20,000 units × 30 stations × ~3 signals ≈ 3,600,000 rows
```

At ~60 bytes/row plus the mandated indexes, that is **220 MB+ from one table**, before `genealogy` (1.2 M rows) or `checklists`.

**Fix:** persist `station_readings` only for the **48-hour replay window** (~2,600 units, ~230 k rows, a few MB). Full-history analysis is served from the hourly `station_stats` rollups, which is all the Manager and Leadership views ever query anyway.

### H8. The what-if grid is 3,510 simulation runs per plant

`whatif_grid` is keyed by `(station_id, buffer_delta, cycle_delta_pct)` with buffer_delta −4…+8 and cycle_delta −20%…+20%.

```
30 stations × 13 buffer values × 9 cycle values = 3,510 runs/plant = 7,020 total
```

Each is a full 14-day simulation. Even at an optimistic 20 s/run that is **39 hours**. The spec allocates no budget and never mentions the cost.

**Fix:** 6 candidate stations (the bottleneck and its neighbours — the only ones a manager would realistically tune) × 5 buffer deltas × 3 cycle deltas = **90 runs/plant**, each on a 4-hour window rather than 14 days. Tractable in minutes.

Separately: **`new_bottleneck_station` must not be linearly interpolated.** It is a categorical station ID; the mean of S12 and S19 is not S15.5, it is meaningless. Use nearest-neighbour lookup and label the response honestly.

### H9. Blocking `time.sleep()` in the SSE handler freezes the whole server

§7 specifies the stream endpoint yields lines *"with `time.sleep(interval / speed)`"*, and §9 runs uvicorn with `--workers 1`.

`time.sleep()` inside an async request handler blocks the **event loop**, not just that request. With one worker, a single connected SSE client makes the entire API unresponsive — `/api/health`, `/api/metrics`, every other endpoint, and every other browser tab. Opening the dashboard in two tabs, or a judge loading `/manager` while `/supervisor` streams, kills the demo.

**Fix:** `await asyncio.sleep()` inside an async generator.

Three further stream-hardening items the spec omits, all of which cause "works locally, dies on stage":

- `X-Accel-Buffering: no` and `Cache-Control: no-cache` headers — **Hugging Face Spaces sits behind a proxy that buffers streamed responses**, so SSE silently delivers nothing until the connection closes.
- Periodic keepalive comment lines (`: ping`) to survive idle-connection timeouts.
- The prebuilt byte-offset index the endpoint needs in order to seek to `from_ts` **is not in the schema** — see L26.

### H10. `lot_trailing_defect_rate` is silent temporal leakage

The spec permits this feature and explicitly defends it:

> rolling defect rate of the last 100 units from the same lot, computed using ONLY past inspection results. This is legitimate because it's computable at inference time.

The reasoning is right; the trap is that "past" is ambiguous. Prediction happens at **S20**. Inspection happens at **S30**, which the spec itself says is **25–40 minutes later**. So at prediction time, the most recent ~30–60 units of that lot have entered the line but have **not yet been inspected** — their labels do not exist yet.

If `featurize.py` selects "the last 100 units of this lot" by **unit index** (the natural implementation), it silently ingests labels from the future. The leak is small per unit, completely invisible in the output, and it inflates PR-AUC.

This is almost certainly what would trigger the spec's own "if the model exceeds 0.92, the simulator is too clean" tripwire — sending a team to make the *simulator* harder when the actual bug is in the *features*.

And the specified guard cannot catch it. `tests/test_no_leakage.py` is described as scanning *column names* for station IDs > 20. A name-based scan has no visibility into which timestamps a value was computed from.

**Fix:** select the trailing window by `inspection_ts <= prediction_ts`, never by index. Replace the name scan with a real assertion: for every feature of every unit, the maximum source-row timestamp must be ≤ that unit's S20 exit timestamp. Keep the name scan as a cheap additional guard, not the primary one.

### H11. `POST /api/feedback` writes into the read-only committed artifact

The feedback endpoint "stores in a local SQLite table," and the only SQLite database in the runtime image is `artifacts/pulsetwin.db` — which is committed, shipped inside the Docker image, and the subject of the "reproduces byte-for-byte" guarantee.

Writing to it (a) breaks that determinism guarantee the moment anyone clicks 👍, (b) dirties the container layer, and (c) is silently lost on every Hugging Face Space restart, because that filesystem is ephemeral.

**Fix:** open the artifact DB read-only (`file:...?mode=ro` URI) so an accidental write raises loudly instead of corrupting the demo. Put feedback in a **separate** writable database under `/tmp`, created at startup. Document the production path (a real event sink) in `ARCHITECTURE.md` — the spec already gestures at this and it is a good answer.

Also unmentioned: SQLite connections shared across async handlers need `check_same_thread=False` plus a lock.

### H12. The alert budget counter cannot persist

The Supervisor view's `AlertBudgetCounter` shows *"Alerts: 4 / 6 this shift"* with a meter that turns amber at 5 and red at 6. It is one of the strongest trust signals in the whole UI and directly serves rubric bullet 7.

But the anti-patterns list forbids `localStorage` and `sessionStorage` in React components. If the count lives in React state, **it resets to 0 on every page refresh** — including the refresh a judge triggers by navigating between the three persona views and back.

**Fix:** derive it server-side. The DB knows every alert's timestamp and the replay clock knows the current simulated shift; count alerts in the current shift window on the server and return that count alongside the stream. This is more correct anyway — the budget is a property of the shift, not of one browser tab.

---

## MEDIUM SEVERITY

**M13. The paint zone is specified two incompatible ways.** §1 describes S13–S18 as "Surface prep, primer, base coat, clear coat, cure, inspection" (making S15 = base coat, S17 = cure). §4's config comments name **S15 "Paint Surface Prep"** and **S17 "Clear Coat Application."** Two different layouts for the same six stations. The demo script and the defect physics both depend on §4's naming, so §4 wins — but §1's prose must be rewritten to match, or `ARCHITECTURE.md` will contradict the code.

**M14. The S18 in-line gate creates selection bias.** The optional gate catching "~40% of Mode A defects" removes those units from the line. They never reach S30, so they never receive an S30 label — meaning the training population is **conditioned on having survived S18**. The model systematically under-observes exactly the defect mode the Tuesday story is built around, and its apparent Mode A recall is measured on a censored sample. Realistic, but it must be handled: train on the union of both label sources, or drop the gate. Either way it belongs in `MODEL_CARD.md` under known failure modes.

**M15. SHAP is a banned import, but `/api/predict` promises `top_factors`.** The response schema carries `"explanation_mode": "approximate"` — an acknowledgement that live SHAP is impossible inside the import wall, but the spec never says what "approximate" means. Left undefined, this becomes a fabricated explanation, which is the single worst thing to be caught doing in an explainability-graded project. **Exact TreeSHAP for a tree ensemble is roughly 100 lines of numpy** and is entirely feasible within the allowlist. Do that and label it `"exact"`; fall back to precomputed global attributions only if time runs out, and say so explicitly in the response.

**M16. "Byte-for-byte reproducible" is false as configured.** sklearn's `HistGradientBoostingClassifier` is multithreaded via OpenMP, and floating-point reduction order varies with thread count — so results differ across machines and even across runs. Set `OMP_NUM_THREADS=1` before training, or soften the claim to "metrics reproduce within tolerance."

**M17. The containment endpoint violates the operator-privacy rule.** Design principle 9 says *"Never surface individual operator performance."* The containment response returns `"shared_factors": ["same_lot", "same_operator_s15"]`, and the UI mockup renders `Lot,Opr15` in a table a supervisor reads. That is individual operator attribution on screen, tied to defect risk. The linkage is genuinely needed for containment — fix it at the API boundary by emitting an opaque rotating group label, never a stable operator ID.

**M18. The 200 MB RSS ceiling has no headroom, and `/api/health` crashes on Windows.** Idle cost of numpy (~35 MB) plus pydantic v2's compiled core, fastapi, and `uvicorn[standard]`'s uvloop/httptools is realistically **110–140 MB before serving a single request**. Adding SQLite page cache, the loaded `model.npz`, and SSE buffers puts the target right on the line. Separately, reading RSS without `psutil` (banned) means `/proc/self/statm` — which **does not exist on Windows**, so the health endpoint throws during local development on this machine. It needs a guarded fallback. Worth asking whether 200 MB is a real constraint at all: a free Hugging Face Space provides 16 GB.

**M19. The import allowlist contradicts itself and is incomplete.** §0 lists `csv` as permitted; §3 rule 10's authoritative list omits it. Also missing but genuinely required: `logging`, `re`, `collections`, `functools`, `itertools`, `hashlib`, `enum`, `sys`, `io`. And the enforcing test must inspect **first-party imports only** — fastapi transitively imports starlette, anyio, sniffio and typing_extensions, so a transitive check fails on the first run and would tempt someone to weaken the very test that protects the core constraint.

**M20. The PR-AUC target band is narrower than the measurement error.** ~20,000 units at a 3% base rate gives ~600 positives; a 3-day test split holds roughly **130**. Bootstrap confidence intervals on PR-AUC with 130 positives run to roughly **±0.08**. The spec's control rule — "if > 0.92 make the world harder, if lower check for bugs" — can therefore fire on pure sampling noise, sending the build in circles. Report bootstrap CIs everywhere and treat the band as advisory. If time allows, 21 simulated days instead of 14 raises test positives to ~200 and meaningfully tightens the interval; with event-driven simulation (B5) that is nearly free.

**M21. Isotonic calibration will overfit the validation fold.** Fitting isotonic regression on days 10–11 means roughly 90 positive examples. Isotonic is non-parametric and will produce a coarse step function that fits that sample's noise, then look poorly calibrated on test — undermining the reliability diagram that is meant to *demonstrate* calibration. Prefer Platt scaling at this sample size, or isotonic with explicit binning constraints. Show the reliability diagram with confidence bands either way.

---

## LOW / PROCESS

**L22. Local Python is 3.13.2; `numpy==1.26.4` has no cp313 wheels.** The container is `python:3.11-slim` so production is fine, but `pip install -r serve/requirements.txt` fails on this machine. Either pin numpy ≥ 2.1 for local development or standardise on 3.11 locally.

**L23. Next.js 14 against Node v24.19.0.** Next 14 targets Node 18/20. Next 15 is the safer pairing with the installed runtime.

**L24. Recharts has no native waterfall chart.** The `ValueBridge` component is specified as "Recharts waterfall." It must be constructed from a stacked bar with a transparent base series — worth knowing before hour 20, not during it.

**L25. SSE at 50× replay speed will cause a React re-render storm.** 55 UPH across 30 stations is ~27 events/s of simulated flow; at 50× that is **~1,375 events/sec**. One `setState` per event will lock the browser at exactly the moment the demo shows off the speed control. Needs `requestAnimationFrame` coalescing client-side, or downsampling server-side at high multipliers.

**L26. The stream offset-index table is missing from the schema.** `/api/stream` seeks to `from_ts` "using a prebuilt offset index in the DB," but no such table appears among the 14 defined. Add `stream_index(plant_id, ts, byte_offset)`.

**L27. A 3-year NPV is promised with no discount rate.** `BUSINESS_PROPOSAL.md` must state one, but `economics.yaml` — the file that is supposed to source every number — has no discount-rate field. Add it with a source, or the NPV becomes the one unsourced figure in a project whose central discipline is sourcing every figure.

**L28. CORS "allow all origins" plus an unauthenticated feedback endpoint sits awkwardly beside the OT-security narrative.** `ARCHITECTURE.md` argues carefully for read-only passive tapping and Purdue-level discipline, while the API accepts writes from anywhere. Defensible as a demo posture — but say so explicitly in the document before a judge finds it, because they will.

**L29. Economics assumes 250 operating days; the simulation runs 24/7.** Three 8-hour shifts covering a full 24 hours over 14 consecutive days implies continuous operation, while `operating_days_per_year: 250` implies 5-day weeks. The annualisation must state which basis it uses.

**L30. Defect mode weights are under-specified.** `mode_a: 0.45 + mode_b: 0.35 + mode_c: 0.20 = 1.00`, but the propensity formula adds `w_lot * incoming_lot_quality` and `w_noise * random_noise` **on top**. Either the mode weights are not the whole budget or the formula is wrong. The normalisation must be stated explicitly, because it directly controls the realised defect rate the whole simulator is tuned to hit.

**L31. Mode B's behavioural premise needs justification.** The rush trigger is Station 15 being blocked > 60 s and then clearing. But a **blocked** station is blocked precisely because downstream cannot accept its output — an operator rushing while blocked gains nothing; the unit simply waits longer in the buffer. The mechanism only makes sense on the *unblocking burst*, when a full buffer drains at once and the operator faces a visible queue plus shift-quota pressure. That is a real and well-documented behaviour, but it must be stated in `ASSUMPTIONS.md` — a judge with manufacturing background will pull this thread, and "the operator rushed while blocked" is not a defensible answer.

**P32. There is no time budget and no MVP cut line.** Ten phases at the specified depth is 40–80 hours of work. The spec's instruction — *"Do NOT stop to ask for approval... Start now. Phase 1. Go."* — optimises for autonomy, which is right, but with no de-scope ladder an agent will spend hours on `plant_b.yaml`'s 42 stations while the Supervisor view does not exist. **Given a sub-24-hour budget this is the most practically dangerous omission in the document.** See `docs/STATE.md` for the rubric-aware ladder.

**P33. Committing `artifacts/` every phase bloats git irreversibly.** The spec says commit after each phase and lists `artifacts/` as committed. SQLite files do not delta-compress, so each rebuild adds a fresh ~50 MB blob that git history keeps forever. Commit artifacts once, at the end.

---

## What the spec gets right

Worth recording, so none of it gets refactored away under time pressure:

- **The Tuesday Problem is a genuinely good causal scenario** — two correlated defect modes with different root causes (physics versus human behaviour), each requiring different evidence to detect. That is a real thing that happens on real lines, and it is the reason this project has a story rather than a dashboard.
- **Precompute-heavy / serve-thin** is the correct architecture for the deployment constraint, and the pure-numpy tree scorer with a parity test against sklearn is a genuinely strong engineering artifact.
- **Masked-station validation** for the soft sensors — hiding data you actually have and measuring how well you recover it — is the single most credible thing in the document. It converts a claim into a measurement. Do not cut it.
- **The instruction to cap PR-AUC and report honest negative ablations** shows the right instinct about what a technical judge rewards.
- **The compliance table** mapping each rubric bullet to specific code paths is exactly how to make a grader's job easy.
- **Provenance as a first-class contract** propagating DB → API → UI is the right way to handle mixed legacy/modern instrumentation, and it is rare to see it specified that rigorously.
