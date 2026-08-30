# PulseTwin — Build State

> **READ THIS FIRST, EVERY SESSION.** Then `docs/SPEC.md` for the build target.
> Update the ledger and the "next action" line at the end of every phase.

---

## Clock

| | |
|---|---|
| Bootstrap session | 2026-08-31 |
| Budget | **Under 24 hours** |
| Deadline (fill in) | `____________________` |
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
- [ ] `docs/SPEC.md` — corrected canonical spec
- [ ] `git init` + first commit

**Nothing else is built.** No configs, no simulator, no API, no frontend.

---

## → NEXT ACTION

**Start Tier 0, item 1: `configs/plant_a.yaml`.**

Write all 30 stations with cycle times in **45–62 s** (ADR-002), S19 base 58 s with
`ev_multiplier: 1.4`. Manual tier at S10, S15, S22, S27. Paint zone naming per ADR-013.
`inspection_false_reject_rate: 0.002` (ADR-001). Propensity weights structured per ADR-025.
Then `configs/economics.yaml` with `discount_rate` and `annualisation_basis` (ADR-024),
then `configs/plant_b.yaml`, then the Pydantic validator.

Before writing YAML, re-read `docs/SPEC.md` §Phase 1 — it carries the corrected values.

---

## The ladder

### TIER 0 — no demo exists without these (~10h)

| # | Item | Est | Done | Notes |
|---|---|---|---|---|
| 1 | `configs/` — plant_a, plant_b, economics + Pydantic validation | 1.5h | ☐ | Config-driven from hour one; makes Plant B nearly free later |
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
```
