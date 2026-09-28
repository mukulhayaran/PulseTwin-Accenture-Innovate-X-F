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
make test            # full suite (it runs real models, so allow several minutes)

make dev-backend     # API on :7860 (local dev on the SQLite build artifact)
make dev-frontend    # UI on :3000
python scripts/smoke_demo.py   # health + readiness + 10-event live-synthetic run
```

The committed artifacts already contain everything, so `make dev-backend` works without
running the pipeline at all. That is a developer convenience, not a runtime: every
deployment requires Postgres (ADR-052).

### The stack (Postgres only)

The Supervisor now exposes two honest data-source modes: **Recorded Replay** and a
server-owned **Live Synthetic Simulation**. Live synthetic runs have a run ID, seed,
scenario, shared clock/state, persistent alerts, and inspection outcomes; the screen is
always labelled to say that no real factory data is connected.

The stack runs PostgreSQL, Redis, an MQTT broker, automatic artifact loading, backend,
ingestion, and the frontend. The backend refuses to start without Postgres
(`PULSETWIN_REQUIRE_POSTGRES=1`, ADR-052):

```bash
make up              # the full stack; UI on :3000, API on :7860
make up-mqtt         # same stack plus the deterministic MQTT edge publisher
make down            # tear it down, volumes included
PULSETWIN_REQUIRE_FULL_DEMO=1 python scripts/smoke_demo.py  # DB, Redis, MQTT, publisher, ingestion, scoring, API + UI
make migrate         # apply migrations/*.sql to Postgres
make load-data       # bridge the committed artifact into Postgres
make drift-check     # PSI / calibration / sensor-coverage drift report
```

The free-tier cloud deploy (Neon + Render + Vercel) is step by step in
`docs/PRODUCTION_ARCHITECTURE.md` §DEPLOY. Retraining was removed (ADR-053): the data is a
static seed-42 simulation, so a retrain would reproduce the deployed model.

See `docs/LIVE_SYNTHETIC_DEMO.md` for the event contract, API lifecycle, and a
three-minute demonstration script.

`docs/PRODUCTION_ARCHITECTURE.md` has the OT/IT diagram this stack implements.
The stack was executed end to end on 2026-09-22, including migrations, artifact loading,
MQTT publication/ingestion, online scoring, shared live state, and complete readiness.
If port 3000 is in use, set `FRONTEND_PORT=3001` before starting it. The API remains on
7860; `/api/ready` checks the API itself and `/api/demo/readiness` checks the complete
assembled MQTT demo.

### Agentic triage (`genai/`)

An agent investigates the line through the read-only API and returns claims, each
carrying the tool call and JSON Pointer its number came from. A deterministic verifier
then re-checks every one and **removes what it cannot trace** — the model is not trusted,
it is gated.

```bash
pip install -r genai/requirements.txt
python -m genai.triage --plant plant_a --question "The constraint moved. Investigate."
python -m genai.verify_cli --run-id <id>    # re-run the gate over a stored transcript
```

Without `GROQ_API_KEY` it falls back to a **real, no-LLM triage** that runs the same tools
by fixed rules and passes the identical verifier — so the feature demos and tests with no
credential, and doubles as an honest baseline for whether the model added anything.
`docs/DECISIONS.md` ADR-037 has the reasoning.

### AI Operations Copilot (`/copilot`)

The agent, as a conversation. Ask in plain language, or run a named action — investigate
an incident, generate shift intelligence, generate a handover, run a what-if. Available
full-page at `/copilot` and as a dock on every persona view.

```bash
make dev-backend && make dev-frontend        # then open http://localhost:3000/copilot
python -m genai.triage --plant plant_a --intent handover --mode deterministic
```

Four things make it different from a chatbot bolted onto a dashboard:

- **Nothing reaches the screen before the verifier passes on it.** The run is
  non-streaming on purpose — streaming the model's text would render numbers before they
  were checked. What streams is tool activity: a phase trail showing *Checking production
  state → Examining risk signals → Running simulation → Verifying every number*. Model
  prose never enters it, and a test asserts that.
- **Citations do not cross turns.** Call ids restart every run, so a replayed citation
  could resolve against a different turn's evidence and be marked verified on a
  coincidence. Only already-verified *values* carry forward, as a prose exemption and
  nothing else (ADR-039).
- **Every named action works with no model.** Each has a deterministic plan that passes
  the same gate — the demo survives a provider outage with its guarantees intact.
- **The evidence is computed in the backend.** Alert triage, shift summaries, data
  quality and the evidence graph are deterministic endpoints; the agent narrates them.
  A verifier can only check a claim against a tool result, so the tool result has to be
  the trustworthy thing.

The agent is **read-only**: eighteen tools, and the one that is not a GET posts to a
simulator. It cannot promote a model, change a config, or touch the line.
`docs/DECISIONS.md` ADR-038/039 and `docs/COPILOT_DEMO_SCRIPT.md` have the rest.

---

## Compliance — the seven Real-World Complexities

| # | Complexity | How PulseTwin answers it | Where |
|---|---|---|---|
| 1 | Mixed legacy / modern instrumentation | Soft sensors with quantile prediction intervals; **masked-station validation** hides real sensors on S9/S17/S21 and measures recovery — publishing both where it works (ambient signals, R² 0.99) and where it cannot (tool wear, R² ≈ 0); every value carries `measured` \| `inferred` provenance end to end | `offline/ml/soft_sensor.py`, `serve/main.py`, LineMap hatch + `INF` badge |
| 2 | Multi-causal, intermittent defects | Three modes with genuinely different structure — multiplicative co-occurrence, human behaviour, and a required two-station interaction — combined as **competing risks**; exact additive decision-path explanations per unit | `offline/simulator/causal_engine.py`, `serve/scorer.py` |
| 3 | Cannot modify live systems | Read-only passive tap; the API opens the artifact DB `mode=ro` so a write raises; **instrumentation planner** ranks retrofit by measured value per dollar — and says "you already have this signal" where a sensor would be wasted | `serve/db.py`, `offline/ml/scenarios.py`, `docs/ARCHITECTURE.md` |
| 4 | Delayed defect surfacing | Containment tracing finds in-flight units sharing production context; lead time is measured from genealogy, not assumed | `serve/main.py::containment`, ContainmentPanel |
| 5 | Different stakeholders | Four persona views: **Supervisor** (line map, alerts, containment), **Manager** (constraint migration, league table, Pareto, what-if console), **Leadership** (value bridge, threshold trade-off, instrumentation planner, rollout), and **Ops** (model health, drift, ingestion lag, audit log — the team that runs the twin itself) | `frontend/app/{supervisor,manager,leadership,ops}` |
| 6 | Multi-site scaling | Config-driven: a new plant is a new `configs/<plant>.yaml` with no code edit, and models are per plant because feature spaces differ. One plant ships (plant B was removed, ADR-068) | `offline/config_schema.py`, ADR-006 |
| 7 | False alarms erode trust | **Two thresholds that disagree, both published.** Cost-optimal says p ≥ 0.0065; human capacity says p ≥ 0.41. We operate on the budget and price the gap | `offline/ml/thresholds.py`, AlertBudgetCounter |

---

## Measured results

Plant A, held-out test week (7 days, 9,046 units, 228 positives, 2.5% base rate):

| Metric | Value |
|---|---|
| PR-AUC | **0.256** <!--metric:plant_a.pr_auc:.3f--> [95% CI 0.207<!--metric:plant_a.pr_auc_ci_low:.3f-->–0.308<!--metric:plant_a.pr_auc_ci_high:.3f-->] |
| **Oracle PR-AUC** | **0.481** <!--metric:plant_a.oracle_pr_auc:.3f--> — the simulator's own latent propensity |
| **Share of the ceiling captured** | **53%** <!--metric:plant_a.oracle_capture:.0%--> |
| ROC-AUC | 0.883 <!--metric:plant_a.roc_auc:.3f--> [0.855–0.909] |
| Lift over base rate | 10.2× |
| Precision at operating threshold | 35% <!--metric:plant_a.precision_at_budget:.0%--> |
| Recall at operating threshold | 28% <!--metric:plant_a.recall_at_budget:.0%--> |
| Alerts per shift | 8.5 <!--metric:plant_a.alerts_per_shift:.1f--> (budget 6) |
| Bottleneck detection accuracy (self-consistency, NOT independent — see below) | 85.9% <!--metric:plant_a.bottleneck_detection_accuracy:.1%--> |
| Bottleneck forecast skill vs persistence | +0.136 <!--metric:plant_a.forecast_skill_vs_persistence:.3f--> |
| Soft sensor, ambient-driven signals | **R² 0.965–0.996** |
| Soft sensor, tool-wear-driven signals | **R² −0.55 to −0.07** (unrecoverable — see below) |
| Value of unsensored-station evidence | **+0.060 <!--metric:plant_a.soft_sensor_ablation_delta:.3f--> PR-AUC** (23% relative) — but the confidence intervals overlap, so the artifact itself records it as "improvement is within confidence intervals, reported as measured" |
| Fallback (SPC) shadow precision / recall | 3.0% / 100% — published as measured, see ADR-035 |

Every figure above is read from `artifacts/pulsetwin.db` (`metrics`, `metrics_json`,
`plant_meta`), not transcribed by hand. The numbers carrying an invisible
`<!--metric:...-->` marker are **machine-checked** against `artifacts/metrics_summary.json`
by `python tools/metrics_summary.py --check`, which CI runs — a README number that drifts
from its artifact fails the build.

### Independent bottleneck validation

The 85.9% / 90.2% row above is a **self-consistency** figure, not an accuracy: its
reference is the same active-share matrix the detector consumes, smoothed over a longer
window (`offline/ml/bottleneck.py::ground_truth`). Detector and reference share any bias in
the statistic.

An independent ground truth now exists. For each station, slow its cycle time by 10% under
Common Random Numbers, rerun the line across 30 seeds, and measure the throughput lost —
the constraint is the station that costs the most. Plant A, `baseline` scenario, 5-day
horizon, bootstrap 95% CIs:

| Detector | top-1 | top-3 | MRR | Mean station distance |
|---|---|---|---|---|
| **Active Share** (shipped) | **0.93** | 1.00 | 0.967 | 0.5 |
| Utilization | 0.93 | 1.00 | 0.967 | 0.5 |
| Active Period (Roser) | 0.70 | 1.00 | 0.850 | 2.1 |
| Turning Point (adapted) | 0.00 | 1.00 | 0.500 | 7.0 |
| Arrow blocked/starved (adapted) | 0.00 | 1.00 | 0.500 | 7.0 |
| Average Queue Length | 0.00 | 0.00 | 0.078 | 9.0 |
| Busy Ratio | 0.00 | 0.00 | 0.069 | 18.0 |

Ground truth S19, runner-up S12, margin 5.01 UPH per unit relative slowdown, CIs
non-overlapping. Plain utilisation **ties** the shipped detector here, so this evidence
does not justify preferring active share. Source: `artifacts/benchmarks/`. Method and
limitations — including the six scenarios still **NOT RUN** — in
`docs/BOTTLENECK_BENCHMARK.md`.

### Within-alert discrimination — a negative result

Plant A's 113 alerts share **one** score. Unique ratio 0.9%, normalised entropy 0.000,
within-cohort ranking AUC **0.500** — literally random ordering. The model identifies the
at-risk set and cannot rank it. Whether this is specific to plant A or inherent to the
architecture is not established: with one plant there is nothing to compare against.

Measured by `python -m offline.benchmarks.score_diversity` →
`artifacts/benchmarks/score_diversity.json`. **Not fixed** — see `docs/REQUIREMENTS.md`.

### Live simulation

`docs/LIVE_SIMULATION.md` distinguishes three sources: Recorded Replay, Scenario Injection
(which writes risk directly and is kept as a scripted demo), and **true Live Simulation** —
a stepped discrete-event run where an incident changes a physical condition and the trained
model reacts, or does not. In a 12-day plant A run the twin produced 0 alerts through
simulated hour 22.8, then 5 → 27 → 15 → 9 as the week reached its EV-heavy, high-humidity
days, at a 2.974% defect rate against ~3% configured. No risk value is written by hand, and
an AST guard in the test suite enforces it.

If you rebuild the artifact, these move — update this table, `docs/MODEL_CARD.md`,
`docs/CLAIMS.md`, `docs/STATE.md` and `docs/ASSUMPTIONS.md` together, then run
`python tools/metrics_summary.py --write` and `python tools/verify_artifacts.py --write`.

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

## The Incident Commander (ADR-067)

PulseTwin does not stop at "something is wrong". The **Incident Commander**:
- decides which evidence would best separate competing root causes, and gathers it from
  whichever specialist can supply it: reliability, production, quality or genealogy, data
  quality, or memory;
- scores the hypotheses with a published rule, capped by how well the station is sensed;
- tests the fitting intervention in the digital twin;
- abstains when the evidence, the data or the model cannot support a decision;
- routes every action through an autonomy governor (L0–L4, never OT) to a manager;
- coordinates approved work through CMMS/MES/QMS adapters, idempotently;
- measures whether the action worked, and remembers the case across plants.

The LLM is optional. When present it only proposes the next step as JSON, which the server
validates like its own. On **/supervisor**, pick a demo preset (or report an issue) and watch
the investigation; decide it on **/manager** under Decisions;
`docs/GRAND_FINALE_DEMO.md` is the script, `docs/AGENT_ARCHITECTURE.md` the design,
`docs/AUTONOMY_AND_SAFETY.md` the safety model, and `docs/AI_EVALUATION.md` what was
measured and what it got wrong.

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
        ├──►  artifacts/pulsetwin.db        38 MB, both plants
        ├──►  artifacts/model_<plant>.npz   flat numpy trees + Platt
        └──►  artifacts/stream_<plant>.jsonl
                     │
                     ▼
  serve/               fastapi + numpy + stdlib, plus I/O drivers only
        │              no pandas, no sklearn, no shap, no scipy
        ▼
  frontend/            Next.js static export, zero server RAM

  ── finals upgrade ──────────────────────────────────────────────────

  ingestion/     MQTT (Unified Namespace) → dedup/reorder → data quality
        │        the ONLY component allowed to touch the OT network
        ▼
  PostgreSQL     migrations/001-016; REQUIRED by every deployment (SQLite = build/test only)
        ▲
        │
  mlops/         drift detection + SPC fallback baselines (read-only; no retraining)
                 pandas/sklearn live HERE, never in serve/

  ── agentic tier ────────────────────────────────────────────────────

  genai/         tool-use loop over the READ-ONLY API → structured claims
        │        → genai/verifier.py: a deterministic gate that REDACTS or
        │          REJECTS any number it cannot trace to a tool result
        ▼        a separate deployable; serve/ never imports it
```

**Precompute heavy, serve thin.** The runtime image contains no model library. The
ensemble is walked by ~90 lines of numpy, proven identical to sklearn on the entire test
set to within 1e-6.

The import wall (`CLAUDE.md` §2, enforced by `tests/test_serve_imports.py`) now also
permits `psycopg`, `redis`, `jose`, `prometheus_client`, `starlette` and `subprocess` —
I/O drivers, metrics bookkeeping and process management, never compute. ADR-033 and
ADR-036 justify each one; `pandas`, `sklearn`, `xgboost`, `shap` and `scipy` remain
banned outright, and the test fails the build if one appears.

---

## Honest limitations

- **PR-AUC 0.256 is low in absolute terms** and we do not dress it up. It is 53% of a
  measured ceiling of 0.481. The remaining gap is real headroom, not noise.
- **One plant.** Every figure is measured on plant A alone; nothing here shows how the
  models transfer to a second line (plant B was removed, ADR-068).
- **Feedback is durable only on Postgres** — which every deployment now is. Local dev on
  the SQLite build artifact keeps it in a tempdir file that does not survive a restart.
- **There is no authentication, only authorisation.** The finals upgrade added real RBAC
  (four roles, server-enforced) and an audit trail over every mutating action — but the
  role arrives in an `X-PulseTwin-Role` header that any caller can set, and CORS still
  allows all origins. That is a deliberate demo posture, not an oversight:
  `docs/SECURITY.md` states it plainly and gives the OIDC/JWT swap, which touches exactly
  one function body.
- **The live path still uses synthetic events.** The production-shaped stack and real
  MQTT wire path have been exercised end to end, but no customer PLC, MES, QMS, or
  historian is connected. The permanent source label makes that boundary visible.
- **Soft sensors work for some signals and not others, and we publish both.** Ambient-driven
  readings (paint temperature, humidity, film thickness) reconstruct at R² 0.97–1.00.
  Tool-wear-driven readings (torque, vibration) reconstruct at R² ≈ 0 or worse, because
  each station's wear is an independent random walk — a neighbour's torque correlates 0.06
  with the target's. The negative result is the more useful one: it tells you which sensor
  retrofits are unnecessary and which are unavoidable.
- **Three of four retrofit candidates score zero uplift**, and that is a finding rather
  than a gap. At S15 the operator checklist is already the second strongest driver in the
  whole model, so a temperature/humidity sensor there would buy information we get free.
- **The model cannot rank within its own alert set.** All 648 alerting units on plant A
  score `risk_raw` = 0.880117 to sixteen significant figures, so every alert renders the
  same 41% risk. All 32 trees split on `paint_buffer_dwell_s` at the root; once a unit is
  past the dwell threshold and under the checklist threshold, no later split
  discriminates. It identifies the at-risk population correctly and has nothing further
  to say inside it. Measured, reproduced with the numpy scorer, and left alone — spreading
  the scores by retraining would be tuning toward a nicer-looking number.
- **The agent's claims are gated, not trusted.** Anything `genai/verifier.py` cannot trace
  to a recorded tool result is removed from the answer, and a run with a contract
  violation is discarded entirely rather than shown with a warning badge.

---

## Documentation

**Start with `docs/STATE.md`** — it is the build ledger, the list of every bug found, and
the current "what to verify next."

| File | What it is |
|---|---|
| `docs/STATE.md` | Build ledger, all 24 bugs found, and the next actions |
| `docs/AUDIT.md` | 31 findings against the original brief (plus B34, added later), with the arithmetic |
| `docs/DECISIONS.md` | 36 ADRs — every deviation, with its reasoning |
| `docs/SPEC.md` | The corrected canonical spec that was actually built |
| `docs/BUSINESS_PROPOSAL.md` | Business case, personas, roadmap, risk register |
| `docs/ARCHITECTURE.md` | ISA-95 placement, read-only design, streaming, security posture |
| `docs/MODEL_CARD.md` | Metrics with intervals, soft-sensor contrast, failure modes |
| `docs/ASSUMPTIONS.md` | Every economic, physical and behavioural assumption |
| `docs/DEMO_SCRIPT.md` | The Round 2 5-minute arc, with the seeded hero unit |

Finals upgrade — production readiness:

| File | What it is |
|---|---|
| `docs/PRODUCTION_ARCHITECTURE.md` | The OT/IT diagram, one-way data flow, and what each property is enforced by |
| `docs/SECURITY.md` | RBAC table, audit trail, encryption, secrets, the OIDC upgrade path, compliance |
| `docs/MLOPS_LIFECYCLE.md` | Train offline → serve → read-only drift monitoring, with the real thresholds; why retraining was removed |
| `docs/DATA_GOVERNANCE.md` | Classification, retention, anonymisation, right-to-deletion |
| `docs/CHANGE_MANAGEMENT.md` | Stakeholders, comms, training, per-phase success criteria, rollback |
| `docs/SCALING_COST_MODEL.md` | 1 / 5 / 20 plants, priced off the *harder* plant's measured economics |
| `docs/INCIDENT_RUNBOOK.md` | Five concrete procedures: drift, dropout, DB failure, outage, false-alarm spike |
| `docs/FINALS_DEMO_SCRIPT.md` | The 7-minute finals arc, including live sensor-dropout injection |
| `docs/GRAND_FINALE_DEMO.md` | The Incident Commander demo: five live scenarios, approval to outcome |
| `docs/AGENT_ARCHITECTURE.md` | Commander state machine, planner, scoring rule, tracing (Mermaid) |
| `docs/AUTONOMY_AND_SAFETY.md` | Autonomy levels, governor, OT prohibition, prompt-injection posture |
| `docs/INCIDENT_LIFECYCLE.md` | Sense to learn, with the table that persists each stage |
| `docs/ENTERPRISE_INTEGRATION.md` | Adapter interface and where SAP PM / Maximo / MES / QMS / Teams connect |
| `docs/AI_EVALUATION.md` | Scenario results, defects found by running it, limitations |

Six of the audit findings were build-blockers: a label-noise rate 14× the stated figure,
a throughput target no station layout could reach, a demo unit whose alert cited features
from stations it had not yet visited, two thresholds presented as one, a tick loop that
could not meet its own runtime budget, and one model expected to score two different
feature spaces.
