# PulseTwin — Digital Twin for Vehicle Assembly Line

Hackathon Round 2 prototype. Mixed-model vehicle assembly line digital twin.

**STATUS: feature-complete, both rounds, plus a GenAI tier.** All ten phases of the
original brief are built — simulator, ML pipeline, artifacts, API, three persona views.
The finals upgrade (F1-F8) sits on top: PostgreSQL, MQTT ingestion, MLOps
(drift/retrain/registry/fallback), RBAC + audit trail, graceful degradation, a fourth Ops
persona, K8s/Terraform manifests, eight production-readiness documents. Since then: a
full frontend redesign (Tailwind v4 + Radix + Motion), `genai/` — an agentic triage tier
with a deterministic verification gate — and, on top of that, an **AI Operations Copilot**:
multi-turn threads, five named actions, eighteen tools, four new deterministic
decision-intelligence endpoints, and a `/copilot` route plus a dock on every persona view.

Read `docs/STATE.md` first — it has the ledger, every bug found, and the verify-next
list. `docs/AUDIT.md` covers why the build deviates from `pulsetwin_final_prompt.md`.

**Do not re-read the whole codebase to get oriented.** §4 below maps every subsystem to
its file and its governing doc. `docs/STATE.md`'s ledger is the change history.

**Still unverified:** `docker compose -f docker-compose.prod.yml up` has never been run
end to end. The agent path **has** now run against a live provider (OpenRouter,
2026-09-21): cold-cache start, soft-degrade to L4, six tool calls, 4/5 claims verified
with the fifth correctly redacted for a ratio-vs-percent mismatch — plus a 10/10 run on
`incident`. The four defects that first live run exposed are ADR-040. See §8 for the
standing gotchas that have each cost a debugging session.

**Before changing anything, know these three:**
1. **Do not tune the model to raise PR-AUC.** 0.256 is 53% of a *measured* oracle ceiling
   of 0.481. The brief's 0.75–0.88 band is arithmetically unreachable given its own
   requirement of unobservable causes (`docs/AUDIT.md` §B34). The oracle framing is the
   stronger claim.
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
  `configs/*.yaml`. Swapping `plant_a.yaml` → `plant_b.yaml` must work end to end.
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
retrain job as a detached process instead of hosting `mlops/` in `serve/` — see ADR-036.

Heavy libraries live in `offline/` and never ship in the runtime image.
**Precompute heavy, serve thin.**

**`genai/` is OUTSIDE the wall and the wall was not widened for it.** `genai` is simply
absent from `ALLOWED`, so `tests/test_serve_imports.py` already fails on any
`import genai` inside `serve/` — that test needed **no edit** to cover the GenAI tier,
which is a better outcome than an amendment. `serve/routers/triage.py` triggers a run
with `subprocess.Popen` exactly as `admin.py` triggers retraining. Never add `openai`,
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
| `docs/DECISIONS.md` | 37 numbered ADRs — why we deviate from the original prompt. |
| `docs/AUDIT.md` | The 31-finding flaw analysis of the original prompt (plus B34). |
| `pulsetwin_final_prompt.md` | The original Round 2 brief. **Historical only — do not edit.** Note: it contains NO finals section and no mention of GenAI, despite older text claiming otherwise. |

### Subsystem map — read the file, not the whole repo

| Subsystem | Entry point | Governing doc |
|---|---|---|
| Simulator | `offline/simulator/generate.py` | `docs/SPEC.md` §Phase 2 |
| Features / model | `offline/ml/{featurize,train,thresholds}.py` | `docs/MODEL_CARD.md` |
| Artifact build | `offline/build_artifact.py` (16 tables) | ADR-027, ADR-032 |
| Runtime API | `serve/main.py` (+ 6 routers) — 35 endpoints | `docs/ARCHITECTURE.md` |
| Numpy scorer | `serve/scorer.py` — decision-path, NOT SHAP | ADR-015 |
| MQTT ingestion | `ingestion/` — separate deployable | `docs/PRODUCTION_ARCHITECTURE.md` |
| MLOps | `mlops/{drift_detector,retrainer,model_registry,evaluation,fallback}.py` | `docs/MLOPS_LIFECYCLE.md` |
| **GenAI triage** | `genai/` — separate deployable, never imported by `serve/` | **ADR-037** |
| Frontend | `frontend/app/{supervisor,manager,leadership,ops}` + `components/ui/` | §7 below |

**`genai/` file roles** (all provider-independent except `client.py`):
`config.py` model ID + caps · `client.py` the ONLY `openai` import · `api_client.py`
stdlib HTTP to PulseTwin's own API · `tools.py` the closed registry = the data-egress
allowlist · `loop.py` the agent loop · `verifier.py` **the deterministic gate** ·
`offline_triage.py` no-LLM path that passes the same gate · `store.py` ops-DB writes ·
`triage.py` CLI entry.

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
make generate        # simulate both plants
make train           # train models (per-plant)
make build-artifact  # bake SQLite + model_<plant>.npz + stream JSONL
make test            # pytest tests/ -v   (339 tests, ~18 min — it runs real models)
make demo            # docker compose: backend + frontend (SQLite runtime)

make dev-backend     # uvicorn on :7860
make dev-frontend    # next dev on :3000

# finals upgrade
make demo-prod       # docker compose -f docker-compose.prod.yml (postgres/redis/mqtt/ingestion)
make demo-prod-down  # ...and tear it down, volumes included
make migrate         # apply migrations/*.sql to Postgres
make load-data       # bridge the committed artifact into Postgres
make drift-check     # python -m mlops.drift_detector
make retrain         # python -m mlops.retrainer (champion-challenger)
make test-all        # pytest tests/ -v --tb=short

# genai tier (ADR-037/038/040) -- needs a running backend; a provider key is optional
pip install -r genai/requirements.txt
python -m genai.triage --plant plant_a --question "..." [--mode deterministic]
python -m genai.verify_cli --run-id X      # re-run the gate over a stored transcript
```

Without a provider key the triage falls back to `genai/offline_triage.py` — a real,
no-LLM triage that passes the **same** verifier. That is the default in dev and it is
how the whole feature is tested keyless.

The key goes in **`PULSETWIN_GENAI_API_KEY`**, never in a provider-named variable.
`genai/config.py::KEY_ENVS` takes the first name that is SET, and `GROQ_API_KEY` is
ahead of `OPENROUTER_API_KEY` in that list — so an environment holding both sends the
Groq key to OpenRouter and every run dies on a bare `provider_status_401` (ADR-040).
Nothing in this repo auto-loads `.env.local`; export it, or the defaults in
`genai/config.py` are exactly what runs.

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

Dense, information-rich, dark. Linear/Vercel-dashboard polish, not a marketing page.
Monospace for all numbers.

```
--color-canvas     #0a0c11   --color-ink        #e6e9ef   --color-ok      #10b981  measured/healthy
--color-surface    #111419   --color-ink-dim    #a0a8b4   --color-danger  #ef4444  alert/defect
--color-surface-2  #171b22   --color-ink-faint  #88919f   --color-warn    #f59e0b  inferred/warning
--color-surface-3  #1e232c   (ink-faint is WCAG-AA      --color-info    #3b82f6  info/selection
--color-line       #232834    tuned -- #6b7482 failed)   --color-ai      #8b5cf6  prediction/AI
```
Each signal also has a brightened `-fg` variant for text on dark (`--color-ok-fg` etc.).

**Use `components/ui/` — do not hand-roll.** `Panel` was once reimplemented 8 times.
Available: `Panel Badge Button Select Slider SegmentedControl Tooltip DataTable
AnimatedNumber AppShell Kpi/Stat/Fact Skeleton ChartFrame/ChartTooltip/ChartLegend`.
Type sizes come from the scale (`text-2xs`…`text-3xl`), never `text-[11px]`.

**`cn()` from `lib/cn.ts`, not template strings.** It registers `text-numeric` as its own
tailwind-merge class group — without that, tailwind-merge sees the `text-` prefix and
silently strips it whenever another `text-*` follows, which once cost every figure in the
app its monospace face.

No `localStorage` / `sessionStorage` in React components — persistent UI state
(alert budget, shift counters) is derived server-side from the replay clock. The RBAC role
follows the same rule: it lives in a module variable in `frontend/lib/api.ts` and resets
on reload, which is why each persona page calls `setRole()` for itself on mount.

**Four views, and they are not symmetric.** `/supervisor`, `/manager` and `/leadership`
each pin themselves to one role and show that persona's job. `/ops` is the exception: it
carries a live "Viewing as" selector so a demo can drop the role and watch `RoleGate`
close panels in real time. Put new role-gated UI there, not in the three fixed views,
where a gate can never actually fire.

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

### SQLite (`serve/db.py`)
- **Two connections to one SQLite file INSIDE one process deadlock each other** exactly as
  two processes do. `db.execute` commits *after* the statement, so a statement that raises
  (e.g. a duplicate INSERT) leaves a write transaction open and blocks every later write.
- The ops DB is genuinely **multi-process** — the backend holds a long-lived handle while
  `mlops.retrainer` and `genai.triage` are spawned detached. It is opened WAL +
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
- **Bottleneck accuracy must never read 100%** (ADR-029), and **`/api/stations/stats`
  currently ships `provenance: "inferred"` rows with no `ci_low`/`ci_high`**, which
  violates contract #1 — `genai/offline_triage.py` declines to make point claims about
  them rather than papering over it.
