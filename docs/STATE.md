# PulseTwin — Build State

> **READ THIS FIRST, EVERY SESSION.** Then `docs/SPEC.md` for the build target.
> Update the ledger and the "next action" line at the end of every phase.

---

## Clock

| | |
|---|---|
| Bootstrap session | 2026-08-31 |
| Budget | **Under 24 hours** |
| Deadline (fill in) | `___1 september 2026_________________` |
| Tier 0 must close by | **T-14h** |
| Tier 1 must close by | **T-6h** |
| T-3h onward | Freeze code. Docs, demo rehearsal, screenshots only. |

**The ladder is authoritative.** When the clock and the spec disagree, the clock wins —
drop to the next tier rather than half-finishing the current one. A working Supervisor view
with three cut features beats five half-built views.

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

### Still open (Tier 2, cut per the ladder)

- Manager and Leadership **views** (their APIs exist and are tested: `/api/bottleneck/*`,
  `/api/stations/stats`, `/api/economics/roi`, `/api/metrics`)
- What-if grid (ADR-007)
- Instrumentation planner
- Docker Compose (Dockerfile written; `uvicorn` + `next dev` demos identically)
- `BUSINESS_PROPOSAL.md`

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
```
