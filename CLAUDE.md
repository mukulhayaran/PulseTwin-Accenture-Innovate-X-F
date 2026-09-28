# PulseTwin — Digital Twin for Vehicle Assembly Line

Hackathon Round 2 prototype. Mixed-model vehicle assembly line digital twin.

**STATUS: feature-complete, both rounds, plus a GenAI tier.** All ten phases of the
original brief are built — simulator, ML pipeline, artifacts, API, three persona views.
The finals upgrade (F1-F8) sits on top: PostgreSQL, MQTT ingestion, read-only model
monitoring (drift/registry/fallback — retraining was removed, ADR-053), RBAC + audit trail, graceful degradation, a fourth Ops
persona, eight production-readiness documents (the never-applied K8s/Terraform
manifests were removed, ADR-054). Since then: a
full frontend redesign (Tailwind v4 + Radix + Motion), `genai/` — an agentic triage tier
with a deterministic verification gate — and, on top of that, an **AI Operations Copilot**:
multi-turn threads, five named actions, eighteen tools, four new deterministic
decision-intelligence endpoints, and a `/copilot` route plus a dock on every persona view.

Read `docs/STATE.md` first — it has the ledger, every bug found, and the verify-next
list. **Grand-Finale engineering loop:** `LOOP.md` (process), `.loop/goal.json` (criteria A–W),
`docs/ENGINEERING_LOOP_STATUS.md` (ledger); run or resume it with the `pulsetwin-loop` skill.
Its checks: `scripts/verify.py [--full]`, `scripts/verify_demo_flow.py [--replay]`,
`scripts/verify_ui_flow.py` (need `PULSETWIN_DEMO_MODE=1` on the stack). `docs/AUDIT.md` covers why the build deviates from `pulsetwin_final_prompt.md`.

**Do not re-read the whole codebase to get oriented.** §4 below maps every subsystem to
its file and its governing doc. `docs/STATE.md`'s ledger is the change history.

**Production-only (ADR-052):** there is no SQLite demo runtime. Every deployment sets
`PULSETWIN_REQUIRE_POSTGRES=1`, so a missing `DATABASE_URL` fails at startup; SQLite
(`artifacts/pulsetwin.db`) is the build output and the test backend only. The full stack
(`make up`) ran end to end for the first time on 2026-09-25 (docs/STATE.md). The cloud
deploy is Neon + Render + Vercel — `docs/PRODUCTION_ARCHITECTURE.md` §DEPLOY. The agent path **has** now run against a live provider (OpenRouter,
2026-09-21): cold-cache start, soft-degrade to L4, six tool calls, 4/5 claims verified
with the fifth correctly redacted for a ratio-vs-percent mismatch — plus a 10/10 run on
`incident`. The four defects that first live run exposed are ADR-040. See §8 for the
standing gotchas that have each cost a debugging session.

**Before changing anything, know these three:**
1. **Do not tune the model to raise PR-AUC.** 0.256 is 53% of a *measured* oracle ceiling
   of 0.481. The brief's 0.75–0.88 band is arithmetically unreachable given its own
   requirement of unobservable causes (`docs/AUDIT.md` §B34). The oracle framing is the
   stronger claim. Tuning happens ONLY as a gated challenger
   (`offline/ml/xgb_challenger.py`, ADR-061): tuned on train/validate, scored once on test,
   promoted only if every gate passes. It was NOT promoted on either plant (plant A fails
   recall under its trailing-window alert policy, ADR-062).
   Plant A's test set has been looked at three times, every run is disclosed, and each
   further run is one more look, so do not re-run it to shop for a pass. Budget
   thresholds are chosen on VALIDATION (`train.py`, ADR-062), never on test.
2. **Negative results stay published.** Soft sensors reconstruct ambient-driven signals
   (R² ≈ 0.99) and not tool-wear-driven ones (R² ≈ 0). Three of four retrofit candidates
   score zero uplift. Both are findings, not gaps — see ADR-030, ADR-031.
3. **Bottleneck accuracy must never read 100%.** If it does, ground truth has collapsed
   back onto the detector. They use deliberately different windows (ADR-029).

---

## 1. NON-NEGOTIABLE RULES

### Git
- **NEVER `git push`.** Never create a remote, a GitHub repo, a PR, or a release.
  This is enforced by deny rules in `.claude/settings.json` — do not try to work around them.
- **NEVER add trailers to commit messages.** No `Co-Authored-By:`, no `Claude-Session:`,
  no `Generated with Claude Code`. Claude must not appear in the contributor list.
  (Enforced by `includeCoAuthoredBy: false` in user settings.)
- Commit message format is plain and nothing else:
  ```
  phase 3: featurize with temporal leakage guards
  ```
- Local commits are expected and encouraged. Publishing is not.
- Do **not** commit `artifacts/` on every phase — a ~50 MB SQLite blob per commit
  bloats history irreversibly. Commit artifacts **once**, at the end.

### Code
- **No placeholders.** No `# TODO`, no bare `pass` as a function body, no `...` as an
  implementation, no stub components. Every file runs. Every component renders real data.
- **Config-driven.** No hardcoded station IDs (10, 15, 22, 27...) anywhere outside
  `configs/*.yaml`. Adding another `configs/<plant>.yaml` must work end to end with no code edit. The
  project ships ONE plant (`plant_a`); plant B was removed (ADR-068).
- **Deterministic.** Seed 42 everywhere. Set `OMP_NUM_THREADS=1` before training or
  reproducibility claims are false.

---

## 2. THE IMPORT WALL

`serve/` may import **only**:

```
fastapi  uvicorn  pydantic  numpy  orjson
psycopg  psycopg_pool  redis  jose  prometheus_client  prometheus_fastapi_instrumentator
starlette  subprocess
sqlite3  json  math  pathlib  os  sys  time  asyncio  typing  datetime
contextlib  dataclasses  logging  re  collections  functools  itertools
hashlib  enum  csv  io  tempfile  threading  secrets  decimal
```

Nothing else. Never `pandas`, `sklearn`, `xgboost`, `shap`, `simpy`, `scipy`, `psutil`.
Enforced by `tests/test_serve_imports.py`, which checks **first-party imports only** —
it must not walk fastapi's transitive graph (starlette/anyio) or it fails instantly.

`psycopg`/`redis`/`jose`/`prometheus_*` are I/O drivers and bookkeeping, not compute — see
ADR-033 for why they don't violate the wall. `paho-mqtt` is used only by `ingestion/`,
a separate deployable that never imports into `serve/`. `starlette` is fastapi's own ASGI
middleware base (a direct import, not transitive graph-walking); `subprocess` launches the
GenAI triage run as a detached process instead of hosting `genai/` in `serve/` — see ADR-036.

Heavy libraries live in `offline/` and never ship in the runtime image.
**Precompute heavy, serve thin.**

**`genai/` is OUTSIDE the wall and the wall was not widened for it.** `genai` is simply
absent from `ALLOWED`, so `tests/test_serve_imports.py` already fails on any
`import genai` inside `serve/` — that test needed **no edit** to cover the GenAI tier,
which is a better outcome than an amendment. `serve/routers/triage.py` triggers a run
with `subprocess.Popen` (ADR-036). Never add `openai`,
`anthropic`, `groq`, `openrouter`, or any provider SDK to the allowlist (ADR-037). The
provider has now moved TWICE — to Groq in ADR-038, to OpenRouter in ADR-040 — and the
DEPENDENCY did not change either time, because all three speak the OpenAI wire protocol.
That is the whole reason this ban survives a provider swap.

Three constants in `serve/` MIRROR `genai/` by hand because of this wall, and a change to
one without the other is a bug: `API_KEY_ENV` (↔ `genai/config.py`), `INTENT_LABELS` /
`INTENT_KEYS` (↔ `genai/intents.py`), and `PHASE_ORDER` (↔ `genai/tools.py`). Each pair
has a test asserting they agree.

---

## 3. THE THREE CONTRACTS

These three propagate from simulator → DB → API → UI. Breaking one anywhere breaks it everywhere.

**Provenance.** Every value carries `provenance: "measured" | "inferred"`.
Inferred values additionally carry `ci_low` and `ci_high`. The UI renders them
differently (amber `INFERRED` badge + hatch pattern). Never silently mix the two.

**Sourced economics.** No dollar figure appears in any config, API response, doc, or UI
element without a `source` field pointing at `configs/economics.yaml` or a named
assumption in `docs/ASSUMPTIONS.md`.

**Operator privacy.** The authoritative definition is
`genai/verifier.py::_contains_operator_identity` — use it rather than writing a second,
looser string match. Never surface individual operator identity or performance —
not in an API response, not in a table, not in a tooltip. Aggregate to shift, team, or
opaque hashed bucket only. This includes `shared_factors` in the containment endpoint:
emit `"operator_group_B"`, never a real or stable operator ID.

---

## 4. WHERE THINGS ARE

| File | What it is |
|---|---|
| `docs/STATE.md` | **Start here.** Progress ledger, every bug found, next action. |
| `docs/SPEC.md` | The corrected canonical spec. This is the Round 2 build target. |
| `docs/DECISIONS.md` | 54 numbered ADRs — why we deviate from the original prompt. |
| `docs/AUDIT.md` | The 31-finding flaw analysis of the original prompt (plus B34). |
| `pulsetwin_final_prompt.md` | The original Round 2 brief. **Historical only — do not edit.** Note: it contains NO finals section and no mention of GenAI, despite older text claiming otherwise. |

### Subsystem map — read the file, not the whole repo

| Subsystem | Entry point | Governing doc |
|---|---|---|
| Simulator | `offline/simulator/generate.py` | `docs/SPEC.md` §Phase 2 |
| Features / model | `offline/ml/{featurize,train,thresholds}.py` | `docs/MODEL_CARD.md` |
| Artifact build | `offline/build_artifact.py` (16 tables) | ADR-027, ADR-032 |
| Runtime API | `serve/main.py` (+ 13 routers) — 91 endpoints | `docs/ARCHITECTURE.md` |
| Numpy scorer | `serve/scorer.py` — decision-path, NOT SHAP | ADR-015 |
| XGBoost challenger | `offline/ml/xgb_challenger.py` → `serve/xgb_scorer.py` (numpy walk of Booster JSON) · `serve/model_loader.py` picks the champion from `artifacts/model_selection_<plant>.json` · `serve/rolling_threshold.py` trailing-window alert budget · `offline/champion.py` applies a promotion to baked artifacts + live twin | **ADR-061, ADR-062**, `docs/XGB_CHALLENGER.md` |
| MQTT ingestion | `ingestion/` — separate deployable | `docs/PRODUCTION_ARCHITECTURE.md` |
| Model monitoring (read-only) | `mlops/{drift_detector,fallback}.py` — run offline; retraining removed (ADR-053) | `docs/MLOPS_LIFECYCLE.md` |
| **GenAI triage** | `genai/` — separate deployable, never imported by `serve/` | **ADR-037** |
| Multi-agent orchestration | `genai/orchestrator.py` — `incident` only, 3 specialists + synthesizer | **ADR-045** |
| Knowledge retrieval | `offline/build_knowledge_index.py` + `serve/knowledge_index.py` — lexical TF-IDF, no embeddings | **ADR-046** |
| Proactive watch | `serve/watch.py` (detectors; each firing opens an Incident Commander incident) · `offline/build_watch_rules.py` (YAML → `artifacts/watch_rules_<plant>.json`). The ADR-051 proposal inbox + watch intents were retired | **ADR-051**, **ADR-073** |
| Dynamic simulation experiments | `serve/experiment_catalog.py` (the ONLY allowed interventions + limits) · `serve/routers/experiments.py` (API, budgets, reaping) · `offline/sim_experiment.py` (detached worker: paired seeds on isolated twin copies) | **ADR-064** |
| Outcome learning | `serve/outcomes.py` (deterministic monitoring + classification, thresholds in `configs/alert_policies.yaml outcome_evaluation:`) · `serve/routers/learning.py` (outcomes, structured similar-incident search) | **ADR-064** |
| **Incident Commander** | `serve/commander.py` (state machine, planner, plan validation) · `serve/incident_evidence.py` (probes, baselines, genealogy trace) · `serve/hypotheses.py` · `serve/governor.py` · `serve/action_gateway.py` · `serve/case_memory.py` · `serve/routers/incidents.py` · `genai/commander.py` (optional LLM planner) · `configs/agentic.yaml` ↔ `serve/agentic_config.py` (pinned equal by a test) | **ADR-067**, `docs/AGENT_ARCHITECTURE.md` |
| Frontend | `frontend/app/{overview,supervisor,manager,leadership,ops,copilot}` + `components/ui/` · read-only incident UI in `components/incidents/` (/supervisor) · Vehicle Timeline in `components/vehicles/` · Decision Queue in `components/decisions/` (/manager; API `serve/routers/operations.py`) | §7 below, **ADR-047**, **ADR-069**, **ADR-071** |

**`genai/` file roles** (all provider-independent except `client.py`):
`config.py` model ID + caps · `client.py` the ONLY `openai` import · `api_client.py`
stdlib HTTP to PulseTwin's own API · `tools.py` the closed registry = the data-egress
allowlist · `loop.py` the agent loop · `orchestrator.py` Coordinator → Specialists → Simulation planner →
Synthesizer for `incident` (ADR-045, ADR-064) · `verifier.py` **the deterministic gate**, now also
checking `KnowledgeCitation` substrings (ADR-046) · `offline_triage.py` no-LLM path that
passes the same gate · `store.py` ops-DB writes · `triage.py` CLI entry.

Finals upgrade docs — read the relevant one before changing that subsystem:

| File | Governs |
|---|---|
| `docs/PRODUCTION_ARCHITECTURE.md` | OT/IT boundary, one-way flow, what runs where |
| `docs/SECURITY.md` | RBAC table, audit trail, OIDC upgrade path, compliance |
| `docs/MLOPS_LIFECYCLE.md` | Drift thresholds, promotion rule, rollback, retirement |
| `docs/DATA_GOVERNANCE.md` | Classification, retention, anonymisation |
| `docs/CHANGE_MANAGEMENT.md` | Rollout phases, training, per-phase exit criteria |
| `docs/SCALING_COST_MODEL.md` | 1/5/20-plant economics |
| `docs/INCIDENT_RUNBOOK.md` | What a human does when it breaks |
| `docs/FINALS_DEMO_SCRIPT.md` | The 7-minute finals demo |

`configs/deployment.yaml`, `configs/alert_policies.yaml` and `configs/data_governance.yaml`
are the **reviewable sources** their code counterparts implement as constants — `serve/`
cannot parse YAML (the import wall bans it), so when you change a threshold in one, change
it in both. `configs/alert_policies.yaml`'s escalation levels and
`serve/routers/alerts_mgmt.py`'s `ESCALATION_LEVELS` are the pair most likely to drift.

> **Where `pulsetwin_final_prompt.md` and `docs/SPEC.md` disagree, `docs/SPEC.md` wins.**
> The original contains contradictions that cannot be built as written. Every deviation
> is justified in `docs/DECISIONS.md`. If you think SPEC.md is wrong, add an ADR —
> do not silently revert to the original.

---

## 5. COMMANDS

```bash
make generate            # simulate the plant
make train               # train models (per-plant)
make build-artifact      # bake SQLite + model_<plant>.npz + stream JSONL
python -m offline.ml.xgb_challenger --config configs/plant_a.yaml [--smoke]  # ADR-061 gated challenger
make build-knowledge-index  # bake the engineering-knowledge TF-IDF index (ADR-046)
python -m offline.build_watch_rules  # bake configs/*.yaml `watch:` + stream baselines (ADR-051)
make test                # pytest tests/ -v   (611 tests — it runs real models; ~18 min
                          # was measured pre-ADR-045/046, not remeasured against the
                          # larger current count)

make dev-backend     # uvicorn on :7860
make dev-frontend    # next dev on :3000

# the stack -- Postgres only (ADR-052); the cloud deploy is docs/PRODUCTION_ARCHITECTURE.md §DEPLOY
make up              # docker compose -f docker-compose.prod.yml (postgres/redis/mqtt/ingestion/ui)
make up-mqtt         # ...plus the synthetic MQTT publisher
make down            # ...and tear it down, volumes included
make migrate         # apply migrations/*.sql to Postgres
make load-data       # bridge the committed artifact into Postgres
make drift-check     # python -m mlops.drift_detector (read-only monitoring, run offline)
make test-all        # pytest tests/ -v --tb=short

# genai tier (ADR-037/038/040/045/046) -- needs a running backend; a provider key is optional
pip install -r genai/requirements.txt
python -m genai.triage --plant plant_a --question "..." [--mode deterministic]
python -m genai.triage --plant plant_a --intent incident   # Coordinator -> Specialists -> Synthesizer
python -m genai.verify_cli --run-id X      # re-run the gate over a stored transcript
```

Without a provider key the triage falls back to `genai/offline_triage.py` — a real,
no-LLM triage that passes the **same** verifier. That is the default in dev and it is
how the whole feature is tested keyless.

The key goes in **`PULSETWIN_GENAI_API_KEY`**, never in a provider-named variable.
`genai/config.py::KEY_ENVS` takes the first name that is SET, and `GROQ_API_KEY` is
ahead of `OPENROUTER_API_KEY` in that list — so an environment holding both sends the
Groq key to OpenRouter and every run dies on a bare `provider_status_401` (ADR-040).
`.env.local` (gitignored) is loaded automatically by `make up` (compose `env_file`) and
`make dev-backend`; a bare `uvicorn` or `docker compose` run outside those does NOT load it.
The backend logs `genai: agent mode` or `genai: DETERMINISTIC mode` at startup — check it.

Always `OMP_NUM_THREADS=1` for anything touching training.

`tests/conftest.py` is the ONLY shared test fixture and does one thing: it resets the
rate limiter between tests. Every test file otherwise stays self-contained, by
convention. Do not move per-file setup into it — but do remember it exists, because
without it a long suite trips a production `role:ip` limit and tests that pass alone
fail together (`docs/STATE.md` bug 72).

The suite is slow because it trains and scores real models rather than mocking them —
budget ~16 minutes, and prefer running a single file while iterating. Do not "fix" a slow
test by mocking the model out; that is the coverage.

---

## 6. SKILLS TO ACTUALLY USE

Available and directly relevant — reach for these rather than improvising:

- **`design`** — multi-artboard canvas. Mock a persona view *before* writing React. On a
  tight clock this is the cheapest way to kill UI rework.
- **`dataviz`** — load it **before writing the first chart**. This project has ~12 charts;
  a coherent palette and correct chart-form choices carry the "control room" aesthetic.
- **`frontend-design`** (plugin) — visual direction, so the dashboards don't read as
  templated Tailwind.
- **`feature-dev`** (plugin) — structured explore → question → architect → review.
- **`playwright`** — **the highest-yield tool in this repo.** The MCP server times out
  here, but Python Playwright + Chromium are installed and work: drive the routes, capture
  console/network, click through flows. Every one of the last ~20 defects was found by
  RUNNING the thing, never by reading it. pytest cannot see a React component, and a
  passing unit suite has repeatedly coexisted with a broken seam.
- **`pyright-lsp`** / **`typescript-lsp`** (plugins) — live type errors. `npx tsc
  --noEmit -p .` in `frontend/` is the fast whole-project check and currently passes clean.

---

## 7. DESIGN LANGUAGE (frontend)

**Stack: Next.js 15 (static export) · React 19 · Tailwind v4 · Radix · Motion · Recharts.**
Tailwind v4 is **CSS-first** — there is no `tailwind.config.ts`; all tokens live in the
`@theme` block of `frontend/app/globals.css`, mirrored in `frontend/lib/theme.ts` for
Recharts (which takes hex, not class names). Change one, change both.

**BMW corporate-automotive, light (ADR-047).** Dense and information-rich, but light:
white canvas, BMW blue as the *only* action colour, **0px corners everywhere**, **no drop
shadow on a card** (depth is colour-block contrast plus a hairline), and a **700/300
weight pair with 500 absent from the system**. Monospace for all numbers. This replaced a
dark control-room palette — if you find a doc still describing `#0a0c11`, that doc is
stale, not the code.

```
--color-canvas     #ffffff   --color-ink        #262626   --color-ok      #16a34a  measured/healthy
--color-surface    #ffffff   --color-ink-dim    #4a4a4a   --color-danger  #dc2626  alert/defect
--color-surface-2  #fafafa   --color-ink-faint  #757575   --color-warn    #d97706  inferred/warning
--color-surface-3  #f0f0f0   (AA-pinned at 4.6:1 -- it   --color-info    #1c69d4  info/selection
--color-line       #e6e6e6    carries every axis label)  --color-ai      #7c3aed  prediction/AI
--color-primary    #1c69d4  BMW blue -- `info` is the SAME hex on purpose: the corporate
                            system has exactly one blue, so there is no second action colour.
```
`surface` deliberately **equals** `canvas` — the hairline is what makes a panel a panel.
Each signal has a **darkened** `-fg` variant for text on white (`--color-ok-fg` etc.).
Note this INVERTED with the palette: `-fg` used to mean "brightened for black".

**Light theme only — there is no dark surface (ADR-074).** The old navy band (`#1a2129`)
made selected text invisible and read as a second theme, so it was removed. The token
NAMES survive so no call site changed, but `--color-surface-dark` is now a light
cool blue-grey tint (`#f3f6fb`), `text-on-dark`/`-soft` are ink/ink-dim, and the
`-on-dark` signal set equals the `-fg` values. `band-dark` is a light blue-grey gradient
with hairlines. It still carries the sidebar rail, the landing principles and CTA bands,
the tooltip and the chart tip. Consequence: **white text on a blue or ink fill must use
`text-on-primary` / `text-canvas`, never `text-on-dark`**, which is now ink.

**`label-upper`** is the "LEARN MORE ›" inline link (13px / 700 / 0.115em); `Button`'s
`link` variant is its button form. **`m-stripe`** is the M tricolor — divider and badge
role only, never a CTA fill.

**Use `components/ui/` — do not hand-roll.** `Panel` was once reimplemented 8 times.
Available: `Panel Badge Button Select Slider SegmentedControl Tooltip DataTable
AnimatedNumber AppShell Kpi/Stat/Fact Skeleton ChartFrame/ChartTooltip/ChartLegend EmptyState`
plus the shadcn primitives reworked onto the kit (ADR-075): `Sheet Dialog Popover
DropdownMenu Tabs`. `Tabs` switches panels; `SegmentedControl` filters a value.
Also `components/ui/icons.tsx` (inline stroke icons — do NOT add lucide for twelve paths).
`components.json` exists for `npx shadcn add`, but the CLI misreads the `@/lib/cn` alias,
imports `cn` from an npm package called `cn` (and installs it), pulls the umbrella
`radix-ui` package and lucide. Treat its output as a draft to rewrite, never a finished
file. Magic UI pieces live in `components/magicui/`, adapted to the tokens, with a
reduced-motion path each.
Type sizes come from the scale (`text-2xs`…`text-4xl`), never `text-[11px]`. Radius comes
from `rounded-none` or `rounded-full` and nothing between; the four `--radius-*` steps all
resolve to 0 and exist only so no call site had to be edited.

**`cn()` from `lib/cn.ts`, not template strings.** It registers `text-numeric` as its own
tailwind-merge class group — without that, tailwind-merge sees the `text-` prefix and
silently strips it whenever another `text-*` follows, which once cost every figure in the
app its monospace face.

No `localStorage` / `sessionStorage` in React components — persistent UI state
(alert budget, shift counters) is derived server-side from the replay clock. **One
exception, ADR-048:** the login session is persisted in `sessionStorage` by
`frontend/lib/api.ts` — never by a component. A sign-in is not derived state, and since
this ships as a static export, typing `/manager` into the address bar is a reload, so
without it every hard navigation would sign the user out. What is stored is a
server-issued token the server re-validates (`restoreSession`), not a role string.

**Auth is two things that are easy to confuse (ADR-048).**
`session` = who the server says you are (`getSession()`, null when anonymous, established
only by `/api/auth/login`). `viewRole` = which persona the mounted page speaks as
(`setRole()`, and it is **clamped** to the session — a page can drop below its role, never
climb above it). `getRole()` returns the second falling back to the first, then to
`supervisor`. Change `setRole` and you are changing a privilege boundary, not a label.

**Who may open which view lives in ONE table:** `frontend/lib/routeAccess.ts`. It is an
**exact match, not the role hierarchy** — `manager` and `leadership` are peers who do not
see each other's view, and `system` is refused on both despite outranking them. The
hierarchy in `serve/middleware/auth.py` still governs what the API *allows*; these are
different questions. Both the route guard (`usePersonaAccess`) and `AppShell`'s rail read
that table, so a lock icon can never sit on a link that still opens.

**The views are not symmetric.** `/manager`, `/leadership` and `/ops` each require exactly
their own role and show that persona's job. `/ops` additionally carries a live "Viewing
as" selector so a demo can drop the role and watch `RoleGate` close panels in real time —
put new role-gated UI there, not in the fixed views, where a gate can never actually fire.
**`/supervisor` and `/overview` are the exceptions and they are PUBLIC:** the floor view is
where an unauthenticated visitor lands, and `/overview` pins no persona and degrades panel
by panel — it is the page you arrive on before choosing, so gating its entry would defeat
it. Signed out, both act as `supervisor`, matching the API's own default for a caller with
no credential.

**`AppShell` owns the chrome: a dark rail + a light top bar.** Its prop contract
(`persona plant onPlantChange role connected lastKnownAt onRetry controls status`) is
unchanged from the pre-ADR-047 header, which is why all four persona pages mount it
unedited — rebuild its internals, don't fork it. Anything `position: fixed` must clear the
15rem rail (`lg:left-[16.25rem]`), or it lands on top of the navigation.

---

## 8. GOTCHAS THAT HAVE EACH COST A SESSION

Read this before debugging anything. Every line below was paid for once.

### Running the stack
- **`next build` wipes `.next` out from under a running `next dev`.** The dev server then
  404s on its own chunks and every page renders blank with hydration errors. Symptom looks
  like your code broke; it didn't. Kill dev, `rm -rf .next`, restart.
- **The frontend does NOT primarily use `NEXT_PUBLIC_API_BASE`.** `lib/api.ts` fetches
  `/config.json` first (ADR-028 runtime config) and only falls back to the env var. To
  point a browser at a different backend, intercept `**/config.json` in Playwright rather
  than editing the committed file.
- **Port 7860 is often held by a stale process** that `Get-Process` cannot see. Find it
  with `netstat -ano | grep 7860`; if the PID is invisible, use a different port.
- **`make` is not installed.** Use the raw commands in §5.

### Determinism and CPU (ADR-065)
- **Any sort whose ORDER is used must be `kind="stable"`.** numpy's default argsort orders
  ties differently across versions and CPUs; that alone made the simulator stop
  reproducing its own shipped data.
- **Never `statistics.stdev`/`mean` in a per-event path.** It is exact-rational and was the
  largest cost of a twin replica. `feature_contract.py` uses `math.fsum`.
- **The warmed twin is cached** (`offline/live_twin/warm_cache.py`, keyed by input hashes).
  Copy a twin with `pickle.loads(blob)`, not `copy.deepcopy`, which is 6× slower.

### SQLite (`serve/db.py`)
- **Two connections to one SQLite file INSIDE one process deadlock each other** exactly as
  two processes do. `db.execute` commits *after* the statement, so a statement that raises
  (e.g. a duplicate INSERT) leaves a write transaction open and blocks every later write.
- The ops DB is genuinely **multi-process** — the backend holds a long-lived handle while
  `genai.triage` is spawned detached. It is opened WAL +
  `busy_timeout`; do not remove those.
- **A detached child's failure is invisible** if its stdio goes to `DEVNULL` — the row just
  sits at `queued` forever. `serve/routers/triage.py` writes a per-run `.log` beside the
  heartbeat. Do the same for any new spawned job.

### Artifacts and the stream
- **`stream_index.byte_offset` is unreliable on Windows-built artifacts.** `build_artifact`
  counted a 1-byte `"\n"` while text mode wrote `"\r\n"`, so offsets drift by one byte per
  preceding line. Fixed at the source (`newline=""`) and probed around in `serve/main.py`,
  but the **committed artifact still has drifted offsets** until it is rebuilt.
- **Never `seek()` a text-mode file to a byte offset.** Text handles only accept values
  from their own `tell()`. This is what broke `?from_ts=` for the whole life of the repo.

### Frontend traps
- **An UNLAYERED CSS rule beats every Tailwind utility, at any specificity.** Tailwind v4
  emits utilities inside `@layer utilities`, and unlayered always wins over layered. A bare
  `h2 { color: var(--color-ink) }` in `globals.css` therefore overrode `text-on-dark` and
  rendered every heading in every dark band invisible, and `.text-2xs { font-weight: 400 }`
  overrode `font-bold` on every badge, panel title and table header. Everything in the base
  block must stay inside `@layer base` (ADR-047).
- **Don't verify CSS by grepping the stylesheet** — Next's dev server injects CSS through
  JS, so a response-body grep captures nothing and reports every class as missing. Probe
  `getComputedStyle` on a throwaway element in the page instead.
- **Don't test the static export with `python -m http.server`.** It forces
  `/overview.html` into the address bar, so `usePathname()` returns `"/overview.html"`
  while the build prerendered `"/overview"` — the active nav item differs and React throws
  hydration error #418 on every page whose shell prerenders. Production serves clean URLs
  (`serve out`, see `frontend/Dockerfile`) and never sees it. Serve `out/` behind a
  clean-URL shim, or the bug you chase will be in your harness.
- `fallback ?? default` does **not** short-circuit on `null` — an explicit
  `fallback={null}` still renders the default. `RoleGate` checks `!== undefined`.
- **Radix `Select` renders its placeholder for `value=""`**, so an "All" option with an
  empty value shows a blank trigger. Use a sentinel and map it back to `undefined`.
- `motion`'s `layout` prop measures every element each render — never put it on a list
  that re-renders on the SSE flush.
- The supervisor header must fit **one line**; it wraps when the webfont swaps in and
  drops `MAIN` 32px (load CLS 0.145 → 0.043 when fixed).

### Testing
- **`networkidle` never fires on `/supervisor`** — the SSE stream holds the connection
  open by design. Use `domcontentloaded` + an explicit wait.
- pytest's default `test*` glob **collects imported helpers** whose names start with
  `test` (e.g. `testclient_transport`). Alias them on import.
- Playwright `get_by_role(name=...)` is a substring match: "Start simulation" also matches
  "Restart simulation". Use `exact=True`.

### The GenAI provider seam (ADR-040 — all measured against OpenRouter on 2026-09-21)
- **An OpenAI-compatible endpoint can reject a parameter with HTTP 200.** OpenRouter
  accepts `response_format` alongside `tools` and the model then makes **zero** tool
  calls. `capabilities.is_capability_rejection` only sees 400/422, so nothing degraded
  and a cold run died on L0 with an unparseable finding. `suppresses_tool_calls` is the
  soft probe that catches it — and it needs BOTH halves of its signature, because "no
  tool call on turn 0" alone is a legitimate finish that two existing tests pin.
- **The output schema reaches the model through the PROMPT, not just `response_format`.**
  At L4 no schema is sent at all, and L4 is where the ladder lands on both providers.
  A model that never saw the schema invents one (`citations` instead of `claims`) and a
  perfectly good six-tool investigation is thrown away by the parser. Change
  `prompts.RESPONSE_FORMAT`, change the OUTPUT SHAPE block in `prompts.SYSTEM` too.
- **`store.py`'s rate card is pinned to a model id that is an environment variable.**
  Left alone it bills a free model at another provider's prices with a `cost_source` that
  names them — contract #2 broken by the tier that enforces contract #2. `_cost()` now
  prefers the provider's `usage.cost`; OpenRouter reports it, Groq does not.
- **`:free` means 50 requests per DAY across all free models**, and one agent run spends
  7-12. Four or five conversations exhaust the account and every later run fails
  `rate_limited`. Check it with `curl -H "Authorization: Bearer $PULSETWIN_GENAI_API_KEY"
  https://openrouter.ai/api/v1/key` — the answer is in `free_model_daily_requests`.
- **The SDK's 90 s timeout is per READ, not per call.** OpenRouter keeps a queued or
  slow request alive with whitespace, so a worker sat 14+ minutes in one call and the run's
  300 s cap (checked only between calls) never fired. `loop._create_within` now bounds
  each call by the run's remaining budget (ADR-063 §13).
- **A toolless VALID-JSON first answer is also the suppression signature.** At a rung
  sending `response_format`, Nemotron answered with well-formed JSON, no tool call, and
  12 claims citing a `call_0001` that never existed. It now degrades like broken JSON did.
- **Nemotron is a reasoning model**: a trivial prompt spent 11,555 reasoning tokens
  against 341 of visible output. Budget latency accordingly (a 6-tool run is ~70s), and
  do not read a large `completion_tokens` as a large answer.

### Known model behaviour that looks like a bug but isn't
- **Every alert renders the same risk (41% on plant A).** All 648 alerting units share
  `risk_raw` = 0.880117 exactly. All 32 trees split on `paint_buffer_dwell_s` at the root;
  once a unit is past the dwell threshold and under the checklist threshold, no later
  split discriminates, so every such unit lands in the same leaf in every tree. The model
  identifies the at-risk set but **cannot rank within it**. This is measured, reproduced
  with the numpy scorer, and not a rendering fault. Do NOT retrain to spread the scores —
  that is tuning toward a nicer number, which §1 forbids. If it needs addressing, change
  the presentation (band + CI) and record it in `docs/MODEL_CARD.md`.
- **Bottleneck accuracy must never read 100%** (ADR-029). `/api/stations/stats`'s
  inferred rows carry `<field>_ci_low`/`_ci_high` (ADR-060) — before that, a live agent run
  citing an unsensored station was rejected `missing_interval`. The verifier checks an
  interval is PRESENT, not that it matches the source.
