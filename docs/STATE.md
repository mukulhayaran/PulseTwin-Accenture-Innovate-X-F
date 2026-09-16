# PulseTwin — Build State

> **READ THIS FIRST, EVERY SESSION.** Then `docs/SPEC.md` for the build target.
> Update the ledger and the "next action" line at the end of every phase.

---

## Clock — Round 2 CLOSED, Finals upgrade IN PROGRESS

| | |
|---|---|
| Bootstrap session | 2026-08-31 |
| Round 2 build closed | 2026-09-02 |
| Original budget | Under 24 hours, deadline 1 September 2026 |
| Finals upgrade (`pulsetwin_final_prompt.md` §Finals, phases F1-F8) | started, then stalled mid-build; resumed and closed out 2026-09-16 |

**Everything on the Round 2 ladder was built — Tier 0, Tier 1 and Tier 2.** Nothing from
the original brief was cut. The de-scope ladder below is kept as the historical record of
how that work was ordered, not as an outstanding plan.

The ladder did its job: it forced the Supervisor view and the honest-metrics work ahead of
the what-if console and the instrumentation planner, so the parts a judge grades hardest
were finished first and the optional ones landed on top rather than instead.

---

## Finals upgrade — Phases F1-F8

The finals brief (real-time ingestion, MLOps lifecycle, RBAC/audit, graceful degradation,
a 4th Ops persona, K8s/Terraform, and eight new docs) was substantially built in an
earlier session but left in an inconsistent state: most backend and infrastructure work
existed, but 4 tests were red, Prometheus metrics were never wired despite the
dependencies being installed, `frontend/app/ops/page.tsx` did not exist at all despite 4
of its 7 components being built, and several of the finished pages had shared components
(`OfflineBanner`, `SensorDropoutAlert`) imported but never rendered. This session's job
was auditing what was actually there (by reading source, not trusting import lines) and
closing every gap found.

| Phase | What shipped | Evidence |
|---|---|---|
| F1 Postgres | `migrations/001-004`, `serve/db_postgres.py`, `offline/load_to_postgres.py`, SQLite fallback for local dev | ADR-034 (a real FK bug the migration caught); `tests/test_api.py` passes against the SQLite path |
| F2 Ingestion | `ingestion/` — unified namespace, MQTT publisher with fault injection (dropout/late-arrival/duplicate/corrupt) and a live `control/dropout` topic for demo use, subscriber with dedup + reordering, per-station data-quality scoring | `tests/test_ingestion.py` (fixed a completeness-scoring bug this session — see below) |
| F3 MLOps | `mlops/` — PSI + calibration + coverage drift detection, champion-challenger retrainer, model registry, SPC fallback | ADR-035 (published the textbook Nelson-rule fallback's real, poor shadow performance rather than a nicer invented number) |
| F4 Auth/RBAC/audit | `serve/middleware/auth.py` (role hierarchy), `audit.py` (every mutating action logged), `rate_limit.py`; frontend `RoleGate`, per-page role badges, and (new this session) an actual interactive role selector on `/ops` | `tests/test_auth.py`, `tests/test_audit.py` |
| F5 Graceful degradation | Backend: SPC fallback on a broken model file, Prometheus metrics (**wired this session** — `serve/metrics.py`, previously just an unused dependency), Grafana dashboard + Prometheus/Alertmanager configs (**written this session**, none previously existed). Frontend: `OfflineBanner`/`StaleDataIndicator`/`SensorDropoutAlert` **connected to live JSX this session** on `/supervisor` (they existed as dead imports before), and added to `/manager`, `/leadership`, `/ops` | `tests/test_graceful_degradation.py`, `tests/test_sensor_dropout.py` (fixed a test-fixture bug and a real "latest row" ordering bug this session — see below) |
| F6 Ops view | `frontend/app/ops/page.tsx` **did not exist before this session** despite 4 of its 7 components already being built. Built the page plus the 3 missing components (`SensorHealthGrid`, `AuditLogViewer`, `RetrainingConsole`) | manual review; `npx tsc --noEmit` clean across the whole frontend |
| F7 K8s + Terraform | `infrastructure/kubernetes/*.yaml` (namespace, deployments, HPA, OT/IT network policy) already existed and is thorough. `infrastructure/terraform/main.tf` and `infrastructure/monitoring/{prometheus.yml,alertmanager.yml,grafana-dashboards/pulsetwin-overview.json}` **did not exist; written this session** | file review; YAML/JSON parse-validated |
| F8 Docs | All eight — `PRODUCTION_ARCHITECTURE.md`, `SECURITY.md`, `MLOPS_LIFECYCLE.md`, `DATA_GOVERNANCE.md`, `CHANGE_MANAGEMENT.md`, `SCALING_COST_MODEL.md`, `INCIDENT_RUNBOOK.md`, `FINALS_DEMO_SCRIPT.md` — **did not exist; written this session**, sourced against real ADRs/configs, not invented figures | this ledger |

### Real bugs found and fixed while closing out the finals upgrade

16. **`DataQualityTracker.score()` under-counted completeness for a station that hadn't
    been running for a full window yet**, scoring a perfectly healthy 20-reading stream
    at 86.7% instead of ~100% because it divided by a hypothetical full-`window_s` count
    rather than the count expected over the span actually observed. Fixed to measure
    expected count against `events[-1] - events[0]`, which converges to the original
    intent once the window is genuinely full. Caught by `tests/test_ingestion.py`.
17. **`test_sensor_dropout.py` corrupted its own fixture.** Two of its four tests opened a
    second, ad-hoc `TestClient(app)` instead of reusing the module-scoped one — re-entering
    `serve.main`'s lifespan on the same process-global `STATE` and closing
    `app.state.ops` out from under the next test, which then hit
    `sqlite3.ProgrammingError: Cannot operate on a closed database`. Fixed by routing
    every test through the shared fixture, matching every other test module's convention.
18. **`sensor_health`'s "latest row per (station, sensor)" query ordered by `timestamp`
    alone**, which only has millisecond resolution — two status transitions for the same
    sensor landing in the same millisecond (exactly what a fast test, or a real burst of
    ingestion events, produces) picked the wrong "latest" row via SQLite's undefined tie-
    break order. Fixed to order by `id` (strictly monotonic on insert) instead.
19. **Prometheus metrics were never wired**, despite `prometheus-client` and
    `prometheus-fastapi-instrumentator` being installed specifically for this (ADR-033)
    and the finals brief listing seven named series. `serve/metrics.py` now defines them;
    `serve/main.py`, `system.py`, and `model_health.py` update them at the actual call
    sites (`Instrumentator().instrument(app).expose(app)` adds the generic HTTP series
    alongside).
20. **Two import-wall gaps**: `serve/middleware/rate_limit.py` needs `starlette` (fastapi
    has no ASGI middleware base of its own) and `serve/routers/admin.py` needs
    `subprocess` (to spawn the retrain job as a detached process rather than importing
    `mlops` into `serve/`). Both are legitimate, both are now ADR-036, both are in
    `tests/test_serve_imports.py`'s `ALLOWED` set.
21. **`frontend/app/supervisor/page.tsx` imported `OfflineBanner` and
    `SensorDropoutAlert` and declared their backing state, but never rendered either
    component**, and `lastKnownAt` was never set. The dropout-polling `useEffect` was
    real; only the JSX to show it was missing. Now both render, `lastKnownAt` updates on
    every stream event, and a `retryNonce` state re-opens the SSE connection so
    `OfflineBanner`'s [Retry Now] button actually does something.
22. **`serve/Dockerfile` never copied `migrations/`**, even though
    `migrations/migrate.py`'s own docstring says the `migrate` service in
    `docker-compose.prod.yml` runs on that exact image with its `CMD` overridden — that
    service would have built successfully and then crashed at container start with
    `ModuleNotFoundError`. Added `COPY migrations/ ./migrations/`.
23. **Wiring `Instrumentator().instrument(app)` (new this session) crashed almost every
    endpoint** with `AttributeError: '_IncludedRouter' object has no attribute 'path'` —
    this dev environment's ambient FastAPI (0.139.0) is newer than
    `prometheus-fastapi-instrumentator==7.0.0` (pinned in `serve/requirements.txt`)
    understands internally. Bumped the pin to `8.1.0`, which resolves cleanly against a
    modern FastAPI/Starlette (`pip check` clean) and fixed every affected test.
24. **My own `from serve import metrics` collided with `serve/main.py`'s pre-existing
    `def metrics(...)` — the `GET /api/metrics` endpoint function**, both bound to the
    same module-scope name, and the later definition silently won, so every
    `metrics.predictions_total`-style call raised `AttributeError: 'function' object has
    no attribute 'predictions_total'`. Imported as `from serve import metrics as
    prom_metrics` instead.

**All 4 originally-failing tests, all tests broken while wiring Prometheus metrics, and
the full 226-test suite pass as of this session's close** (`OMP_NUM_THREADS=1 pytest
tests/ -q` → `226 passed`).

**Not verified this session:** `docker compose -f docker-compose.prod.yml up --build`
end-to-end. The Docker CLI is present in this sandbox but has no daemon behind it (no
`Docker Desktop.exe` process, no `com.docker.service`) — `docker compose up` fails
immediately with a named-pipe connection error, before any image even builds. Every
manifest and Dockerfile was reviewed by hand instead (catching bug #22 above); an actual
`up` run needs a machine with a working Docker daemon.

---

## Status

**Phase 0 — Bootstrap: COMPLETE** (2026-08-31)

- [x] Spec audited — 31 findings, 6 blockers (`docs/AUDIT.md`)
- [x] 28 ADRs resolving every finding (`docs/DECISIONS.md`)
- [x] `CLAUDE.md` — session rules, import wall, three contracts
- [x] Git guardrails — push denied in `.claude/settings.json`; `includeCoAuthoredBy: false`
- [x] Plugins installed: frontend-design, feature-dev, pyright-lsp, typescript-lsp,
      playwright, claude-md-management, commit-commands
- [x] `docs/SPEC.md` — corrected canonical spec
- [x] `git init` + first commit

**Phase 1 — Configs: COMPLETE** (2026-08-31)

- [x] `configs/plant_a.yaml` — 30 stations, 4 manual, cycles 45–61s
- [x] `configs/plant_b.yaml` — 42 stations, 8 manual, TRUCK variant, cycles 45–70s
- [x] `configs/economics.yaml` — every figure sourced; discount rate + annualisation basis
- [x] `offline/config_schema.py` — Pydantic schemas with ADR-enforcing validators
- [x] `tests/test_configs.py` — **43 passing**

**Verified from config alone:**
- plant_a: S12 (62.1s) → **S19 (64.6s)** on EV-heavy days ✓
- plant_b: S16 (72.1s) → **S31 (74.9s)** on truck-heavy days ✓
- The bottleneck shift mechanism **generalises across both plants** — it is not tuned per plant.
- Cost-optimal threshold p\* = **0.0052**; alarm budget = 6/shift. Irreconcilable, as ADR-004 predicts.

**Correction made this phase:** ADR-002 originally specified S19 base = 58s. That value makes
S19 the constraint on *normal* days too (62.5s vs S12's 62.1s), silently deleting the entire
Tuesday scenario. Corrected to **55s**. `check_bottleneck_shift_configured` now fails any
config whose constraint does not move with variant mix — the validator caught this on its
first run. `docs/SPEC.md` and ADR-002 both amended.

**Phases 2-8 — COMPLETE** (2026-08-31)

| Phase | What shipped | Evidence |
|---|---|---|
| 2 Simulator | Event-driven DES, correlated physics, competing-risks defects | 12/12 acceptance checks, both plants, 98s |
| 3 Featurise | 253 features, timestamp-based leakage guard | perturbation test passes |
| 4 Soft sensors | Quantile regressors + masked-station validation | `offline/ml/soft_sensor.py` |
| 5 Model | HistGB + Platt + dual thresholds + oracle ceiling | PR-AUC 0.256 [0.207-0.308] |
| 6 Artifacts | SQLite 37.5 MB + numpy models + stream JSONL | parity < 1e-6 |
| 7 Serve | FastAPI, import wall enforced, async SSE | 330ms under load |
| 8 Frontend | Supervisor view + landing, static export | control-room UI |
| 10 Docs | AUDIT, DECISIONS, SPEC, ARCHITECTURE, MODEL_CARD, ASSUMPTIONS, DEMO_SCRIPT, README | — |

**100 tests passing.**

### Headline measured results

| | plant_a | plant_b |
|---|---|---|
| Throughput | 53.8 UPH (97.8% of target) | 45.4 UPH (94.5%) |
| Defect rate | 2.99% | 2.82% |
| PR-AUC | 0.256 [0.207-0.308] | 0.052 |
| Oracle ceiling | 0.481 (53% captured) | 0.075 (69% captured) |
| Bottleneck accuracy | 87.9% | 79.1% |
| Bottleneck shift | S12 -> S19 | S16 -> S31 |

### Bugs found and fixed during the build

1. **Line deadlock** — a station breaking down while blocked never recovered; throughput
   collapsed 55 -> 6 UPH. `_on_repair_done` now retries the release.
2. **Manual stations were accidental bottlenecks** — cycle formula ignored their
   configured nominal.
3. **Mode C sign error** — poor lot quality raised torque while raising vibration,
   anti-correlating the interaction. Mode C fired 5 times in 27k units.
4. **Additive logistic could not work** — no single mode could clear the intercept alone.
   Replaced with competing risks; mode shares now track configured weights.
5. **Test split had no peak days** — 21-day horizon put test on Thu-Sun. Moved to 28 days
   of whole weeks.
6. **Circular bottleneck accuracy** — detector and ground truth used the same definition
   and reported 100%. Ground truth now uses a 2-hour window vs the detector's 15 minutes.
7. **Explanation additivity** — contributions telescope to `leaf - root`, so they sum to
   `raw_score - expected_value`, not `- baseline`. Also switched to sample-weighted node
   values (proper Saabas).
8. **PR-AUC band unreachable** — see AUDIT B34. Now measured against an oracle ceiling.

**Phase 9 + remaining Tier 2 — COMPLETE** (2026-09-01)

| Item | Evidence |
|---|---|
| Manager view | Constraint migration, league table, root-cause Pareto, what-if console |
| Leadership view | Value bridge, threshold trade-off, instrumentation planner, rollout, metrics panel |
| What-if grid (ADR-007) | 90 scenarios/plant, one 8h shift each, peak variant mix |
| Instrumentation planner | Ranked by measured value per dollar, with method stated |
| Bottleneck forecasting | Skill vs persistence +0.136 (A) / +0.205 (B) |
| Soft sensors + masked validation | The ambient vs tool-wear contrast, published both ways |
| Docker Compose + frontend Dockerfile | `make demo` |
| `BUSINESS_PROPOSAL.md` | 3-year case, risks, roadmap |
| `tests/test_api.py` | Every endpoint against real artifacts |

**Nothing from the original brief remains unbuilt.**

### Additional bugs found in this phase

9.  **What-if grid omitted the EV bottleneck.** Seeded from the single most frequent
    constraint (S12), so the grid covered S9-S14 and excluded S19 entirely -- the one
    station a manager would most want to tune. Now seeded from BOTH constraints.
10. **What-if window never reached steady state.** A 4-hour run reported 57.5 UPH (above
    the 55 target, impossible in steady state) and named S3 as the constraint, because
    buffers had not filled. Extended to one full 8-hour shift.
11. **What-if ran on the wrong variant mix.** The simulator starts on a Monday, so short
    runs used the NORMAL mix and S19 never bound -- making every change at S19 look
    worthless. The grid now forces the peak mix, which is the condition being tuned for.
12. **What-if buffer slider did nothing.** Every delta at S19 returned an identical
    52.707 UPH. The mutation was applied correctly -- it changed the station's OUTPUT
    buffer, which is irrelevant because a bottleneck is never blocked by what follows it.
    Now applied to the FEED buffer upstream, which shows a real optimum at +4 slots
    (52.7 -> 54.1 UPH) and a constraint migration to S12.
13. **Constraint detection was fill-biased.** Cumulative active time over a whole short
    window favours upstream stations, which have seen more units while the line fills --
    plant B reported S2 on a 42-station line. Now scored on the second half only.
14. **`achieved_uph` was wrong by a factor of 25, silently.** plant_meta reported
    **2.1 UPH** for a line running at 53.8. The computation divided the unit count -- by
    then trimmed to the replay window (ADR-032) -- by the FULL 28-day horizon. Nothing
    failed: the number flowed straight into `/api/economics/roi`, where
    `units_per_year = achieved_uph x 8 x 3 x 250`, and understated the entire business
    case. Found by eyeballing plant_meta after a rebuild, not by any test -- so a
    regression test now asserts achieved throughput lands within 80-105% of target.
15. **Soft sensor reported R2 = -0.30.** Not a bug in the end, but the diagnosis mattered:
    tool-wear signals are genuinely unrecoverable (neighbour correlation 0.06), while
    ambient signals reach R2 0.99. Added a paint station to the masked set so the contrast
    is measured, and reduced regressor capacity so an absent signal reports ~0 rather than
    a misleading negative.

---

## → NEXT ACTION (if time remains)

1. `npm install && npm run dev` in `frontend/`, then walk the demo script end to end.
2. Manager view — the APIs are already live and tested; this is presentation only.
3. Leadership view — `/api/economics/roi` returns everything it needs, sourced.
4. `docs/BUSINESS_PROPOSAL.md`.

**Do not** retune the model to raise PR-AUC. The oracle framing is stronger than a
higher number, and ADR-020 makes the band advisory.

---

## Original next action (Phase 1, done)

**Tier 0, item 2: the simulator.** `offline/simulator/`.

Build in this order, because each stage is testable against the one before:

1. `line_model.py` — **event-driven** scheduler (ADR-005), never a 1-second tick loop.
   States `idle | working | blocked | starved | down`; finite output buffers; exponential
   breakdowns from MTBF with gamma repair; tool-wear drift 0.1–0.5%/hr resetting after
   repair. Persist state as **intervals** `(station_id, state, start_ts, end_ts)`.
2. `physics.py` — temporally autocorrelated signals. Mean-reverting random walks for tool
   wear, thermal inertia `T(t) = α·T(t-1) + (1-α)·(T_ambient + heat)`, EWMA vibration that
   spikes on blocking→release bursts. Manual stations emit **NaN + a checklist record**.
3. `operators.py` — skill pools; the S15 rush fires on the **unblock transition** (ADR-026).
4. `causal_engine.py` — the ADR-025 propensity formula. `paint_batch_quality` and
   `incoming_lot_quality` are latent and must never reach the feature matrix.
5. `inspection.py` — S30 only (ADR-014). 92% detection, 0.2% false reject. `true_defect`
   stored separately and never shown to the model.
6. `generate.py` — CLI, writes telemetry / genealogy / checklists / inspections.

Read `docs/SPEC.md` §Phase 2 for the acceptance checks. The one to watch: **achieved UPH
within 10% of 55** — that is the check that proves ADR-002 actually worked.

Config access is already done — use `offline.config_schema.load_plant_config`. Do not
re-parse YAML anywhere else, and do not hardcode a station ID outside the config.

---

## The ladder

### TIER 0 — no demo exists without these (~10h)

| # | Item | Est | Done | Notes |
|---|---|---|---|---|
| 1 | `configs/` — plant_a, plant_b, economics + Pydantic validation | 1.5h | ✅ | 43 tests. Both plants' bottleneck shift verified |
| 2 | Simulator | 3.5h | ✅ | 12/12 acceptance checks both plants |
| 3 | Featurise + leakage test | 1.5h | ✅ | perturbation test, not a name scan |
| 4 | Defect model + thresholds | 1.5h | ✅ | dual thresholds + oracle ceiling |
| 5 | Artifacts + numpy export | 1.5h | ✅ | 37.5 MB, parity < 1e-6 |
| 6 | FastAPI core | 1.5h | ✅ | import wall enforced, async SSE verified |
| 7 | Supervisor view | 3h | ✅ | line map, alert feed, containment |
| 8 | README + compliance table | 0.5h | ✅ | all 7 rubric bullets mapped |
| 2 | Simulator — event-driven, physics, operators, causal engine | 3.5h | ☐ | ADR-005. **The foundation. If it is weak everything above is theatre.** |
| 3 | `featurize.py` + timestamp-based leakage test | 1.5h | ☐ | ADR-010. Do not skip the real test |
| 4 | Defect model + Platt calibration + both thresholds | 1.5h | ☐ | ADR-004, ADR-021 |
| 5 | `build_artifact.py` + numpy export + parity test | 1.5h | ☐ | ADR-006 per-plant, ADR-008 readings window |
| 6 | FastAPI core: `/health` `/config/stations` `/stream` `/predict` `/metrics` | 1.5h | ☐ | ADR-009 async SSE + proxy headers |
| 7 | **Supervisor view** — LineMap, AlertFeed, AlertCard, budget counter | 3h | ☐ | The most important screen. Use `design` skill to mock first |
| 8 | `README.md` + compliance table | 0.5h | ☐ | Maps all 7 rubric bullets to code paths |

**If Tier 0 is not closed at T-14h, stop and cut Tier 1 down to containment only.**

### TIER 1 — best rubric value per hour (~6h)

| # | Item | Est | Done | Rubric |
|---|---|---|---|---|
| 9 | Soft sensors + **masked-station validation** | 1.5h | ☐ | Bullet 1. **Do not cut** — strongest credibility moment in the project |
| 10 | Containment endpoint + `ContainmentPanel` | 1.5h | ☐ | Bullet 4 (delayed defect surfacing) |
| 11 | Bottleneck detection + `BottleneckTimeline` + Manager view | 2h | ☐ | Bullets 2, 5 |
| 12 | ROI endpoint + Leadership KPI cards + value bridge | 1h | ☐ | Bullet 5 |

### TIER 2 — cut in this order, first to last (~6h)

| # | Item | Cut order | Rubric cost if cut |
|---|---|---|---|
| 13 | Instrumentation planner | 6th (keep if possible) | Bullet 3 weakens — it is the differentiator |
| 14 | Reduced what-if grid (ADR-007) | 5th | The "twin" moment weakens |
| 15 | Plant B full artifacts | 4th | Bullet 6. **Keep `plant_b.yaml` loading even if its pipeline never runs** |
| 16 | Sensitivity toggle | 3rd | Minor |
| 17 | Rollout timeline | 2nd | Minor — it is in the business proposal anyway |
| 18 | Docker Compose | **1st to cut** | None. `uvicorn` + `next dev` demos identically |

---

## Rubric coverage tracker

The seven "Real-World Complexities" are the grading surface. Keep every row non-empty.

| # | Complexity | Answered by | Tier | Status |
|---|---|---|---|---|
| 1 | Mixed legacy / modern instrumentation | Soft sensors + INFERRED provenance badge | 0+1 | ☐ |
| 2 | Multi-causal, intermittent defects | 3 defect modes + TreeSHAP explanations | 0+1 | ☐ |
| 3 | Cannot modify live systems | Read-only architecture + instrumentation planner | 1+2 | ☐ |
| 4 | Delayed defect surfacing | Containment tracing + lead time | 1 | ☐ |
| 5 | Different stakeholders | 3-persona dashboard | 0+1 | ☐ |
| 6 | Multi-site scaling | Config-driven + Plant B swap | 0+2 | ☐ |
| 7 | False alarms erode trust | Dual thresholds + alarm budget + feedback | 0 | ☐ |

**Bullet 7 is fully inside Tier 0** and is the one the ADR-004 dual-threshold story makes
genuinely distinctive. Lead with it.

---

## Risk register

| Risk | Trigger | Mitigation |
|---|---|---|
| Simulator overruns 3.5h | Event scheduler debugging | Hard cap at 5h. Fall back to a coarser 5-second tick — worse fidelity, still ships |
| PR-AUC lands outside 0.75–0.88 | Reported at Tier 0 item 4 | **Do not tune toward the band.** Report the measured value with its CI (ADR-020). An honest 0.71 beats a tuned 0.85 |
| SSE dead in deployment | Only visible after deploy | ADR-009 headers from the first commit. Test through a proxy, not just localhost |
| Frontend eats the remaining clock | Tier 0 item 7 exceeds 3h | Mock with the `design` skill first. Ship Supervisor only; Manager and Leadership become static-data views |
| Both plants' pipelines overrun | Tier 2 item 15 | `plant_b.yaml` validating + loading proves config-driven. Full artifacts optional |

---

## Decision log — this session

- Original prompt kept unmodified as the historical brief; `docs/SPEC.md` is canonical.
- No GitHub push, ever. No commit trailers. Enforced, not merely documented.
- Artifacts committed once at the end, not per phase (ADR-027).
- 21 simulated days rather than 14 (ADR-020) — cheap under event-driven simulation, and it
  meaningfully tightens the PR-AUC confidence interval.

---

## Ledger

Append one line per phase as it closes. Include the wall-clock time — the ladder depends on
knowing whether we are ahead or behind.

```
2026-08-31  Phase 0  Bootstrap complete. Audit + ADRs + CLAUDE.md + guardrails + plugins.
2026-08-31  Ph2-8   Simulator -> API -> Supervisor UI. 100 tests pass.
                     PR-AUC 0.256 (53% of a measured 0.481 oracle ceiling).
                     8 real bugs found and fixed; all logged above.
2026-08-31  Phase 1  Configs complete. plant_a (30 stn) + plant_b (42 stn) + economics.
                     43 tests pass. Bottleneck shift verified on BOTH plants.
                     Caught + fixed: S19 58s->55s (58s deleted the Tuesday scenario).
2026-09-16  Finals   F1-F8 closed out. Audited a stalled prior session (mostly-built
                     backend/infra, but 4 red tests, dead Prometheus deps, and
                     frontend/app/ops/page.tsx missing entirely). Fixed 4 pre-existing
                     test failures + 3 introduced while wiring Prometheus metrics; built
                     the Ops view (3 missing components + the page); wired
                     OfflineBanner/SensorDropoutAlert/RoleGate/AuditToast/EscalationChain,
                     which existed but were never rendered on /supervisor and were absent
                     from /manager, /leadership; wrote terraform/main.tf +
                     monitoring configs + all 8 finals docs (none existed). 226/226 tests
                     pass. Docker daemon unavailable in this sandbox -- compose stack
                     reviewed by hand, not run end to end (see bugs 16-24 above).
```
