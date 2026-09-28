# Baseline Verification

Production-readiness pass, 2026-09-21. Everything below was actually executed on this
machine; no figure here is estimated.

## Environment

| Check | Result |
|---|---|
| Python | 3.13.2 |
| Node | 24.19.0 |
| npm | 11.17.0 |
| Docker | 29.6.2 (client), Docker Desktop 4.83.0 engine 29.6.2 (server) — **daemon confirmed live and usable**, contradicting `docs/STATE.md`'s prior "no daemon behind it" note for this session's environment |
| Disk | 169 GB free of 264 GB on `D:` — not a constraint |
| Ambient Python deps (ran `pip show`) | `fastapi 0.139.0`, `starlette 1.6.0`, `pydantic 2.13.4`, `uvicorn 0.51.0`, `orjson 3.12.0`, `prometheus_client 0.21.0`, `psycopg 3.2.3`, `numpy 2.2.3` — **all newer than `serve/requirements.txt`'s pins**, which is what let the Docker build's dependency conflict go undetected until this pass (see production-readiness-report.md finding P0-1) |

## Backend test suite

Command: `OMP_NUM_THREADS=1 python -m pytest tests/ -q --tb=short`

**Result: 518 passed, 0 failed, 164 warnings, in 1328.35s (22m08s).**

This is the full suite BEFORE this session's fixes were applied as new test files (it was
launched before `tests/test_model_registry_runtime.py` existed, to get an honest
before/after). All warnings are the same pre-existing `FastAPIDeprecationWarning` about
`ORJSONResponse` (cosmetic, not a functional issue) and an `anyio` deprecation from
`starlette.testclient` — neither is new this pass.

After the fixes (`serve/main.py`, `serve/routers/admin.py`, `serve/requirements.txt`) plus
the 5 new tests in `tests/test_model_registry_runtime.py`:

```
tests/test_model_registry_runtime.py::test_startup_bootstraps_a_registry_row_for_the_committed_model PASSED
tests/test_model_registry_runtime.py::test_bootstrap_is_idempotent_across_restarts PASSED
tests/test_model_registry_runtime.py::test_resolve_champion_prefers_a_registry_row_over_the_committed_default PASSED
tests/test_model_registry_runtime.py::test_promotion_via_the_real_api_survives_a_restart PASSED
tests/test_model_registry_runtime.py::test_resolve_champion_falls_back_when_the_registry_artifact_is_missing PASSED
5 passed in 2.35s
```

**Final closing run (Phase 20 acceptance), full suite, after every fix in this pass:**

```
525 passed, 167 warnings in 1029.17s (0:17:09)
```

525 = 518 baseline + 5 new tests in `tests/test_model_registry_runtime.py` + 2 new
parametrized cases in `tests/test_api.py::test_station_stats_returns_one_row_per_station`.
Zero regressions from any fix made in this pass.

## Targeted re-runs after the fix (not the whole suite, but the exposed surface)

| File | Result |
|---|---|
| `tests/test_model_registry_runtime.py` (new) | 5/5 passed |
| `tests/test_genai_tools.py` (docstring change to `get_station_stats`, unrelated prior-session fix, re-verified still passing) | 17/17 passed |

## Frontend

### TypeScript

Command: `npx tsc --noEmit -p .` — **clean, zero errors.**

### Production build

Command: `npm run build` (Next.js 15.1.0, static export)

**Result: clean.** `eslint: { ignoreDuringBuilds: true }` in `next.config.ts` is an
explicit, intentional setting (confirmed by reading the config, not a gap) — "Skipping
linting" in the build output is expected, not a missed step.

```
Route (app)                              Size     First Load JS
┌ ○ /                                    2.81 kB         223 kB
├ ○ /_not-found                          986 B           107 kB
├ ○ /copilot                             3.48 kB         228 kB
├ ○ /leadership                          6.26 kB         320 kB
├ ○ /login                               1.86 kB         222 kB
├ ○ /manager                             12.1 kB         330 kB
├ ○ /ops                                 11.5 kB         330 kB
└ ○ /supervisor                          8.38 kB         233 kB
+ First Load JS shared by all            106 kB
```

All 8 routes prerendered as static content, exported successfully (3/3). No route exceeds
330 kB First Load JS — reasonable for a dense dashboard app, not bloated.

**Caveat, and a gotcha reproduced live:** running `npm run build` while `next dev` was
running clobbered the dev server's `.next` directory exactly as `CLAUDE.md` §8 warns
("`next build` wipes `.next` out from under a running `next dev`"). Confirmed: the running
dev server (PID 20264) started returning HTTP 500 on `/supervisor` immediately after the
build. Recovered per the documented remedy: killed the dev process, `rm -rf .next`,
restarted. This is not a new defect — it is the exact documented gotcha, reproduced as
further confirmation it is accurate and not stale.

## Docker

See `reports/e2e-test-report.md` for the full production-stack run. Summary: build failed
on first attempt (real `pip` dependency conflict, fixed), succeeded on the second, and the
full compose stack (`postgres`, `redis`, `mqtt`, `migrate`, `backend`, `ingestion`) came up
and was exercised end to end — the first time this has happened in this repository's
history (`docs/STATE.md` had this marked "Not verified this session" indefinitely).

## What this baseline does NOT cover

- The full suite was not re-run a second time end to end after every individual fix in
  this pass (only after the first, largest batch) — the closing Phase 20 run covers this.
- No dependency vulnerability scan was run (`pip-audit`, `npm audit`) — `docs/SECURITY.md`
  already names this as unbuilt-for-the-demo; not newly discovered here.
