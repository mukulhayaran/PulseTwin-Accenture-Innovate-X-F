# PulseTwin codebase cleanup report — 2026-09-28

**Verdict: CLEANUP VERIFIED WITH LIMITATIONS.** See §10.

Nothing was committed, pushed, stashed, reset or restored. Every pre-existing user
modification is intact.

## 1. Baseline state

- **Working tree:** 114 status entries. The whole Incident Commander was uncommitted: untracked `serve/commander.py`, `serve/routers/incidents.py`, `serve/routers/operations.py`, the `frontend/components/{decisions,incidents,vehicles}` components, migration 022, and so on. There were also staged deletions of the plant_b files and of `ProposalInbox.tsx`/`TriageConsole.tsx`.
- **Tracked files (index):** 332. Tracked plus untracked, not ignored: 424.
- **pytest `tests/`:** **882 passed, 0 failed** (23m25s, `OMP_NUM_THREADS=1`). There were no pre-existing failures.
- **`tsc --noEmit`:** pass.
- **`npm run build`:** pass (Next 15.1.0).
- **`/api/ready`:** ready. Frontend on :3000 returned 200.
- **`ruff check .`:** 4 errors, all in the user's untracked Commander files:
  - F541 in `serve/ai_operations.py:212`;
  - F401 in `serve/routers/incidents.py`;
  - F401 and F841 in `tests/test_incident_commander.py`.

  These pre-date the cleanup.
- **Docker images:**

  | Image | Size |
  |---|---|
  | backend | 738 MB |
  | migrate | 737 MB |
  | ingestion | 388 MB |
  | load-data | 284 MB |
  | frontend | 268 MB |

## 2. Repository usage map (how "unused" was decided)

- **Python import graph.** An AST scan of every top-level function and class, checked against every other `.py` file and every YAML, TOML, JSON, Makefile and Dockerfile. Only one definition had zero references: `serve/outcomes.py::due`.
- **Modules with no ordinary import.** Every one was resolved individually:
  - routers, via `from serve.routers import (...)`;
  - `scripts/*`, `tools/*`, `offline/benchmarks/*`, all CLI entry points referenced by CI, the Makefile, MANIFEST or docs.
- **Routes.** All 95 routes were matched against the frontend, `genai/`, `scripts/`, `offline/` and `ingestion/`. Three have no product caller; see §5. The proposal routes had callers only in `genai/tools.py` and tests.
- **Runtime routing traced.**
  - `serve/live_simulation.py::_on_firing` sends **every** watch firing to `_open_commander_incident`. Its own docstring says the ADR-051 path was retired.
  - `grep INSERT INTO action_proposals` returns nothing: no code path creates a proposal.
  - `/api/watch/firings` reads `action_proposals`, so it is always empty.
  - `start_counterfactual` (the only spawner of `python -m offline.counterfactual`) had no caller outside `proposals.py`.
  - The watch intents open with `get_watch_firings`, so their deterministic plans always ended "no firing".
  - No UI, auto-investigation (`incident`) or Commander path starts them.
- **Paths confirmed active.**
  - Incident Commander, including the optional LLM planner (`genai/commander.py`) and its deterministic fallback.
  - Watch detectors, outcome monitoring, case memory, and the Decision Queue and History.
  - Supervisor auto-investigation (`incident` intent), backend and MQTT-followed runs, and the Ops agent controls.
- **Kafka.** No code. Only prose in two docs.
- **Frontend.** Import scan over `app/`, `components/` and `lib/`, then `tsc` as the authority. `TwinForecast.tsx` had no importer.

## 3. Removed

| Removed | Evidence | Lines |
|---|---|---|
| `serve/routers/proposals.py`: `GET /api/watch/firings`, `GET /api/proposals`, `POST /api/proposals/{id}/approve\|reject`, and the forecast-job spawner | No writer of `action_proposals`; no frontend fetch function | −404 (file) |
| Registration in `serve/main.py` | Follows from the row above | −2 |
| `serve/outcomes.py::due` | Zero references anywhere | −8 |
| `offline/counterfactual.py`: `forecast`, `_run_arm`, `_prepare`, `_arm_worker`, `_ci`, `simulated_as`, `_write`, `main` and the CLI | Only spawned by the removed router. `hypothesis`, `_reseed`, `_mitigate` and `MAX_CONGESTION` are kept for `sim_experiment` and `agent_eval` | −244 |
| `genai/intents.py`: `STARVATION`/`PAINT_ENV`/`TOOL_WEAR` intents and `EXPERIMENT_GUIDANCE`/`ACTION_GUIDANCE` (used only by them) | Unreachable, per §2 | −95 |
| `genai/offline_triage.py`: `run_starvation`, `run_paint_env`, `run_tool_wear`, `_watch_firing`, `_line_context`, `_evidence_claim`, `_refused`, `_finish` | Fixed-point analysis: each was called only by the watch plans; no external importer | −444 |
| `genai/tools.py`: `get_watch_firings` and `get_proposals` tools (27 → 25) and the ADR-051 egress note | Their endpoints are gone. `PHASE_ORDER` is unchanged, since every phase is still used | −38 |
| Mirrors: `serve/routers/triage.py` `INTENT_LABELS`/`INTENT_PATTERN`, and `frontend/lib/api.ts::TriageIntent` | The parity tests require all mirrors to agree | −6 |
| `frontend/components/ops/TwinForecast.tsx` | No importer | −(file) |
| `frontend/lib/api.ts`: `listTriageRuns`, `WatchDetector`, `ProposalStatus`, `ActionProposal`, `Interval`, `ForecastArm`, `Counterfactual`, `ProposedAction.simulated_impact` | No user after the component deletion; confirmed by `tsc` | −~90 |
| Tests for removed behaviour: router, firings and forecast-job tests in `test_proposals.py`; 3 `forecast` tests in `test_counterfactual.py`; `/api/proposals` assertions in `test_outcome_learning.py` | The behaviour is intentionally gone. No test of live behaviour was removed | −290 |

**Kept in the trimmed test files:**
- MITIGATIONS↔ACTION_TYPES parity;
- the verifier dropping an action with unverified evidence;
- twin mitigation physics;
- hypothesis mapping;
- the outcome-learning flow.

**Totals, measured on tracked plus untracked source:**
- Python: 48,517 → 46,990 (**−1,527**). Product code −1,237, tests −290.
- TS/TSX: 13,253 → 13,034 (**−219**, excluding the 3-line `neon.ts` another agent added mid-run).
- Overall: about **−1,746 source lines** and **2 source files** removed.

## 4. Consolidations and small fixes

- **Handover plan.** The deterministic handover (`_operations_memory`) previously took outcome counts and "observing / harmful" watch items from `get_proposals`, which was always empty. It now reads the same facts from `get_action_outcomes` (`/data/accuracy/by_status/*`). Those records exist for Commander actions. This removes a dependency on a dead endpoint and makes the handover reflect real outcomes.
- **Handover agent prompt.** No longer tells the model to read `get_proposals`.
- **The 4 pre-existing ruff errors (§1) fixed.** Unused `Query` import, unused `SimpleNamespace`, unused `st` binding (the call itself is kept), and a placeholder-less f-string. None changes behaviour. `ruff check .` is now clean.
- **Stale comments corrected:**
  - `serve/Dockerfile`: `offline/` is still needed, now for `sim_experiment`;
  - `genai/finding.py`: the mirror now points at `MITIGATIONS`;
  - `AgentPanel.tsx`;
  - `serve/routers/triage.py`.
- **Docs.** `docs/SECURITY.md` (RBAC row, audit event, "proposing is not doing") now names the Commander's `POST /api/recommendations/{id}/approve|reject`. CLAUDE.md's subsystem map and endpoint count are updated. **ADR-073** was added to `docs/DECISIONS.md`.
- **`.gitignore`.** `tmp/` added. It holds 18 MB of local PDFs and pytest temp dirs, which were untracked but not ignored.

## 5. Considered but retained

| Item | Reason |
|---|---|
| `action_proposals` table and migrations | Existing Postgres databases hold rows; dropping them needs a migration decision |
| `serve/outcomes.py::record_decision`, `/api/learning/similar`, `get_similar_incidents` | Still read by live code and tests. **Gap found:** since ADR-069, no runtime path writes `incident_learning_records` (see §8) |
| `live_simulation` `applied`/EFFECTIVE bookkeeping | Still driven by `commander → apply_mitigation` |
| `/api/maintenance/windows`, `/api/ai/operations/runs`, `/api/sensor-maturity` | No UI caller, but part of the new Commander API and documented in `docs/ENTERPRISE_INTEGRATION.md`; one has a test. **Manual review** |
| `offline/experiments/p75_prototype.py` | Standalone research CLI. `config_schema.py` carries a hook for it, so removing it means a schema change |
| `components/ui/index.ts` | **Wrongly flagged at first.** It is the `@/components/ui` barrel, imported about 40 times. My first scan missed the directory-index import; `tsc` caught it and it was restored byte-identical from HEAD |
| Watch detectors (`serve/watch.py`), `WATCH_DETECTORS` in experiments | Live: they feed the Commander and the experiment incident context |
| `scripts/*`, `tools/*`, `offline/benchmarks/*`, artifacts | CLI entry points and runtime or MANIFEST inputs |
| `landing-full.png` (repo root, untracked), `tmp/` contents | Not provably mine to delete; now ignored. **Manual review** |

## 6. Before / after

| | Before | After |
|---|---|---|
| Tracked files (index) | 332 | 332. Nothing staged; the deletions are unstaged working-tree changes |
| Source files, tracked plus untracked | — | −2 (`proposals.py`, `TwinForecast.tsx`) |
| Python lines | 48,517 | 46,990 |
| TS/TSX lines | 13,253 | 13,034 (plus 3 from the other agent's `neon.ts`) |
| `serve/` / `genai/` / `offline/` size | 833 / 337 / 563 KB | 813 / 309 / 551 KB |
| First Load JS, /supervisor · /manager · shared | 262 · 347 · 106 kB | 262 · 348 · 102 kB |
| Backend image | 738 MB | 738 MB |

The bundle is effectively unchanged, as expected: the frontend removals were types and an unimported component, which tree-shaking already dropped. The "after" bundle was also built on **Next 15.5.26** (see §9), so the two sets of numbers are not directly comparable.

The backend image size is unchanged because it is dominated by Python dependencies.

## 7. Tests and smoke checks

| Check | Result |
|---|---|
| Batch A focused: `test_proposals`, `test_counterfactual`, `test_outcome_learning`, `test_serve_imports`, `test_warm_cache`, `test_sim_experiment`, `test_agent_eval`, `test_api` | **172 passed** |
| Batch B focused: `test_genai_tools`, `test_genai_offline`, `test_triage_api`, `test_genai_loop`, `test_genai_orchestrator`, `test_prompt_injection`, `test_proposals`, `test_serve_imports`, `test_incident_commander` | **288 passed** |
| `ruff check .` | pass |
| `tsc --noEmit` | pass |
| `npm run build` | pass |
| Backend image rebuilt and container recreated → `/api/ready` | ready; `genai: agent mode -- provider key present`; `proposals.py` absent in the container |
| Backend logs | no Traceback, ImportError or missing file |
| Frontend :3000 `/supervisor` | 200 |
| `scripts/verify_demo_flow.py --replay` (demo mode on for the check, then restored to 0) | **Run 1: 31/31 PASS**. Replay: first 6 checks PASS, then the run was stopped (see §9) |
| Full `pytest tests/` after cleanup | **Not completed.** Stopped by Claude Code for low system memory after about 35 tests, 0 failures (see §9) |
| Reference sweep for every deleted path and symbol (serve, genai, offline, tests, scripts, tools, frontend, `.github`, configs, Makefile, compose, render) | clean |

**What run 1 of `verify_demo_flow.py` covered:**
- fresh start;
- vehicles moving;
- healthy state before the scenario;
- the bottleneck scenario auto-detected by the watch, with the Commander opening an incident;
- the investigation completing with dynamic specialists;
- competing hypotheses;
- a twin experiment, informative and improved;
- the "explain" narrative;
- a decision queued at L3 with the Governor requiring a manager;
- the queue scoped by role;
- the evidence drawer;
- **the supervisor getting 403 on approve** and the manager approving;
- the action gateway executing idempotently, on the simulated twin or CMMS stub only;
- the audit trail;
- the queue clearing into history;
- recovery observed, not instant;
- predicted vs actual recorded;
- the incident resolved with an outcome;
- the case written to memory.

## 8. Remaining cleanup opportunities

1. **Learning-record writer gap.** `incident_learning_records` has no runtime writer since the proposal path went. Either call `outcomes.record_decision`-style logic from `serve/routers/incidents.py` approve/reject, or retire `/api/learning/similar` and the `get_similar_incidents` tool. This is a product decision.
2. **`action_proposals` table.** Once old rows are no longer wanted, drop it with a new migration. Also drop the `origin_proposal_id` query filter on `/api/simulation-experiments` if nothing sets it.
3. **Three uncalled Commander endpoints** (§5): keep them as an integration API or delete them.
4. **`offline/experiments/p75_prototype.py`** and its `config_schema` hook: archive it if the experiment is finished. Its docstring still says "both plants".
5. **Stale doc prose** mentioning the proposal inbox remains in historical ADRs, STATE.md and the demo scripts. It was deliberately left alone, since ADRs are history.

## 9. Risks and limitations

- **Full suite not re-run to completion.** Claude Code stopped the background run for low system memory. Coverage of the changed code comes from the two focused batches (460 test executions, 0 failures), which include every test file touching a modified module. **Please re-run `pytest tests/` when memory allows.**
- **Demo-flow replay not completed**, for the same memory reason. Run 1 was complete and all green.
- **A concurrent agent was changing the repo during this cleanup** (processes owned by `CodexSandboxOffline`, plus npm logs at 07:49 UTC). I did not touch any of its changes:
  - `npm install next@15.5.26` in `frontend/`: `frontend/package.json` changed, the lockfile was regenerated, and a leftover npm temp dir `node_modules/.next-PXigGWhG` remains;
  - new root files `package.json`, `package-lock.json`, `neon.ts`, `skills-lock.json`.

  Consequences:
  - the post-cleanup `tsc` and build ran on Next 15.5.26;
  - `next build` now warns about two lockfiles (root and frontend);
  - **the frontend Docker image was deliberately NOT rebuilt**, so the other agent's in-flight upgrade would not be shipped into the running stack. My frontend changes do not alter the runtime bundle, so the running frontend image is not stale with respect to this cleanup.
- **Index handling.** A `git rm --cached` I ran early by mistake was reversed at once by re-adding HEAD's blob. The file had been unmodified, so the index is byte-identical to before.
- **API removal.** `POST /api/triage/runs` with `intent` = `starvation`, `paint_env` or `tool_wear` now returns 422, and the four proposal and firing routes return 404. External scripts using them would break. None exist in the repo.

## 10. Verdict

**CLEANUP VERIFIED WITH LIMITATIONS.** Everything that ran passed:
- focused tests, lint, typecheck and production build;
- backend rebuild and readiness, with LLM agent mode on;
- the full Commander flow, including RBAC and Governor.

No deleted module is referenced by Docker, CI, a subprocess or config, and no secrets were added. The limitations are the full-suite re-run and the demo replay, both stopped by the memory reaper, and the concurrent agent's `next` upgrade.
