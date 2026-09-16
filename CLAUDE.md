# PulseTwin — Digital Twin for Vehicle Assembly Line

Hackathon Round 2 prototype. Mixed-model vehicle assembly line digital twin.

**STATUS: feature-complete, both rounds.** All ten phases of the original brief are built
— simulator, ML pipeline, artifacts, API, three persona views. The finals upgrade (F1-F8)
is built on top: PostgreSQL, MQTT ingestion, MLOps (drift/retrain/registry/fallback),
RBAC + audit trail, graceful degradation, a fourth Ops persona, K8s/Terraform manifests,
and eight production-readiness documents. 226 tests pass.

Read `docs/STATE.md` first — it has the ledger, all 24 bugs found, and the current
verify-next list. `docs/AUDIT.md` covers why the build deviates from
`pulsetwin_final_prompt.md`.

**The one thing still unverified:** `docker compose -f docker-compose.prod.yml up` has
never been run end to end (no Docker daemon was available). Everything else in this
repo has either a passing test or a hand-review behind it.

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

---

## 3. THE THREE CONTRACTS

These three propagate from simulator → DB → API → UI. Breaking one anywhere breaks it everywhere.

**Provenance.** Every value carries `provenance: "measured" | "inferred"`.
Inferred values additionally carry `ci_low` and `ci_high`. The UI renders them
differently (amber `INFERRED` badge + hatch pattern). Never silently mix the two.

**Sourced economics.** No dollar figure appears in any config, API response, doc, or UI
element without a `source` field pointing at `configs/economics.yaml` or a named
assumption in `docs/ASSUMPTIONS.md`.

**Operator privacy.** Never surface individual operator identity or performance —
not in an API response, not in a table, not in a tooltip. Aggregate to shift, team, or
opaque hashed bucket only. This includes `shared_factors` in the containment endpoint:
emit `"operator_group_B"`, never a real or stable operator ID.

---

## 4. WHERE THINGS ARE

| File | What it is |
|---|---|
| `docs/STATE.md` | **Start here.** Progress ledger, every bug found, next action. |
| `docs/SPEC.md` | The corrected canonical spec. This is the Round 2 build target. |
| `docs/DECISIONS.md` | 36 numbered ADRs — why we deviate from the original prompt. |
| `docs/AUDIT.md` | The 31-finding flaw analysis of the original prompt (plus B34). |
| `pulsetwin_final_prompt.md` | The original brief, and the finals brief. **Historical only — do not edit.** |

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
make test            # pytest tests/ -v   (226 tests, ~16 min — it runs real models)
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
```

Always `OMP_NUM_THREADS=1` for anything touching training.

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
- **`playwright`** (plugin) — drive a real browser over the four views, screenshot them,
  and prove the SSE stream actually renders. **This is still outstanding** — pytest cannot
  see a React component, and no view has been confirmed rendering since the Ops view
  landed. "The UI works" is otherwise unverified.
- **`pyright-lsp`** / **`typescript-lsp`** (plugins) — live type errors. `npx tsc
  --noEmit -p .` in `frontend/` is the fast whole-project check and currently passes clean.

---

## 7. DESIGN LANGUAGE (frontend)

Dark control-room aesthetic. Dense and information-rich — Bloomberg Terminal meets
factory floor, **not** a SaaS landing page. Monospace for all numbers.

```
--bg-primary   #0a0e17    --text-primary  #e5e7eb    --accent-green   #10b981  measured/healthy
--bg-card      #111827    --text-muted    #6b7280    --accent-red     #ef4444  alert/defect
--bg-surface   #1f2937                               --accent-amber   #f59e0b  warning/inferred
                                                     --accent-blue    #3b82f6  info/selection
                                                     --accent-purple  #8b5cf6  prediction/AI
```

No `localStorage` / `sessionStorage` in React components — persistent UI state
(alert budget, shift counters) is derived server-side from the replay clock. The RBAC role
follows the same rule: it lives in a module variable in `frontend/lib/api.ts` and resets
on reload, which is why each persona page calls `setRole()` for itself on mount.

**Four views, and they are not symmetric.** `/supervisor`, `/manager` and `/leadership`
each pin themselves to one role and show that persona's job. `/ops` is the exception: it
carries a live "Viewing as" selector so a demo can drop the role and watch `RoleGate`
close panels in real time. Put new role-gated UI there, not in the three fixed views,
where a gate can never actually fire.
