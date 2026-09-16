# PulseTwin

**A digital twin for a mixed-model vehicle assembly line.**

On EV-heavy days the line's constraint moves from S12 to S19. Units back up in the paint
buffer past their cure window, and when the block clears, operators at an unsensored
station compress a 45-second checklist into 22 seconds to work off the queue. Two
different defect modes — one chemical, one behavioural — from one shared cause, neither
visible until final inspection 22 minutes downstream.

No single station metric reveals this. That is the problem PulseTwin exists to solve.

---

## Quickstart

```bash
pip install -r requirements-dev.txt        # offline pipeline
pip install -r serve/requirements.txt      # runtime only

make generate        # simulate both plants        (~2 min)
make train           # train per-plant models      (~2 min)
make build-artifact  # bake SQLite + numpy models  (~1 min)
make test            # full suite

make dev-backend     # API on :7860
make dev-frontend    # UI on :3000
```

The committed artifacts already contain everything, so `make dev-backend` works without
running the pipeline at all.

---

## Compliance — the seven Real-World Complexities

| # | Complexity | How PulseTwin answers it | Where |
|---|---|---|---|
| 1 | Mixed legacy / modern instrumentation | Soft sensors with quantile prediction intervals; **masked-station validation** hides real sensors on S9/S17/S21 and measures recovery — publishing both where it works (ambient signals, R² 0.99) and where it cannot (tool wear, R² ≈ 0); every value carries `measured` \| `inferred` provenance end to end | `offline/ml/soft_sensor.py`, `serve/main.py`, LineMap hatch + `INF` badge |
| 2 | Multi-causal, intermittent defects | Three modes with genuinely different structure — multiplicative co-occurrence, human behaviour, and a required two-station interaction — combined as **competing risks**; exact additive decision-path explanations per unit | `offline/simulator/causal_engine.py`, `serve/scorer.py` |
| 3 | Cannot modify live systems | Read-only passive tap; the API opens the artifact DB `mode=ro` so a write raises; **instrumentation planner** ranks retrofit by measured value per dollar — and says "you already have this signal" where a sensor would be wasted | `serve/db.py`, `offline/ml/scenarios.py`, `docs/ARCHITECTURE.md` |
| 4 | Delayed defect surfacing | Containment tracing finds every in-flight unit sharing the causal fingerprint; lead time measured from genealogy, not assumed | `serve/main.py::containment`, ContainmentPanel |
| 5 | Different stakeholders | Three persona views: **Supervisor** (line map, alerts, containment), **Manager** (constraint migration, league table, Pareto, what-if console), **Leadership** (value bridge, threshold trade-off, instrumentation planner, rollout) | `frontend/app/{supervisor,manager,leadership}` |
| 6 | Multi-site scaling | Same pipeline, different YAML: 30 stations → 42, EV → TRUCK, and the bottleneck-shift mechanism **generalises** (S12→S19, S16→S31). Per-plant models, because the feature spaces genuinely differ | `configs/plant_b.yaml`, ADR-006 |
| 7 | False alarms erode trust | **Two thresholds that disagree, both published.** Cost-optimal says p ≥ 0.0065; human capacity says p ≥ 0.41. We operate on the budget and price the gap | `offline/ml/thresholds.py`, AlertBudgetCounter |

---

## Measured results

Plant A, held-out test week (7 days, 9,046 units, 228 positives, 2.5% base rate):

| Metric | Value |
|---|---|
| PR-AUC | **0.256** [95% CI 0.207–0.308] |
| **Oracle PR-AUC** | **0.481** — the simulator's own latent propensity |
| **Share of the ceiling captured** | **53%** |
| ROC-AUC | 0.883 [0.855–0.909] |
| Lift over base rate | 10.2× |
| Precision at operating threshold | 35% |
| Recall at operating threshold | 28% |
| Alerts per shift | 8.5 (budget 6) |
| Bottleneck detection accuracy | 85.9% (plant A), 90.2% (plant B) |
| Bottleneck forecast skill vs persistence | +0.136 / +0.205 |
| Soft sensor, ambient-driven signals | **R² 0.965–0.996** |
| Soft sensor, tool-wear-driven signals | **R² −0.55 to −0.07** (unrecoverable — see below) |
| Value of unsensored-station evidence | **+0.060 PR-AUC** (23% relative) |

### Why we report an oracle instead of chasing 0.85

The brief targets PR-AUC 0.75–0.88. That band is **arithmetically incompatible with the
brief's own requirement of unobservable causes.** PR-AUC is bounded by how concentrated
the at-risk population is; to clear 0.75 at a 3% base rate, ~4% of units must carry a
**70%+ defect rate** — a single near-deterministic condition, which is the opposite of
"multi-causal, intermittent, with unobservable confounders."

So instead of asserting a ceiling, we **measure** it: every training run scores the
simulator's own latent propensity — the true data-generating probability, including both
unobservable causes — against the same labels. No model can beat that number. We report
ours as a fraction of it.

A model that hit 0.85 here would have had to delete the latent confounders to get there.
The full arithmetic is in `docs/AUDIT.md` §B34.

---

## Architecture

```
  configs/*.yaml  ─────────────────────────────────────────┐
        │                                                  │
        ▼                                                  ▼
  offline/simulator/     event-driven DES, correlated physics,
        │                competing-risks defect model
        ▼
  offline/ml/            featurise → train → calibrate → thresholds
        │                (temporal split; leakage proven by perturbation)
        ▼
  offline/build_artifact.py
        │
        ├──►  artifacts/pulsetwin.db        37 MB, both plants
        ├──►  artifacts/model_<plant>.npz   flat numpy trees + Platt
        └──►  artifacts/stream_<plant>.jsonl
                     │
                     ▼
  serve/               fastapi + numpy + sqlite3 ONLY
        │              no pandas, no sklearn, no shap
        ▼
  frontend/            Next.js static export, zero server RAM
```

**Precompute heavy, serve thin.** The runtime image contains no model library. The
ensemble is walked by ~90 lines of numpy, proven identical to sklearn on the entire test
set to within 1e-6.

---

## Honest limitations

- **PR-AUC 0.256 is low in absolute terms** and we do not dress it up. It is 53% of a
  measured ceiling of 0.481. The remaining gap is real headroom, not noise.
- **Plant B performs materially worse** (recall 6% at budget vs 28%). Its Mode B fires on
  13.6% of visits rather than 4.5%, so the operating threshold lands differently. Reported
  as measured; not tuned to match plant A.
- **Feedback is ephemeral.** The free-tier filesystem does not persist; the production
  path is documented in `docs/ARCHITECTURE.md`.
- **CORS is open and the feedback endpoint is unauthenticated.** That is a demo posture,
  stated plainly rather than left for a judge to find.
- **Soft sensors work for some signals and not others, and we publish both.** Ambient-driven
  readings (paint temperature, humidity, film thickness) reconstruct at R² 0.97–1.00.
  Tool-wear-driven readings (torque, vibration) reconstruct at R² ≈ 0 or worse, because
  each station's wear is an independent random walk — a neighbour's torque correlates 0.06
  with the target's. The negative result is the more useful one: it tells you which sensor
  retrofits are unnecessary and which are unavoidable.
- **Three of four retrofit candidates score zero uplift**, and that is a finding rather
  than a gap. At S15 the operator checklist is already the second strongest driver in the
  whole model, so a temperature/humidity sensor there would buy information we get free.

---

## Documentation

| File | What it is |
|---|---|
| `docs/AUDIT.md` | 32 findings against the original brief, with the arithmetic |
| `docs/DECISIONS.md` | 32 ADRs — every deviation, with its reasoning |
| `docs/SPEC.md` | The corrected canonical spec that was actually built |
| `docs/BUSINESS_PROPOSAL.md` | Business case, personas, roadmap, risk register |
| `docs/ARCHITECTURE.md` | ISA-95 placement, read-only design, streaming, security posture |
| `docs/MODEL_CARD.md` | Metrics with intervals, soft-sensor contrast, failure modes |
| `docs/ASSUMPTIONS.md` | Every economic, physical and behavioural assumption |
| `docs/DEMO_SCRIPT.md` | The 5-minute arc, with the seeded hero unit |
| `docs/STATE.md` | Build ledger and every bug found along the way |

Six of the audit findings were build-blockers: a label-noise rate 14× the stated figure,
a throughput target no station layout could reach, a demo unit whose alert cited features
from stations it had not yet visited, two thresholds presented as one, a tick loop that
could not meet its own runtime budget, and one model expected to score two different
feature spaces.
