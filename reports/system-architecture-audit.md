# System Architecture Audit

Production-readiness pass, 2026-09-21. Scope note up front: this document maps the system
as it actually behaves, cross-checked against `docs/ARCHITECTURE.md`,
`docs/PRODUCTION_ARCHITECTURE.md` and `docs/MLOPS_LIFECYCLE.md` — not a re-derivation from
zero. Those documents are already unusually rigorous (they cite the exact file and line for
nearly every claim), so this audit's job was to verify the claims still hold against current
code, not to write the map from scratch. Deltas from the docs are called out explicitly;
everything else should be read as "confirmed, not merely repeated."

## ASCII data-flow diagram

```
configs/*.yaml (plant_a, plant_b, economics)
        |
        v
offline/simulator/  (event-driven DES: line_model, physics, operators, causal_engine,
        |            inspection) --seed 42, OMP_NUM_THREADS=1--> telemetry/genealogy/
        |            checklists/inspections (data/<plant>/*.parquet)
        v
offline/ml/  featurize -> train (HistGB+Platt) -> thresholds -> soft_sensor
        |    253/320 features, temporal split (train/val/test = 14/7/7 days)
        v
offline/build_artifact.py
        |
        +--> artifacts/pulsetwin.db      (SQLite, 16 tables, opened read-only at runtime)
        +--> artifacts/model_<plant>.npz (flattened HistGB ensemble, numpy only)
        +--> artifacts/stream_<plant>.jsonl + stream_index (replay feed for /api/stream)
        |
        v
========================== THE IMPORT WALL (CLAUDE.md §2) ==========================
serve/  fastapi+uvicorn+pydantic+numpy+orjson+psycopg+redis+jose+prometheus_*+stdlib
   |
   +-- serve/db.py --------- dual dispatch: SQLite (file:...?mode=ro) | Postgres
   |                         (serve/db_postgres.py), normalises rows to one shape
   +-- serve/scorer.py ----- ~90 lines, walks the flattened ensemble, decision-path
   |                         explanations (Saabas, NOT SHAP -- ADR-015)
   +-- serve/main.py ------- lifespan: loads plant_meta, resolves champion scorer
   |                         PER PLANT from model_registry (ADR-041, fixed this pass;
   |                         previously always loaded the committed .npz, ignoring the
   |                         registry entirely -- see Phase 3 findings below)
   +-- serve/routers/*.py -- 6 routers, ~35 endpoints (admin, alerts_mgmt, audit,
   |                         intelligence, model_health, system, triage)
   +-- serve/middleware/---- auth (role hierarchy via X-PulseTwin-Role, NOT authenticated),
   |                         audit (INSERT-only audit_log), rate_limit (in-memory,
   |                         per-role:ip sliding window, single-process only)
   +-- SSE /api/stream ----- async, downsampled >25x, X-Accel-Buffering:no

serve/ NEVER imports: mlops/, genai/, ingestion/, pandas, sklearn, shap, psutil.
Enforced by tests/test_serve_imports.py (AST-parsed, first-party imports only).

================================ OT/IT boundary ====================================
OT: PLCs -> OPC-UA -> edge gateway -> MQTT broker (Unified Namespace,
    pulsetwin/{plant}/{zone}/{station}/{signal})
        | one-way, OT -> IT only (network-policy.yaml enforces this at the CNI level,
        v  not just in application code)
IT: ingestion/mqtt_subscriber.py  -- THE ONLY POD/PROCESS ALLOWED TO REACH MQTT
        |   dedup + late-arrival reorder (event-time watermark) + range validation
        |   (ingestion/data_quality.py) + per-station completeness/timeliness scoring
        v
    PostgreSQL (migrations/001-006, partitioned by plant_id on genealogy/station_readings)
        ^                                                    |
        |                                                    v
    serve/ (reads same DB)  <---------------------------  Redis (feature buffer,
        |                                                  ADR-033 degraded-read cache)
        v
    Frontend (Next 15 static export) -- 6 routes, SSE-driven line map + copilot dock

================================ MLOps lifecycle ===================================
mlops/ (pandas/sklearn side of the wall, never imported by serve/)
  drift_detector.py (PSI/calibration/coverage, writes drift_metrics)
  retrainer.py (champion-challenger, spawned by serve/ as a DETACHED SUBPROCESS via
                subprocess.Popen -- "trigger heavy, don't host it", ADR-036)
  model_registry.py (register/promote/rollback/get_champion -- serve/routers/admin.py
                      duplicates this in plain SQL rather than importing it)
  evaluation.py (promotion rule: PR-AUC strictly better AND precision>=AND calibration<=,
                 all three, never a weighted average)

================================== GenAI tier ======================================
genai/  -- OUTSIDE the import wall entirely (not merely widened for it -- absent from
           ALLOWED, so any `import genai` inside serve/ already fails the wall test)
  tools.py (18-entry closed registry = the data-egress allowlist)
  loop.py (agent loop, 5-rung capability-degradation ladder, OpenAI-wire-protocol client)
  verifier.py (deterministic gate: 4 checks, C1 resolvable / C2 faithful / C3 exhaustive
               / C4 contracts -- no model, no network, no clock; genai/verify_cli.py
               re-runs it over stored bytes for a byte-identical verdict)
  offline_triage.py (no-LLM path, same tools, same verifier -- the keyless default)
serve/routers/triage.py spawns `python -m genai.triage` as a detached subprocess,
exactly as admin.py spawns mlops.retrainer -- same pattern, same reason.
```

## Component-by-component: responsibility / state / failure modes / tests

| Component | Responsibility | Persistent state | Failure mode (observed or by design) | Tests |
|---|---|---|---|---|
| `offline/simulator/` | Generate the only "ground truth" this system has | `data/<plant>/*.parquet` (offline, not runtime) | Deterministic given seed+config; a bad config can silently defeat a scenario (ADR-002's S19 base-cycle bug, historical) | 12/12 acceptance checks both plants (per STATE.md; not re-run this pass — see baseline-verification.md) |
| `offline/build_artifact.py` | Bake everything runtime reads into one SQLite file + npz | `artifacts/pulsetwin.db`, `model_<plant>.npz` | Committed once (ADR-027); a rebuild that isn't propagated to all 4-5 docs that quote headline numbers is the recorded failure mode (STATE.md finding #25) | parity < 1e-6 vs sklearn (`test_scorer_parity.py`) |
| `serve/main.py` (lifespan) | Load plant metadata + the champion scorer per plant at startup | In-memory `STATE` dict (module-global, single-process) | **Fixed this pass (ADR-041):** previously ignored `model_registry` entirely; see Phase 3 | `tests/test_model_registry_runtime.py` (new, 5 tests) |
| `serve/db.py` | One query surface over SQLite XOR Postgres | N/A (dispatcher) | WAL + busy_timeout on SQLite (multi-process: backend + spawned mlops/genai children); two connections to one SQLite file in-process can deadlock (documented, historical bug #57) | `test_api.py` runs the whole suite against both backends where practical |
| `serve/scorer.py` | Score one unit, explain the decision path | None (pure function over an in-memory ensemble) | A corrupt/incompatible `.npz` falls back to `serve/fallback_scorer.py`, labelled `FALLBACK MODE` | `test_scorer_parity.py` |
| `serve/middleware/rate_limit.py` | Per-role:ip sliding window | In-memory dict, single-process | **Verified this pass**: correctly returns 429 under sustained load (measured, see performance-benchmark.md); does not share state across replicas (documented limitation, SECURITY.md) | `tests/conftest.py` resets it between tests; the whole suite would false-fail without that reset (STATE.md bug #72) |
| `ingestion/mqtt_subscriber.py` | The ONLY OT/IT bridge process | Writes to Postgres (`sensor_health`, `station_readings` in a live deployment) | Dedup + event-time-watermarked reorder; **verified this pass** against a real Mosquitto broker (see e2e-test-report.md) | `tests/test_ingestion.py` |
| `ingestion/mqtt_publisher.py` | Demo/test-only: replays the artifact's stream over MQTT with fault injection | None | Fault injection (dropout, late, duplicate, corrupt) + a live `control/dropout` topic | exercised live this pass (see e2e-test-report.md); the forced-dropout control message could not be confirmed to visibly transition status inside the test window — see Blocked/Unverified |
| `mlops/model_registry.py` | Champion/challenger CRUD | `model_registry` table | Registry rows are decoupled from what `serve/` loads unless `serve/` asks — **this is exactly the seam fixed this pass** | `tests/test_retraining.py` (real retrain, real data) |
| `mlops/retrainer.py` | Champion-challenger training, spawned detached | Writes registry rows + per-challenger `.npz` (capped at 10, pruned) | `mark_failed()` INSERT-then-UPDATE so a failure before `register()` still leaves a trace (historical bug #39, already fixed) | `test_retraining.py` |
| `genai/verifier.py` | Trust boundary for the one non-deterministic component | None (pure function) | See security-review.md for the one residual gap found this pass (C3 corpus-string scanning) | 25 dedicated tests (`test_genai_verifier.py`) + 12 adversarial tests (`test_prompt_injection.py`) — already thorough |
| Frontend (`frontend/app/*`) | 4 persona views + `/copilot`, SSE-driven | `localStorage` deliberately unused (CLAUDE.md §7 — state is server-derived) | Handles backend-down gracefully (`OfflineBanner`, verified live this pass — see reliability-chaos-report.md) | `npx tsc --noEmit` clean; production build clean (this pass) |

## Scalability bottlenecks and concurrency assumptions (by component)

- **`uvicorn --workers 1`** (ADR-009, required by the async SSE design) is a genuine,
  measured ceiling: single-request-at-a-time CPU-bound work serializes. Measured this pass
  (performance-benchmark.md): `/api/health` p50 goes from ~100ms to ~1.1s between 25 and
  100 concurrent clients on ONE worker. Horizontal scaling (more replicas behind a Service)
  is the documented answer (`docs/PRODUCTION_ARCHITECTURE.md`), not more workers on one
  process — but see the multi-replica promotion gap below, which that scale-out path
  reopens.
- **`STATE["scorers"]` is a plain Python dict, module-global, process-local.** Fine for the
  demo's single replica. In a multi-replica deployment, `POST /api/admin/promote-model`
  only patches the ONE pod that received the request — the others keep serving the old
  model with no error, until each is individually restarted (at which point ADR-041's
  startup lookup catches it up). Named, not fixed — see ADR-041 alternative 2.
- **In-memory rate limiting** does not share state across replicas (SECURITY.md already
  says so). A 5-replica deployment effectively multiplies every role's budget by 5 unless
  moved to Redis (already in the stack for the ingestion feature buffer).
- **Redis** is currently used for ingestion's feature buffer and a documented degraded-read
  cache (ADR-033) — not yet for rate limiting or cross-pod scorer signalling. Both are
  natural, low-risk extensions of infrastructure already present, not new dependencies.
- **Postgres partitioning by `plant_id`** (`genealogy`, `station_readings`) is already
  built for multi-plant scale, confirmed by reading `migrations/001_initial_schema.sql` —
  this is ahead of where a from-scratch audit usually finds a two-plant demo.

## Stale docs / dead code / drift found this pass

| Finding | Evidence |
|---|---|
| `docs/MLOPS_LIFECYCLE.md` claimed "the registry row... is the source of truth" for what a restart serves, with no code making that true. | Traced `serve/main.py::lifespan` — it never queried `model_registry` before this pass. Fixed (ADR-041) and the doc corrected to describe what is now actually implemented. |
| `serve/routers/admin.py::_activate_scorer` returned a hardcoded `"persists_across_restart": False`, discovered stale the moment ADR-041 made it true. | Found by actually promoting a model through the real API and restarting the container (docker), not by reading the code — the field was never asserted on by any test. Fixed. |
| `docs/STATE.md`'s headline "339 tests pass" is stale. | Full suite this pass: 518 passed (before this session's 5 new tests), 523 after. Growth is from the AI Operations Copilot session's new test files, never rolled into the headline count. Recommend deriving this number (`pytest --collect-only -q \| wc -l`) rather than hand-copying it, per `docs/STATE.md`'s own Phase 18 suggestion. |
| `serve/requirements.txt` pinned `fastapi==0.115.0` + `prometheus-fastapi-instrumentator==8.1.0` — mutually un-installable (disjoint starlette ranges). | `docker compose -f docker-compose.prod.yml build` failed with `ResolutionImpossible` — the first time this file has ever been installed from a clean environment (Docker was never buildable before this pass, per STATE.md). Every test run to date used whatever fastapi happened to already be in the ambient interpreter (0.139.0), silently diverged from the pin. Fixed by pinning to the versions actually exercised throughout this project's history. |
| Postgres data volume (`pulsetwinaccenture-pgdata`) predates this session by 5 days. | `docker compose ps` showed the postgres container "created 5 days ago" with migrations 001-004 already applied. Some earlier attempt at the prod stack succeeded further than `docs/STATE.md` records ("Docker CLI is present... but has no daemon behind it" was true only for whichever session wrote that line). Not a defect — noted so the next person doesn't assume a blank slate. |

No dead code or duplicate-source-of-truth findings beyond what `docs/DECISIONS.md` already
tracks (the deliberate `admin.py` / `mlops/model_registry.py` SQL duplication, justified by
the import wall and already tested for drift). Nothing else stood out as unused after
tracing imports across `serve/`, `mlops/`, `genai/`, `ingestion/`.
